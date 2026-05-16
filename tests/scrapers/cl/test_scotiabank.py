"""Tests for the Scotiabank Chile scraper."""

from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from fintself.core.exceptions import LoginError
from fintself.scrapers.cl.scotiabank import ScotiabankScraper


@pytest.fixture
def scraper() -> ScotiabankScraper:
    s = ScotiabankScraper.__new__(ScotiabankScraper)
    s.debug_mode = False
    s.default_timeout = 30000
    s.min_human_delay_ms = 0
    s.max_human_delay_ms = 0
    s.user = "1.234.567-8"
    s.password = "secret"
    s.playwright = None
    s.browser = None
    s.page = MagicMock()
    return s


class TestBankId:
    def test_returns_cl_scotiabank(self, scraper):
        assert scraper._get_bank_id() == "cl_scotiabank"


class TestLogin:
    def test_login_success_flow(self, scraper):
        page = scraper.page

        acceso_locator = MagicMock()
        acceso_locator.first.click = MagicMock()
        personas_locator = MagicMock()
        personas_locator.click = MagicMock()

        page.get_by_text = MagicMock(return_value=acceso_locator)
        page.get_by_role = MagicMock(return_value=personas_locator)
        page.wait_for_selector = MagicMock()

        with (
            patch.object(scraper, "_ensure_page", return_value=page),
            patch.object(scraper, "_navigate") as nav,
            patch.object(scraper, "_save_debug_info"),
            patch.object(scraper, "_type") as type_mock,
            patch.object(scraper, "_click") as click_mock,
            patch.object(scraper, "_dismiss_onboarding_tour") as tour_mock,
            patch("fintself.scrapers.cl.scotiabank.expect") as mock_expect,
        ):
            mock_expect.return_value.to_have_url = MagicMock()
            scraper._login()

        nav.assert_called_once_with(scraper.HOME_URL, timeout_override=60000)
        page.get_by_text.assert_called_with("Acceso Scotia")
        acceso_locator.first.click.assert_called_once()
        page.get_by_role.assert_called_with("link", name="Ingreso Personas")
        personas_locator.click.assert_called_once()
        page.wait_for_selector.assert_called_with(scraper.RUT_INPUT, timeout=30000)

        type_mock.assert_any_call(scraper.RUT_INPUT, scraper.user, delay=120)
        type_mock.assert_any_call(scraper.PASSWORD_INPUT, scraper.password, delay=120)
        type_call_order = [c.args[0] for c in type_mock.call_args_list]
        assert type_call_order == [scraper.RUT_INPUT, scraper.PASSWORD_INPUT], (
            "RUT must be typed before password"
        )

        click_mock.assert_called_once_with(scraper.SUBMIT_BUTTON)

        mock_expect.assert_called_once_with(page)
        to_have_url_call = mock_expect.return_value.to_have_url.call_args
        url_predicate = to_have_url_call.args[0]
        assert url_predicate(
            "https://www.scotiabank.cl/mfe/sweb/mfe-shell-web-cl/mfe/mfe/sweb/mfe-home-cl/"
        )
        assert not url_predicate("https://banco.scotiabank.cl/mfe-login/scotia")
        assert to_have_url_call.kwargs.get("timeout") == 45000

        tour_mock.assert_called_once_with(page)

    def test_login_raises_when_acceso_scotia_missing(self, scraper):
        page = scraper.page
        acceso_locator = MagicMock()
        acceso_locator.first.click.side_effect = PlaywrightTimeoutError("nope")
        page.get_by_text = MagicMock(return_value=acceso_locator)

        with (
            patch.object(scraper, "_ensure_page", return_value=page),
            patch.object(scraper, "_navigate"),
            patch.object(scraper, "_save_debug_info"),
        ):
            with pytest.raises(LoginError, match="Acceso Scotia"):
                scraper._login()

    def test_login_raises_when_login_form_never_renders(self, scraper):
        page = scraper.page
        acceso_locator = MagicMock()
        page.get_by_text = MagicMock(return_value=acceso_locator)
        personas_locator = MagicMock()
        page.get_by_role = MagicMock(return_value=personas_locator)
        page.wait_for_selector.side_effect = PlaywrightTimeoutError("no form")

        with (
            patch.object(scraper, "_ensure_page", return_value=page),
            patch.object(scraper, "_navigate"),
            patch.object(scraper, "_save_debug_info"),
        ):
            with pytest.raises(LoginError, match="Login form did not render"):
                scraper._login()

    def test_login_raises_when_dashboard_url_never_arrives(self, scraper):
        page = scraper.page
        acceso_locator = MagicMock()
        personas_locator = MagicMock()
        page.get_by_text = MagicMock(return_value=acceso_locator)
        page.get_by_role = MagicMock(return_value=personas_locator)
        page.wait_for_selector = MagicMock()

        with (
            patch.object(scraper, "_ensure_page", return_value=page),
            patch.object(scraper, "_navigate"),
            patch.object(scraper, "_save_debug_info"),
            patch.object(scraper, "_type"),
            patch.object(scraper, "_click"),
            patch.object(scraper, "_dismiss_onboarding_tour"),
            patch("fintself.scrapers.cl.scotiabank.expect") as mock_expect,
        ):
            mock_expect.return_value.to_have_url.side_effect = AssertionError(
                "url mismatch"
            )
            with pytest.raises(LoginError, match="Credentials might be incorrect"):
                scraper._login()


