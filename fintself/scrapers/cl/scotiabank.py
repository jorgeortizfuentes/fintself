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
    """Scraper to extract movements from Scotiabank Chile (personas).

    Limitations:
        Developed and live-tested against a single-product profile: one
        cuenta corriente (CTACTE) and one Visa Enjoy credit card. The shell
        URLs hardcode ``?type=CTACTE`` (``CHECKING_SHELL_URL``) and omit any
        ``card=`` selector in ``CC_SHELL_URL``; the portal auto-picks the
        only account/card when there is just one of each.

        Users with multiple checking-style accounts (additional CTACTE, or
        CTAH / CTANI / CTAV variants) or multiple credit cards would have
        the extra products silently skipped — no selection step is
        implemented. To add multi-product support, extend ``scrape`` to
        read the account list and iterate ``?type=`` values, and read the
        card dropdown inside ``CC_SHELL_URL`` to iterate ``?card=NNNN``
        values. PRs welcome.
    """

    HOME_URL = "https://www.scotiabankchile.cl/"
    LOGIN_URL_PREFIX = "https://banco.scotiabank.cl/mfe-login/scotia"
    DASHBOARD_URL_FRAGMENT = "/mfe-home-cl/"

    PUBLIC_LOGIN_TEXT = "Acceso Scotia"
    PUBLIC_LOGIN_PERSONAS_NAME = "Ingreso Personas"

    RUT_INPUT = '[data-testid="inputDni"]'
    PASSWORD_INPUT = '[data-testid="inputPassword"]'
    SUBMIT_BUTTON = 'role=button[name="Ingresar"]'

    # Tab buttons inside the CC iframe (Saldo / Facturados / No facturados).
    # `id` attributes are hash-free and stable; fallbacks via `id$=` cover
    # a hypothetical schema rename.
    TAB_BILLED_SELECTOR = (
        "button#tab-action__movimientos-facturados, "
        'button[id$="movimientos-facturados"]'
    )
    TAB_UNBILLED_SELECTOR = (
        "button#tab-action__movimientos-no-facturados, "
        'button[id$="movimientos-no-facturados"]'
    )

    # Sub-tab buttons inside the active CC pane (Facturados / No facturados).
    # Portal renders <button class="button button--tab tab__action"> with
    # text "Nacional"/"Nacionales" / "Internacional"/"Internacionales".
    # NOT a radio input. Substring match handles both singular and plural.
    SUBTAB_NACIONAL = (
        'button.tab__action:has-text("Nacional"):not(:has-text("Internacional"))'
    )
    SUBTAB_INTERNAC = 'button.tab__action:has-text("Internacional")'

    CHECKING_IFRAME_URL_FRAGMENT = "mfe-accounts-balancesmovements-web"
    CC_IFRAME_URL_FRAGMENT = "mfe-simple-account-statement-web-cl"

    SHELL_BASE = "https://www.scotiabank.cl/mfe/sweb/mfe-shell-web-cl/mfe/"
    CHECKING_SHELL_URL = (
        SHELL_BASE
        + "mfe/ltmnsw/mfe-accounts-balancesmovements-web/?tab=saldos&type=CTACTE"
    )
    CC_BILLED_SHELL_URL = (
        SHELL_BASE + "mfe-simple-account-statement-web-cl/?tab=movimientos-facturados"
    )
    CC_UNBILLED_SHELL_URL = (
        SHELL_BASE
        + "mfe-simple-account-statement-web-cl/?tab=movimientos-no-facturados"
    )

    # Checking table — primary uses BEM class names; `id="table-table"`
    # is a stable fallback present on the actual data table.
    CHECKING_TABLE = "table.Table__dataTable, table#table-table"
    CHECKING_ROW = (
        'tbody.TableBody tr.TableBody__bodyRow, #table-table tbody tr[class*="bodyRow"]'
    )
    CHECKING_CELL = "td.TableBody__cell, #table-table tbody td"

    # Credit-card tables. `[class*="...nacional"]:not([class*="print"])`
    # fallback survives a class-modifier rename.
    CC_TABLE_NAC = (
        "div.tabla__movimientos--nacional table.table, "
        '[class*="movimientos--nacional"]:not([class*="print"]) table'
    )
    CC_TABLE_INT = (
        "div.tabla__movimientos--internacional table.table, "
        '[class*="movimientos--internacional"]:not([class*="print"]) table'
    )
    CC_ROW = "tbody tr.table__row"
    CC_CELL = "td.table__data"

    CC_CARD_LABEL_SELECTOR = "label.label__tarjetas-credito"

    POST_LOGIN_SETTLE_MS = 8000
    TOUR_POLL_INTERVAL_MS = 500
    TOUR_PROBE_TIMEOUT_MS = 200
    TOUR_PRE_NAV_MS = 500

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

    # Promotional modal on scotiabank.cl home (intermitente, intercepta 'Acceso Scotia')
    HOME_PROMO_CLOSE_SELECTOR = "a.sc-itt-close-btn"
    HOME_PROMO_TIMEOUT_MS = 5000

    def _get_bank_id(self) -> str:
        return "cl_scotiabank"

    # ─── Login ────────────────────────────────────────────────────────────

    def _login(self) -> None:
        """Login to Scotiabank Chile personas portal."""
        if not self.user or not self.password:
            raise LoginError(
                "CL_SCOTIABANK_USER and CL_SCOTIABANK_PASSWORD must be set."
            )

        page = self._ensure_page()
        logger.info("Navigating to Scotiabank Chile home page.")
        self._navigate(self.HOME_URL, timeout_override=60000)
        self._save_debug_info("01_home_page")
        self._dismiss_home_popup(page)

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
                re.compile(re.escape(self.DASHBOARD_URL_FRAGMENT)), timeout=45000
            )
            self._save_debug_info("04_login_success")
            logger.info("Login to Scotiabank Chile successful.")
        except (PlaywrightTimeoutError, AssertionError):
            self._save_debug_info("post_login_error")
            current_url = page.url
            try:
                body_snippet = page.content()[:1500].lower()
            except Exception:
                body_snippet = ""
            if any(
                kw in body_snippet
                for kw in (
                    "mantenimiento",
                    "mantención",
                    "mantencion",
                    "fuera de servicio",
                )
            ):
                raise LoginError(
                    f"Scotiabank portal appears to be under maintenance "
                    f"(url={current_url}). See debug_output/post_login_error_*."
                )
            raise LoginError(
                f"Post-login redirect to {self.DASHBOARD_URL_FRAGMENT} did not "
                f"happen within 45s (url={current_url}). Likely cause: invalid "
                f"credentials or portal blocked the session. "
                f"See debug_output/post_login_error_*."
            )

        self._dismiss_onboarding_tour(page)

    def _dismiss_onboarding_tour(
        self,
        page: Page,
        context: str = "post_login",
        max_wait_ms: Optional[int] = None,
    ) -> bool:
        """Poll for and close the onboarding tour overlay.

        Never matches 'Cerrar' alone (that is the logout button text).
        Returns True if a tour button was dismissed, False otherwise.

        ``max_wait_ms`` overrides the polling window — use a small value
        (e.g. ``TOUR_PRE_NAV_MS``) when called right before a click to
        avoid wasting time when no overlay is present. Default is the
        post-login settle window.
        """
        budget = max_wait_ms if max_wait_ms is not None else self.POST_LOGIN_SETTLE_MS
        elapsed = 0
        while elapsed < max(budget, 1):
            for sel in self.TOUR_DISMISS_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=self.TOUR_PROBE_TIMEOUT_MS):
                        logger.info(f"[{context}] dismissing tour overlay: {sel}")
                        loc.click(timeout=2000)
                        page.wait_for_timeout(300)
                        self._save_debug_info(f"tour_dismissed_{context}")
                        return True
                except Exception:
                    pass
            if elapsed + self.TOUR_POLL_INTERVAL_MS >= budget:
                break
            page.wait_for_timeout(self.TOUR_POLL_INTERVAL_MS)
            elapsed += self.TOUR_POLL_INTERVAL_MS
        return False

    def _dismiss_home_popup(self, page: Page) -> bool:
        """Dismiss promotional modal that may appear on scotiabank.cl home.

        Returns True if a popup was found and the close button was clicked,
        False if no popup was detected within the timeout or the click failed.
        Never raises — the login flow must continue regardless.
        """
        try:
            page.wait_for_selector(
                self.HOME_PROMO_CLOSE_SELECTOR,
                state="visible",
                timeout=self.HOME_PROMO_TIMEOUT_MS,
            )
        except PlaywrightTimeoutError:
            logger.debug(
                "[home] no promo popup detected within %dms",
                self.HOME_PROMO_TIMEOUT_MS,
            )
            return False
        try:
            page.locator(self.HOME_PROMO_CLOSE_SELECTOR).first.click(timeout=2000)
            logger.info("[home] dismissed promo popup")
            self._save_debug_info("home_popup_dismissed")
            return True
        except Exception as exc:
            logger.warning("[home] popup found but click failed: %s", exc)
            self._save_debug_info("home_popup_click_failed")
            return False

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
        last_urls: List[str] = []
        while elapsed < deadline_ms:
            matches = [f for f in page.frames if url_fragment in (f.url or "")]
            # Require the INNER content frame (no `mfe-shell` segment) — the
            # outer shell frame matches the fragment too but lacks the
            # table DOM. Keep polling until the inner frame appears.
            inner = [f for f in matches if "mfe-shell" not in (f.url or "")]
            if inner:
                return inner[-1]
            last_urls = [f.url for f in page.frames if f.url]
            page.wait_for_timeout(interval)
            elapsed += interval
        self._save_debug_info(f"iframe_missing_{url_fragment}")
        logger.warning(f"Frames present at timeout for '{url_fragment}': {last_urls!r}")
        raise DataExtractionError(
            f"Could not locate iframe-stage frame with url fragment '{url_fragment}'."
        )

    # ─── Checking ─────────────────────────────────────────────────────────

    def _scrape_checking(self) -> List[MovementModel]:
        """Navigate to checking account view and extract movements."""
        page = self._ensure_page()
        logger.info("Navigating directly to checking shell URL.")
        self._navigate(self.CHECKING_SHELL_URL, timeout_override=60000)
        self._save_debug_info("checking_01_navigated")
        self._dismiss_onboarding_tour(
            page, context="pre_checking", max_wait_ms=self.TOUR_PRE_NAV_MS
        )

        frame = self._get_stage_frame(self.CHECKING_IFRAME_URL_FRAGMENT)
        try:
            frame.wait_for_selector(self.CHECKING_TABLE, timeout=self.TABLE_WAIT_MS)
        except PlaywrightTimeoutError:
            self._save_debug_info("checking_table_missing")
            logger.warning(
                "[checking] table did not render; assuming no movements "
                "(empty account or selector change)."
            )
            return []
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
            shell_url=self.CC_BILLED_SHELL_URL,
            tab_selector=self.TAB_BILLED_SELECTOR,
            transaction_type="Facturado",
            context="cc_billed",
        )

    def _scrape_credit_card_unbilled(self) -> List[MovementModel]:
        return self._scrape_cc_tab(
            shell_url=self.CC_UNBILLED_SHELL_URL,
            tab_selector=self.TAB_UNBILLED_SELECTOR,
            transaction_type="NoFacturado",
            context="cc_unbilled",
        )

    def _scrape_cc_tab(
        self,
        *,
        shell_url: str,
        tab_selector: str,
        transaction_type: str,
        context: str,
    ) -> List[MovementModel]:
        """Navigate to the CC MFE shell URL for this tab and extract.

        Each tab (facturados / no-facturados) has its own shell URL so
        Playwright lands on a fresh iframe state. Falling back to a single
        URL + tab click leaks the previous tab's DOM (both panes stay
        mounted) and causes duplicate extractions.
        """
        page = self._ensure_page()
        logger.info(f"[{context}] navigating directly to {shell_url}.")
        self._navigate(shell_url, timeout_override=60000)
        self._save_debug_info(f"{context}_01_navigated")
        self._dismiss_onboarding_tour(
            page, context=f"pre_{context}", max_wait_ms=self.TOUR_PRE_NAV_MS
        )

        frame = self._get_stage_frame(self.CC_IFRAME_URL_FRAGMENT)

        # Tab is pre-selected by URL; if the button is rendered we still
        # click it to be defensive (no-op if already active).
        logger.info(f"[{context}] waiting for tab {tab_selector} to render.")
        try:
            frame.wait_for_selector(tab_selector, timeout=self.TABLE_WAIT_MS)
        except PlaywrightTimeoutError:
            self._save_debug_info(f"{context}_tab_missing")
            raise DataExtractionError(
                f"[{context}] tab {tab_selector} not present in iframe."
            )
        try:
            frame.locator(tab_selector).click(timeout=3000)
            logger.info(f"[{context}] tab clicked.")
        except Exception as exc:
            logger.info(f"[{context}] tab click skipped (already active?): {exc}")
        try:
            frame.wait_for_selector(self.CC_TABLE_NAC, timeout=self.TABLE_WAIT_MS)
        except PlaywrightTimeoutError:
            self._save_debug_info(f"{context}_nac_table_missing")
            logger.warning(
                f"[{context}] nacional table did not render; assuming empty period."
            )
            return []
        self._save_debug_info(f"{context}_02_table_ready")

        account_id = self._extract_card_id(frame)
        movements: List[MovementModel] = []

        # Nacional sub-tab (default-active in DOM) → expand → extract.
        self._select_cc_radio(frame, self.SUBTAB_NACIONAL, "nacional", context)
        self._expand_all_ver_mas(
            frame,
            context=f"{context}_nacional",
            anchor_selector=self.CC_TABLE_NAC,
        )
        nac = self._extract_cc_nacional_movements(
            frame, account_id=account_id, transaction_type=transaction_type
        )
        logger.info(f"[{context}] nacional rows: {len(nac)}.")
        movements.extend(nac)

        # Internacional sub-tab → SPA unhides the internacional table.
        # Tolerate failure: user may not have USD movements.
        if self._select_cc_radio(frame, self.SUBTAB_INTERNAC, "internacional", context):
            try:
                frame.wait_for_selector(
                    self.CC_TABLE_INT, state="visible", timeout=5000
                )
            except PlaywrightTimeoutError:
                logger.info(
                    f"[{context}] internacional table did not render; "
                    f"assuming no USD movements."
                )
                return movements
            self._expand_all_ver_mas(
                frame,
                context=f"{context}_internacional",
                anchor_selector=self.CC_TABLE_INT,
            )
            intl = self._extract_cc_internacional_movements(
                frame, account_id=account_id, transaction_type=transaction_type
            )
            logger.info(f"[{context}] internacional rows: {len(intl)}.")
            movements.extend(intl)

        logger.info(f"[{context}] extracted {len(movements)} CC movements.")
        return movements

    def _expand_all_ver_mas(
        self, frame: Frame, context: str, anchor_selector: Optional[str] = None
    ) -> None:
        """Click 'Ver más Movimientos' until exhausted (paginated panel).

        Each click reveals more rows in the active CC pane. Buttons are
        often off-screen and Playwright's ``is_visible`` returns False
        for them, so we iterate ALL matching buttons by index, scroll
        each into view, and click whatever becomes actionable. Capped at
        30 iterations to avoid infinite loops.

        ``anchor_selector`` (optional): scroll this element into view
        before each pass so a section's own 'Ver más' button gets
        mounted in the viewport.
        """
        button_selector = (
            "button:has-text('Ver más Movimientos'), button:has-text('Ver más')"
        )
        clicks = 0
        max_clicks = 30
        row_selector = anchor_selector + " tbody tr" if anchor_selector else None
        while clicks < max_clicks:
            if anchor_selector:
                try:
                    anchor = frame.locator(anchor_selector).first
                    if anchor.count() > 0:
                        anchor.scroll_into_view_if_needed(timeout=1500)
                except Exception:
                    pass
            rows_before = frame.locator(row_selector).count() if row_selector else None
            buttons = frame.locator(button_selector)
            count = buttons.count()
            clicked = False
            for i in range(count):
                btn = buttons.nth(i)
                try:
                    btn.scroll_into_view_if_needed(timeout=1500)
                    if not btn.is_visible(timeout=500):
                        continue
                    btn.click(timeout=3000)
                    frame.wait_for_timeout(800)
                    clicks += 1
                    clicked = True
                    break
                except Exception as exc:
                    logger.debug(
                        f"[{context}] 'Ver más' button[{i}] skipped: "
                        f"{type(exc).__name__}"
                    )
            if clicked and row_selector is not None:
                rows_after = frame.locator(row_selector).count()
                if rows_after <= rows_before:
                    logger.info(
                        f"[{context}] 'Ver más' click did not add rows "
                        f"({rows_before} → {rows_after}); stopping."
                    )
                    break
            if not clicked:
                break
        if clicks:
            logger.info(f"[{context}] expanded 'Ver más' {clicks} time(s).")

    def _select_cc_radio(
        self, frame: Frame, radio_selector: str, label: str, context: str
    ) -> bool:
        """Click a CC radio toggle (Nacional / Internacional) inside the iframe.

        Returns True if the click succeeded (or radio not present, treated
        as "already in that view"); False only on hard failures.
        """
        try:
            loc = frame.locator(radio_selector).first
            if loc.count() == 0:
                logger.info(
                    f"[{context}] radio '{label}' not present; "
                    f"assuming view already active."
                )
                return True
            loc.scroll_into_view_if_needed(timeout=3000)
            loc.click(timeout=5000)
            # Wait for the SPA to swap the active panel's table; lazy
            # renders may otherwise miss the last few rows.
            frame.wait_for_timeout(2000)
            logger.info(f"[{context}] selected '{label}' radio.")
            self._save_debug_info(f"{context}_radio_{label}")
            return True
        except Exception as exc:
            logger.warning(
                f"[{context}] could not select '{label}' radio: "
                f"{type(exc).__name__}: {exc}"
            )
            return False

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
                # Sanity check anchored at the start of the description so
                # merchant tokens that contain "pago" as a substring don't
                # trigger false positives.
                if amount_value < 0 and re.match(
                    r"^\s*(pago|abono|devoluci[óo]n)\b",
                    description,
                    re.IGNORECASE,
                ):
                    logger.warning(
                        f"Sign-convention sanity warning: row {idx} description "
                        f"{description[:40]!r} looks like a payment but mapped to a "
                        f"negative amount ({amount_value}). Portal may have "
                        f"inverted its sign convention."
                    )

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
    def _clean_text(text: Optional[str]) -> str:
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
                logger.warning(f"[{label}] extraction failed: {exc}")
            except Exception as exc:
                self._save_debug_info(f"{label}_unexpected_error")
                logger.exception(
                    f"[{label}] unexpected error; other sections will continue: "
                    f"{type(exc).__name__}: {exc}"
                )

        logger.info(
            f"Scotiabank Chile extraction finished. Total: {len(all_movements)} "
            f"movements."
        )
        return all_movements
