from __future__ import annotations

import re
from decimal import Decimal
from typing import List, Optional, Union

from playwright.sync_api import Frame, FrameLocator, Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect

from fintself.core.exceptions import DataExtractionError, LoginError
from fintself.core.models import MovementModel
from fintself.scrapers.base import BaseScraper
from fintself.utils.logging import logger
from fintself.utils.parsers import parse_chilean_amount, parse_chilean_date


LocatorRoot = Union[Page, Frame, FrameLocator, Locator]


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
    CHECKING_ROW = "tbody.TableBody tr.TableBody__bodyRow"
    CHECKING_CELL = "td.TableBody__cell"

    CC_TABLE_NAC = "div.tabla__movimientos--nacional table.table"
    CC_TABLE_INT = "div.tabla__movimientos--internacional table.table"
    CC_ROW = "tbody tr.table__row"
    CC_CELL = "td.table__data"

    CC_CARD_LABEL_SELECTOR = "label.label__tarjetas-credito"
    CC_CARD_VALUE_SELECTOR = (
        "label.label__tarjetas-credito + "
        "div span, label.label__tarjetas-credito ~ div span"
    )

    POST_LOGIN_SETTLE_MS = 8000
    TOUR_POLL_INTERVAL_MS = 500
    TOUR_PROBE_TIMEOUT_MS = 600

    IFRAME_WAIT_MS = 30000
    TABLE_WAIT_MS = 20000

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

    # ─── Login ────────────────────────────────────────────────────────────

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

        Never matches 'Cerrar' alone (that is the logout button text).
        ``context`` labels logs + debug artifacts so callers (post_login,
        pre_cc_billed, ...) get distinguishable forensics.
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

    # ─── Iframe routing ───────────────────────────────────────────────────

    def _get_stage_frame(self, url_fragment: str) -> Frame:
        """Return the iframe-stage ``Frame`` whose URL contains the fragment.

        Waits up to ``IFRAME_WAIT_MS`` for the frame to attach with the right
        URL, then returns it. Raises ``DataExtractionError`` on timeout.
        """
        page = self._ensure_page()
        deadline_ms = self.IFRAME_WAIT_MS
        elapsed = 0
        interval = 500
        while elapsed < deadline_ms:
            for f in page.frames:
                if url_fragment in (f.url or ""):
                    return f
            page.wait_for_timeout(interval)
            elapsed += interval
        self._save_debug_info(f"iframe_missing_{url_fragment}")
        raise DataExtractionError(
            f"Could not locate iframe-stage frame with url fragment '{url_fragment}'."
        )

    # ─── Checking ─────────────────────────────────────────────────────────

    def _scrape_checking(self) -> List[MovementModel]:
        """Navigate to checking account view and extract movements."""
        page = self._ensure_page()
        logger.info("Navigating to checking account ('Ver cartola').")
        try:
            page.get_by_text("Ver cartola").first.click(timeout=15000)
        except PlaywrightTimeoutError:
            self._save_debug_info("ver_cartola_not_found")
            raise DataExtractionError(
                "Dashboard 'Ver cartola' link not found; cannot reach checking view."
            )
        self._dismiss_onboarding_tour(page, context="pre_checking")
        self._save_debug_info("checking_01_navigated")

        frame = self._get_stage_frame(self.CHECKING_IFRAME_URL_FRAGMENT)
        try:
            frame.wait_for_selector(self.CHECKING_TABLE, timeout=self.TABLE_WAIT_MS)
        except PlaywrightTimeoutError:
            self._save_debug_info("checking_table_missing")
            raise DataExtractionError("Checking movement table did not render.")
        self._save_debug_info("checking_02_table_ready")

        movements = self._extract_checking_movements(frame)
        logger.info(f"Extracted {len(movements)} checking movements.")
        return movements

    def _extract_checking_movements(
        self, root: LocatorRoot, account_id: Optional[str] = None
    ) -> List[MovementModel]:
        """Parse the 'Saldos y últimos movimientos' table inside ``root``."""
        rows = root.locator(self.CHECKING_ROW).all()
        logger.debug(f"Checking rows located: {len(rows)}")

        movements: List[MovementModel] = []
        for idx, row in enumerate(rows):
            try:
                cells = row.locator(self.CHECKING_CELL).all()
                if len(cells) < 6:
                    logger.debug(f"Skipping row {idx}: only {len(cells)} cells.")
                    continue

                date_str = cells[1].inner_text(timeout=5000).strip()
                description = cells[2].inner_text(timeout=5000).strip()
                city = cells[3].inner_text(timeout=5000).strip()
                amount_str = cells[4].inner_text(timeout=5000).strip()
                saldo_str = cells[5].inner_text(timeout=5000).strip()

                parsed_date = parse_chilean_date(date_str)
                if parsed_date is None:
                    logger.warning(f"Row {idx}: unparseable date {date_str!r}.")
                    continue

                amount = parse_chilean_amount(amount_str)
                if amount.is_zero():
                    continue

                movements.append(
                    MovementModel(
                        date=parsed_date,
                        description=description,
                        amount=amount,
                        currency="CLP",
                        transaction_type=("Abono" if amount > 0 else "Cargo"),
                        account_id=account_id,
                        account_type="corriente",
                        raw_data={
                            "date_str": date_str,
                            "amount_str": amount_str,
                            "city": city,
                            "saldo_str": saldo_str,
                        },
                    )
                )
            except Exception as exc:
                logger.warning(f"Failed to parse checking row {idx}: {exc}")
                continue
        return movements

    # ─── Credit card ──────────────────────────────────────────────────────

    def _scrape_credit_card_billed(self) -> List[MovementModel]:
        return self._scrape_cc_tab(
            tab_selector=self.TAB_BILLED_SELECTOR,
            transaction_type="Facturado",
            context="cc_billed",
        )

    def _scrape_credit_card_unbilled(self) -> List[MovementModel]:
        return self._scrape_cc_tab(
            tab_selector=self.TAB_UNBILLED_SELECTOR,
            transaction_type="NoFacturado",
            context="cc_unbilled",
        )

    def _scrape_cc_tab(
        self, *, tab_selector: str, transaction_type: str, context: str
    ) -> List[MovementModel]:
        """Navigate to the credit-card MFE, click the requested tab, extract."""
        page = self._ensure_page()
        logger.info(f"[{context}] navigating to Tarjetas section.")
        try:
            page.get_by_text("Tarjetas").first.click(timeout=15000)
        except PlaywrightTimeoutError:
            self._save_debug_info(f"{context}_tarjetas_not_found")
            raise DataExtractionError(
                f"[{context}] dashboard 'Tarjetas' menu not found."
            )
        self._dismiss_onboarding_tour(page, context=f"pre_{context}")
        self._save_debug_info(f"{context}_01_navigated")

        frame = self._get_stage_frame(self.CC_IFRAME_URL_FRAGMENT)

        logger.info(f"[{context}] clicking tab {tab_selector}.")
        try:
            frame.click(tab_selector, timeout=self.TABLE_WAIT_MS)
        except PlaywrightTimeoutError:
            self._save_debug_info(f"{context}_tab_missing")
            raise DataExtractionError(
                f"[{context}] tab {tab_selector} did not become clickable."
            )
        try:
            frame.wait_for_selector(self.CC_TABLE_NAC, timeout=self.TABLE_WAIT_MS)
        except PlaywrightTimeoutError:
            self._save_debug_info(f"{context}_nac_table_missing")
            raise DataExtractionError(f"[{context}] nacional table did not render.")
        self._save_debug_info(f"{context}_02_table_ready")

        account_id = self._extract_card_id(frame)
        movements: List[MovementModel] = []
        movements.extend(
            self._extract_cc_nacional_movements(
                frame, account_id=account_id, transaction_type=transaction_type
            )
        )
        movements.extend(
            self._extract_cc_internacional_movements(
                frame, account_id=account_id, transaction_type=transaction_type
            )
        )
        logger.info(f"[{context}] extracted {len(movements)} CC movements.")
        return movements

    def _extract_card_id(self, root: LocatorRoot) -> str:
        """Best-effort extraction of card label (e.g. 'Visa Enjoy ****XXXX')."""
        try:
            label = root.locator(self.CC_CARD_LABEL_SELECTOR).first
            container = label.locator(
                "xpath=ancestor::*[contains(@class,'children-container')][1]"
            )
            value = container.locator("span").first.inner_text(timeout=3000).strip()
            return value or "card_unknown"
        except Exception:
            try:
                near = root.locator("span:has-text('Visa')").first
                return near.inner_text(timeout=2000).strip() or "card_unknown"
            except Exception:
                return "card_unknown"

    def _extract_cc_nacional_movements(
        self,
        root: LocatorRoot,
        account_id: Optional[str],
        transaction_type: str,
    ) -> List[MovementModel]:
        """Parse the national CC movement table.

        Sign convention in the rendered DOM: negative = abono (payment),
        positive = cargo (spending). The :class:`MovementModel` standard is
        ``negative = outflow``, so we **invert** the parsed sign.
        """
        return self._extract_cc_rows(
            root=root,
            table_selector=self.CC_TABLE_NAC,
            account_id=account_id,
            transaction_type=transaction_type,
            currency="CLP",
            extract_currency_from_amount=False,
            amount_col_idx=3,
            location_col_idx=2,
        )

    def _extract_cc_internacional_movements(
        self,
        root: LocatorRoot,
        account_id: Optional[str],
        transaction_type: str,
    ) -> List[MovementModel]:
        """Parse the international CC table; preserves the per-row currency.

        Row layout: fecha, descripción, país, referencia, monto (e.g.
        ``USD -23,80``). Sign convention matches nacional (invert raw sign).
        """
        return self._extract_cc_rows(
            root=root,
            table_selector=self.CC_TABLE_INT,
            account_id=account_id,
            transaction_type=transaction_type,
            currency="USD",
            extract_currency_from_amount=True,
            amount_col_idx=4,
            location_col_idx=2,
        )

    def _extract_cc_rows(
        self,
        *,
        root: LocatorRoot,
        table_selector: str,
        account_id: Optional[str],
        transaction_type: str,
        currency: str,
        extract_currency_from_amount: bool,
        amount_col_idx: int,
        location_col_idx: int,
    ) -> List[MovementModel]:
        table = root.locator(table_selector).first
        rows = table.locator(self.CC_ROW).all()
        logger.debug(f"CC rows located in {table_selector}: {len(rows)}")

        movements: List[MovementModel] = []
        for idx, row in enumerate(rows):
            try:
                cells = row.locator(self.CC_CELL).all()
                if len(cells) < 4:
                    logger.debug(f"Skipping CC row {idx}: only {len(cells)} cells.")
                    continue

                date_str = self._clean_text(cells[0].inner_text(timeout=5000))
                if not date_str:
                    # Summary row (TOTAL PAGOS / TOTAL COMPRAS) — skip.
                    continue

                description = self._clean_text(cells[1].inner_text(timeout=5000))
                location = (
                    self._clean_text(cells[location_col_idx].inner_text(timeout=5000))
                    if len(cells) > location_col_idx
                    else ""
                )
                if len(cells) <= amount_col_idx:
                    logger.debug(
                        f"Skipping CC row {idx}: amount column {amount_col_idx} missing."
                    )
                    continue
                amount_str = self._clean_text(
                    cells[amount_col_idx].inner_text(timeout=5000)
                )

                parsed_date = parse_chilean_date(date_str)
                if parsed_date is None:
                    logger.warning(f"CC row {idx}: unparseable date {date_str!r}.")
                    continue

                row_currency = currency
                if extract_currency_from_amount:
                    m = re.match(r"\s*([A-Za-z]{3})\b", amount_str)
                    if m:
                        row_currency = m.group(1).upper()

                amount_value = parse_chilean_amount(amount_str)
                if amount_value.is_zero():
                    continue

                # Invert sign: raw "$-1.089.139" is an abono (payment); we
                # store payments as positive (inflow) and cargos as negative.
                amount_value = amount_value * Decimal("-1")

                movements.append(
                    MovementModel(
                        date=parsed_date,
                        description=description,
                        amount=amount_value,
                        currency=row_currency,
                        transaction_type=transaction_type,
                        account_id=account_id,
                        account_type="credito",
                        raw_data={
                            "date_str": date_str,
                            "amount_str": amount_str,
                            "location": location,
                            "table": table_selector,
                        },
                    )
                )
            except Exception as exc:
                logger.warning(f"Failed to parse CC row {idx}: {exc}")
                continue
        return movements

    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "")).strip()

    # ─── Orchestrator ─────────────────────────────────────────────────────

    def _scrape_movements(self) -> List[MovementModel]:
        """Orchestrate extraction of checking + credit card movements."""
        logger.info("Starting Scotiabank Chile movement extraction.")
        all_movements: List[MovementModel] = []

        for label, fn in (
            ("checking", self._scrape_checking),
            ("cc_billed", self._scrape_credit_card_billed),
            ("cc_unbilled", self._scrape_credit_card_unbilled),
        ):
            try:
                all_movements.extend(fn())
            except DataExtractionError as exc:
                self._save_debug_info(f"{label}_extraction_failed")
                logger.warning(f"{label} extraction failed: {exc}")

        logger.info(
            f"Scotiabank Chile extraction finished. Total: {len(all_movements)} "
            f"movements."
        )
        return all_movements