class TestDismissOnboardingTour:
    def test_dismiss_clicks_visible_button_and_exits(self, scraper):
        scraper.POST_LOGIN_SETTLE_MS = 4000
        scraper.TOUR_POLL_INTERVAL_MS = 500

        visible_loc = MagicMock()
        visible_loc.is_visible.return_value = True
        invisible_loc = MagicMock()
        invisible_loc.is_visible.return_value = False

        def fake_locator(sel):
            wrapper = MagicMock()
            wrapper.first = (
                visible_loc if sel == "button:has-text('Saltar')" else invisible_loc
            )
            return wrapper

        page = MagicMock()
        page.locator.side_effect = fake_locator

        scraper._dismiss_onboarding_tour(page)

        visible_loc.click.assert_called_once_with(timeout=2000)

    def test_dismiss_never_matches_logout_button(self, scraper):
        # 'Cerrar sesión' must NOT appear in any tour selector.
        for sel in scraper.TOUR_DISMISS_SELECTORS:
            assert "Cerrar sesión" not in sel
            assert sel != "button:has-text('Cerrar')"
            assert sel != "button[aria-label='Cerrar']"  # scoped only

    def test_dismiss_silent_on_no_overlay(self, scraper):
        scraper.POST_LOGIN_SETTLE_MS = 1000
        scraper.TOUR_POLL_INTERVAL_MS = 500
        invisible_loc = MagicMock()
        invisible_loc.is_visible.return_value = False

        page = MagicMock()
        page.locator.return_value.first = invisible_loc

        scraper._dismiss_onboarding_tour(page)  # must not raise

        invisible_loc.click.assert_not_called()


class TestLoginPageFixture:
    """Sanity check the captured login_page.html still contains expected selectors."""

    def test_fixture_contains_rut_and_password_testids(self):
        from pathlib import Path

        fixture = (
            Path(__file__).parent.parent.parent
            / "fixtures"
            / "cl"
            / "scotiabank"
            / "login_page.html"
        )
        assert fixture.exists(), f"Missing fixture: {fixture}"
        html = fixture.read_text(encoding="utf-8")
        assert 'data-testid="inputDni"' in html
        assert 'data-testid="inputPassword"' in html
        assert "Ingresar" in html


class TestScrapeMovementsStubs:
    """Movement scrapers are stubs until recon is complete."""

    def test_scrape_movements_returns_empty_list(self, scraper):
        assert scraper._scrape_movements() == []


class TestFixtureStructure:
    """Sanity checks on captured DOM fixtures used by upcoming TDD."""

    @staticmethod
    def _fixture(name: str) -> str:
        from pathlib import Path

        return (
            Path(__file__).parent.parent.parent
            / "fixtures"
            / "cl"
            / "scotiabank"
            / name
        ).read_text(encoding="utf-8")

    def test_checking_fixture_has_movement_table(self):
        html = self._fixture("checking_movements.html")
        assert 'class="Table__dataTable"' in html
        assert "TableBody__cell" in html
        assert "Descripción" in html

    def test_cc_billed_fixture_has_tab_and_table(self):
        html = self._fixture("credit_card_billed.html")
        assert "tab-action__movimientos-facturados" in html
        assert "tabla__movimientos--nacional" in html
        assert "tabla__movimientos--internacional" in html

    def test_cc_unbilled_fixture_has_unbilled_tab(self):
        html = self._fixture("credit_card_unbilled.html")
        assert "tab-action__movimientos-no-facturados" in html
        assert "tabla__movimientos--nacional" in html

    def test_fixtures_have_no_real_rut(self):
        import re

        for name in [
            "checking_movements.html",
            "credit_card_billed.html",
            "credit_card_unbilled.html",
            "login_page.html",
        ]:
            html = self._fixture(name)
            assert not re.search(r"\b\d{1,2}\.\d{3}\.\d{3}-[\dkK]\b", html), name
            assert not re.search(r"\b\d{7,8}-[\dkK]\b", html), name
            assert "Picoteo" not in html, name
