from typing import List

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect

from fintself.core.exceptions import LoginError
from fintself.core.models import MovementModel
from fintself.scrapers.base import BaseScraper
from fintself.utils.logging import logger


class ScotiabankScraper(BaseScraper):
    """Scraper to extract movements from Scotiabank Chile (personas)."""

    HOME_URL = "https://www.scotiabankchile.cl/"
    LOGIN_URL_PREFIX = "https://banco.scotiabank.cl/mfe-login/scotia"
    DASHBOARD_URL_FRAGMENT = "/mfe-home-cl/"

    PUBLIC_LOGIN_TEXT = "Acceso Scotia"
    PUBLIC_LOGIN_PERSONAS_NAME = "Ingreso Personas"

    RUT_INPUT = '[data-testid="inputDni"]'
    PASSWORD_INPUT = '[data-testid="inputPassword"]'
    SUBMIT_BUTTON = 'role=button[name="Ingresar"]'

    STAGE_IFRAME_ID = "iframe-stage"
    STAGE_IFRAME_SELECTOR = "#iframe-stage"

    TAB_BILLED_ID = "tab-action__movimientos-facturados"
    TAB_UNBILLED_ID = "tab-action__movimientos-no-facturados"
    TAB_BALANCE_ID = "tab-action__saldo"
    TAB_BILLED_SELECTOR = "button#tab-action__movimientos-facturados"
    TAB_UNBILLED_SELECTOR = "button#tab-action__movimientos-no-facturados"
    ACTIVE_TAB_SELECTOR = 'button[id^="tab-action__"].tab__action--active'

    RADIO_NACIONAL = 'label.label--radio:has-text("Nacionales")'
    RADIO_INTERNAC = 'label.label--radio:has-text("Internacionales")'

    CHECKING_IFRAME_URL_FRAGMENT = "mfe-accounts-balancesmovements-web"
    CC_IFRAME_URL_FRAGMENT = "mfe-simple-account-statement-web-cl"

    CHECKING_TABLE = "table.Table__dataTable"
    CHECKING_HEAD = "th.TableHead__headColumn"
    CHECKING_ROW = "tbody tr"
    CHECKING_CELL = "td.TableBody__cell"

    CC_TABLE_NAC = "div.tabla__movimientos--nacional table.table"
    CC_TABLE_INT = "div.tabla__movimientos--internacional table.table"
    CC_HEAD = "thead.table__header tr.table__row th.table__header-item"
    CC_ROW = "tbody tr.table__row"
    CC_CELL = "td.table__data span"

    POST_LOGIN_SETTLE_MS = 8000
    TOUR_POLL_INTERVAL_MS = 500
    TOUR_PROBE_TIMEOUT_MS = 600

    TOUR_DISMISS_SELECTORS = [
        "button.driver-popover-close-btn",
        ".driver-popover-close-btn",
        "button:has-text('Saltar')",
        "button:has-text('Omitir')",
        "button:has-text('Entendido')",
        "button:has-text('Finalizar tour')",
        "button:has-text('Finalizar recorrido')",
        "button:has-text('Listo')",
        "[role='dialog'] button[aria-label='Cerrar']",
        ".modal button[aria-label='Cerrar']",
    ]

    def _get_bank_id(self) -> str:
        return "cl_scotiabank"

    def _login(self) -> None:
        """Login to Scotiabank Chile personas portal."""
        assert self.user is not None, "User must be provided"
        assert self.password is not None, "Password must be provided"

        page = self._ensure_page()
        logger.info("Navigating to Scotiabank Chile home page.")
        self._navigate(self.HOME_URL, timeout_override=60000)
        self._save_debug_info("01_home_page")

        logger.info("Opening 'Acceso Scotia' menu.")
        try:
            page.get_by_text(self.PUBLIC_LOGIN_TEXT).first.click(timeout=15000)
        except PlaywrightTimeoutError:
            self._save_debug_info("acceso_scotia_not_found")
            raise LoginError("Could not find 'Acceso Scotia' on home page.")

        logger.info("Clicking 'Ingreso Personas' link.")
        try:
            page.get_by_role("link", name=self.PUBLIC_LOGIN_PERSONAS_NAME).click(
                timeout=15000
            )
        except PlaywrightTimeoutError:
            self._save_debug_info("ingreso_personas_not_found")
            raise LoginError("Could not find 'Ingreso Personas' link.")

        logger.info("Waiting for login form to render.")
        try:
            page.wait_for_selector(self.RUT_INPUT, timeout=30000)
        except PlaywrightTimeoutError:
            self._save_debug_info("login_form_timeout")
            raise LoginError("Login form did not render in time.")
        self._save_debug_info("02_login_form")

        logger.info("Entering credentials.")
        self._type(self.RUT_INPUT, self.user, delay=120)
        self._type(self.PASSWORD_INPUT, self.password, delay=120)
        self._save_debug_info("03_credentials_entered")

        logger.info("Submitting login form.")
        self._click(self.SUBMIT_BUTTON)

        logger.info("Waiting for post-login redirect.")
        try:
            expect(page).to_have_url(
                lambda url: self.DASHBOARD_URL_FRAGMENT in url, timeout=45000
            )
            self._save_debug_info("04_login_success")
            logger.info("Login to Scotiabank Chile successful.")
        except (PlaywrightTimeoutError, AssertionError):
            self._save_debug_info("post_login_error")
            raise LoginError(
                "Timeout or error after login to Scotiabank Chile. "
                "Credentials might be incorrect."
            )

        self._dismiss_onboarding_tour(page)

    def _dismiss_onboarding_tour(self, page: Page, context: str = "post_login") -> None:
        """Poll for and close the onboarding tour overlay.

        Tour may mount asynchronously after the dashboard URL settles. Poll
        every ``TOUR_POLL_INTERVAL_MS`` until ``POST_LOGIN_SETTLE_MS`` elapses
        or a known close button becomes actionable. Never matches 'Cerrar'
        alone (that is the logout button text).

        ``context`` labels the call site in logs + debug artifacts so the same
        helper can be invoked at multiple checkpoints (post-login, before
        each tab switch) without losing forensic detail.
        """
        elapsed = 0
        while elapsed < self.POST_LOGIN_SETTLE_MS:
            for sel in self.TOUR_DISMISS_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=self.TOUR_PROBE_TIMEOUT_MS):
                        logger.info(f"[{context}] dismissing tour overlay: {sel}")
                        loc.click(timeout=2000)
                        page.wait_for_timeout(300)
                        self._save_debug_info(f"tour_dismissed_{context}")
                        return
                except Exception:
                    pass
            page.wait_for_timeout(self.TOUR_POLL_INTERVAL_MS)
            elapsed += self.TOUR_POLL_INTERVAL_MS

    def _scrape_checking(self) -> List[MovementModel]:
        """Extract checking account movements.

        Stub: implementation pending. Will use ``STAGE_IFRAME_SELECTOR`` +
        ``CHECKING_TABLE`` selectors documented above.
        """
        logger.warning("ScotiabankScraper._scrape_checking not implemented yet.")
        return []

    def _scrape_credit_card_billed(self) -> List[MovementModel]:
        """Extract billed credit card movements.

        Stub: implementation pending. Will click ``TAB_BILLED_SELECTOR`` then
        iterate ``CC_TABLE_NAC`` / ``CC_TABLE_INT``.
        """
        logger.warning(
            "ScotiabankScraper._scrape_credit_card_billed not implemented yet."
        )
        return []

    def _scrape_credit_card_unbilled(self) -> List[MovementModel]:
        """Extract unbilled credit card movements.

        Stub: implementation pending. Will click ``TAB_UNBILLED_SELECTOR`` then
        iterate ``CC_TABLE_NAC`` / ``CC_TABLE_INT``.
        """
        logger.warning(
            "ScotiabankScraper._scrape_credit_card_unbilled not implemented yet."
        )
        return []

    def _scrape_movements(self) -> List[MovementModel]:
        """Orchestrates extraction of checking + credit card movements."""
        logger.info("Starting Scotiabank Chile movement extraction.")
        all_movements: List[MovementModel] = []
        all_movements.extend(self._scrape_checking())
        all_movements.extend(self._scrape_credit_card_billed())
        all_movements.extend(self._scrape_credit_card_unbilled())
        logger.info(
            f"Scotiabank Chile extraction finished. Total: {len(all_movements)} "
            f"movements."
        )
        return all_movements
