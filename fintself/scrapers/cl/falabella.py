"""Scraper for Banco Falabella Chile (checking account + CMR credit).

Login and navigation flow adapted from the MIT-licensed open-banking-chile
Falabella scraper (https://github.com/kaihv/open-banking-chile).
"""

import re
from typing import List, Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from fintself.core.exceptions import LoginError
from fintself.core.models import MovementModel
from fintself.scrapers.base import BaseScraper
from fintself.utils.logging import logger
from fintself.utils.parsers import parse_chilean_amount, parse_chilean_date


class FalabellaScraper(BaseScraper):
    """Scraper for Banco Falabella: cuenta corriente and CMR credit card."""

    LOGIN_URL = "https://www.bancofalabella.cl"
    MAX_PAGES = 20
    CMR_HOST = "credit-card-movements"
    CMR_WAIT_MS = 30000

    def _get_bank_id(self) -> str:
        return "cl_falabella"

    def _login(self) -> None:
        """Two-step login: RUT, then Clave Internet (no OTP in the common path)."""
        assert self.user is not None, "User must be provided"
        assert self.password is not None, "Password must be provided"

        page = self._ensure_page()
        logger.info("Navigating to Banco Falabella homepage.")
        self._navigate(self.LOGIN_URL, timeout_override=90000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)
        self._dismiss_banners()
        self._save_debug_info("01_homepage")

        logger.info("Opening 'Mi cuenta' login.")
        self._click_mi_cuenta()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)
        self._save_debug_info("02_login_form")

        logger.info("Entering RUT.")
        rut_input = page.get_by_role("textbox", name="RUT", exact=True).or_(
            page.locator(
                'input[name*="rut"], input[id*="rut"], input[placeholder*="RUT"]'
            ).first
        )
        try:
            rut_input.fill(self.user, timeout=10000)
        except PlaywrightTimeoutError:
            self._save_debug_info("rut_field_not_found")
            raise LoginError("Could not find RUT field on Banco Falabella login.")
        page.wait_for_timeout(1000)

        # Falabella uses a two-step modal: RUT first, then password.
        page.keyboard.press("Enter")
        page.wait_for_timeout(2000)

        logger.info("Entering Clave Internet.")
        pwd_input = page.locator('input[type="password"]').first.or_(
            page.get_by_role("textbox", name=re.compile(r"[Cc]lave")).first
        )
        try:
            pwd_input.fill(self.password, timeout=10000)
        except PlaywrightTimeoutError:
            self._save_debug_info("password_field_not_found")
            raise LoginError("Could not find password field on Banco Falabella login.")
        page.wait_for_timeout(500)
        self._save_debug_info("03_credentials_entered")

        logger.info("Submitting login.")
        submit = page.locator('button[type="submit"], input[type="submit"]').first.or_(
            page.get_by_role("button", name=re.compile(r"ingresar|entrar", re.I)).first
        )
        try:
            submit.click(timeout=3000)
        except PlaywrightTimeoutError:
            page.keyboard.press("Enter")

        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(8000)
        self._save_debug_info("04_after_login")

        self._dismiss_post_login()
        self._check_login_outcome()
        self._dashboard_url = page.url
        logger.info("Login to Banco Falabella successful.")

    def _click_mi_cuenta(self) -> None:
        """Opens the login form via the header 'Mi cuenta' button.

        The button uses aria-label=\"Button\", so role-based name matching
        fails — prefer the stable #btn-auth-normal id, then text fallbacks.
        """
        page = self._ensure_page()
        try:
            page.wait_for_selector("#main-header__sub-content", timeout=20000)
        except PlaywrightTimeoutError:
            logger.debug("Header #main-header__sub-content not found; continuing.")

        # Prefer visible candidates; .first on a broad locator can hit a hidden twin.
        selectors = [
            "#btn-auth-normal",
            '#main-header__sub-content button:has-text("Mi cuenta")',
            'button.button_button__primary__OoF9e:has-text("Mi cuenta")',
            'button:has-text("Mi cuenta")',
        ]
        for selector in selectors:
            loc = page.locator(selector)
            try:
                count = loc.count()
                for i in range(count):
                    candidate = loc.nth(i)
                    if candidate.is_visible(timeout=1500):
                        self._click(candidate, force=True, skip_hover=True)
                        logger.info(f"Clicked 'Mi cuenta' via {selector}[{i}].")
                        return
            except Exception as e:
                logger.debug(f"Selector '{selector}' failed: {e}")
                continue

        self._save_debug_info("mi_cuenta_not_found")
        raise LoginError("Could not find 'Mi cuenta' on Banco Falabella homepage.")

    def _dismiss_banners(self) -> None:
        page = self._ensure_page()
        try:
            btn = (
                page.locator("button, a")
                .filter(has_text=re.compile(r"^(Aceptar|Entendido|Continuar)$", re.I))
                .first
            )
            if btn.is_visible(timeout=2000):
                btn.click()
        except Exception:
            pass

    def _dismiss_post_login(self) -> None:
        page = self._ensure_page()
        try:
            close_btn = page.get_by_role("button", name="cerrar", exact=True)
            if close_btn.is_visible(timeout=2000):
                close_btn.click()
        except Exception:
            pass
        try:
            retry = page.get_by_text("Reintentar")
            if retry.is_visible(timeout=2000):
                retry.click()
                page.wait_for_timeout(5000)
        except Exception:
            pass

    def _check_login_outcome(self) -> None:
        page = self._ensure_page()
        content = page.content().lower()
        if "clave dinámica" in content or "segundo factor" in content:
            self._save_debug_info("login_2fa_required")
            raise LoginError(
                "Banco Falabella asked for clave dinámica / 2FA. "
                "Complete it manually in visible mode, or retry later."
            )

        try:
            error = page.locator(
                '[class*="error"], [class*="alert"], [role="alert"]'
            ).first
            text = error.text_content(timeout=2000)
            if text and 5 < len(text.strip()) < 200:
                self._save_debug_info("login_error_message")
                raise LoginError(f"Banco Falabella login error: {text.strip()}")
        except (PlaywrightTimeoutError, LoginError) as e:
            if isinstance(e, LoginError):
                raise
        except Exception:
            pass

        # Soft success signal: still on public homepage without products is a fail.
        if (
            "mi cuenta" in content
            and "cuenta corriente" not in content
            and "cmr" not in content
        ):
            # May still be loading — look for any product-ish text after extra wait.
            page.wait_for_timeout(3000)
            content = page.content().lower()
            if "cuenta corriente" not in content and "cmr" not in content:
                logger.warning(
                    "Post-login product markers not found; continuing anyway "
                    "(debug HTML may clarify)."
                )

    def _scrape_movements(self) -> List[MovementModel]:
        """Extract CMR credit then checking-account movements.

        CMR lives on the consolidada dashboard (`app-credit-cards`). Scraping it
        first avoids a fragile reload after leaving for the checking cartola.
        """
        page = self._ensure_page()
        dashboard_url = getattr(self, "_dashboard_url", None) or page.url

        self._wait_for_dashboard_products()
        self._capture_dashboard_account_ids()

        credit = self._scrape_credit_movements()
        logger.info(f"CMR credit: {len(credit)} movements.")

        logger.info("Returning to dashboard for cuenta corriente.")
        self._go_to_dashboard(dashboard_url)
        self._capture_dashboard_account_ids()
        checking = self._scrape_checking_movements()
        logger.info(f"Checking account: {len(checking)} movements.")

        all_movements = checking + credit
        logger.info(f"Total Falabella movements: {len(all_movements)}")
        return all_movements

    def _go_to_dashboard(self, dashboard_url: str) -> None:
        page = self._ensure_page()
        self._navigate(dashboard_url, timeout_override=60000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)
        self._dismiss_post_login()
        self._wait_for_dashboard_products()

    def _wait_for_dashboard_products(self) -> None:
        """Wait until Angular consolidada products (CMR / accounts) render."""
        page = self._ensure_page()
        try:
            page.wait_for_selector(
                "app-credit-cards, #cardDetail0, #accountDetail0, a.div-product",
                timeout=20000,
            )
            page.wait_for_timeout(1500)
        except PlaywrightTimeoutError:
            logger.warning("Dashboard product widgets did not appear in time.")
            self._save_debug_info("dashboard_products_timeout")

    def _capture_dashboard_account_ids(self) -> None:
        """Read last-4 account/card ids from consolidada product cards."""
        page = self._ensure_page()
        try:
            ids = page.evaluate(
                """() => {
                    const out = { checking: null, cmr: null };

                    const cmrEl = document.querySelector(
                        "#cardDetail0, app-credit-cards a.div-product"
                    );
                    if (cmrEl) {
                        const t = (cmrEl.innerText || "").replace(/\\s+/g, " ").trim();
                        // e.g. "CMR Mastercard Premium • • • • 7723"
                        let m = t.match(/[•·*]\\s*(\\d{4})\\s*$/);
                        if (!m) m = t.match(/(\\d{4})\\s*$/);
                        if (m) out.cmr = m[1];
                    }

                    // e.g. "Cuenta Corriente 1 001 371338 0" on #accountDetail0
                    const acctEl = document.querySelector(
                        "#accountDetail0, a.div-product[id^='accountDetail']"
                    );
                    if (acctEl) {
                        const t = (acctEl.innerText || "").replace(/\\s+/g, " ").trim();
                        const digits = (t.match(/\\d[\\d\\s]+\\d/) || [""])[0]
                            .replace(/\\D/g, "");
                        if (digits.length >= 4) out.checking = digits.slice(-4);
                    }
                    if (!out.checking) {
                        const candidates = Array.from(
                            document.querySelectorAll(
                                "a.div-product, a, button, div.product-name"
                            )
                        );
                        for (const el of candidates) {
                            const t = (el.innerText || "").replace(/\\s+/g, " ").trim();
                            if (!/cuenta\\s+corriente/i.test(t)) continue;
                            const digits = (t.match(/\\d[\\d\\s]+\\d/) || [""])[0]
                                .replace(/\\D/g, "");
                            if (digits.length >= 4) {
                                out.checking = digits.slice(-4);
                                break;
                            }
                        }
                    }
                    return out;
                }"""
            )
        except Exception as e:
            logger.warning(f"Could not capture dashboard account ids: {e}")
            return

        if ids.get("cmr"):
            self._cmr_account_id = ids["cmr"]
            logger.info(f"Captured CMR account_id (last 4): {self._cmr_account_id}")
        if ids.get("checking"):
            self._checking_account_id = ids["checking"]
            logger.info(
                f"Captured checking account_id (last 4): {self._checking_account_id}"
            )


    # ── Checking account ──────────────────────────────────────────

    def _scrape_checking_movements(self) -> List[MovementModel]:
        page = self._ensure_page()
        logger.info("Navigating to cuenta corriente movements.")

        navigated = False
        for selector in (
            "#accountDetail0",
            "a.div-product[id^='accountDetail']",
            "a.div-product:has-text('Cuenta Corriente')",
        ):
            loc = page.locator(selector).first
            try:
                if loc.is_visible(timeout=3000):
                    self._click(loc, force=True, skip_hover=True)
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(3000)
                    navigated = True
                    logger.info(f"Opened checking account via {selector}.")
                    break
            except Exception as e:
                logger.debug(f"Checking selector '{selector}' failed: {e}")

        if not navigated:
            cc_link = page.get_by_role(
                "link", name=re.compile(r"Cuenta Corriente", re.I)
            )
            try:
                if cc_link.is_visible(timeout=5000):
                    self._click(cc_link)
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(3000)
                    navigated = True
            except Exception:
                pass

        if not navigated:
            for text in (
                "cartola",
                "últimos movimientos",
                "movimientos",
                "estado de cuenta",
            ):
                link = (
                    page.locator("a, button, [role='tab']").filter(has_text=text).first
                )
                try:
                    if link.is_visible(timeout=2000):
                        self._click(link)
                        page.wait_for_timeout(4000)
                        navigated = True
                        break
                except Exception:
                    continue

        self._save_debug_info("05_checking_movements")
        self._try_expand_date_range()
        return self._paginate_checking_movements()

    def _try_expand_date_range(self) -> None:
        page = self._ensure_page()
        try:
            selects = page.locator("select")
            for i in range(selects.count()):
                sel = selects.nth(i)
                options = sel.locator("option").all_text_contents()
                for text in options:
                    lower = text.lower()
                    if any(
                        k in lower
                        for k in ("todos", "último mes", "30 día", "mes anterior")
                    ):
                        sel.select_option(label=text)
                        page.wait_for_timeout(3000)
                        return
        except Exception:
            pass

    def _paginate_checking_movements(self) -> List[MovementModel]:
        page = self._ensure_page()
        account_id = getattr(self, "_checking_account_id", None)
        all_movements: List[MovementModel] = []
        seen: set[tuple] = set()

        for _ in range(self.MAX_PAGES):
            raw_rows = page.evaluate(self._CHECKING_TABLE_JS)
            for row in raw_rows:
                movement = self._row_to_checking_movement(row, account_id=account_id)
                if not movement:
                    continue
                key = (
                    movement.date.isoformat(),
                    movement.description,
                    str(movement.amount),
                )
                if key in seen:
                    continue
                seen.add(key)
                all_movements.append(movement)

            clicked = False
            for text in ("siguiente", "ver más", "mostrar más"):
                btn = page.locator("button, a").filter(has_text=text).first
                try:
                    if btn.is_visible(timeout=1000) and not btn.is_disabled():
                        self._click(btn)
                        page.wait_for_timeout(2500)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                break

        return all_movements

    _CHECKING_TABLE_JS = """
    () => {
        const results = [];
        const tables = Array.from(document.querySelectorAll("table"));
        for (const table of tables) {
            const rows = Array.from(table.querySelectorAll("tr"));
            if (rows.length < 2) continue;

            let dateIdx = 0, descIdx = 1, cargoIdx = -1, abonoIdx = -1,
                amountIdx = -1, balanceIdx = -1;
            let hasHeader = false;

            for (const row of rows) {
                const headers = row.querySelectorAll("th");
                if (headers.length < 2) continue;
                const hTexts = Array.from(headers).map(
                    h => (h.innerText || "").trim().toLowerCase()
                );
                if (!hTexts.some(h => h.includes("fecha"))) continue;
                hasHeader = true;
                dateIdx = hTexts.findIndex(h => h.includes("fecha"));
                descIdx = hTexts.findIndex(
                    h => h.includes("descrip") || h.includes("detalle") || h.includes("glosa")
                );
                cargoIdx = hTexts.findIndex(
                    h => h.includes("cargo") || h.includes("débito")
                );
                abonoIdx = hTexts.findIndex(
                    h => h.includes("abono") || h.includes("crédito")
                );
                amountIdx = hTexts.findIndex(h => h === "monto" || h.includes("importe"));
                balanceIdx = hTexts.findIndex(h => h.includes("saldo"));
                break;
            }
            if (!hasHeader) continue;

            let lastDate = "";
            for (const row of rows) {
                const cells = row.querySelectorAll("td");
                if (cells.length < 3) continue;
                const vals = Array.from(cells).map(c => (c.innerText || "").trim());
                const rawDate = vals[dateIdx] || "";
                const hasDate = /^\\d{1,2}[\\/.\\-]\\d{1,2}([\\/.\\-]\\d{2,4})?$/.test(rawDate);
                const date = hasDate ? rawDate : lastDate;
                if (!date) continue;
                if (hasDate) lastDate = rawDate;

                const description = descIdx >= 0 ? (vals[descIdx] || "") : "";
                let amountStr = "";
                if (cargoIdx >= 0 && vals[cargoIdx] && vals[cargoIdx].replace(/\\s/g, "")) {
                    amountStr = "-" + vals[cargoIdx];
                } else if (abonoIdx >= 0 && vals[abonoIdx] && vals[abonoIdx].replace(/\\s/g, "")) {
                    amountStr = vals[abonoIdx];
                } else if (amountIdx >= 0) {
                    amountStr = vals[amountIdx] || "";
                }
                if (!amountStr) continue;

                results.push({
                    date,
                    description,
                    amount_str: amountStr,
                    balance_str: balanceIdx >= 0 ? (vals[balanceIdx] || "") : "",
                });
            }
        }
        return results;
    }
    """

    def _row_to_checking_movement(
        self, row: dict, account_id: Optional[str] = None
    ) -> Optional[MovementModel]:
        date = parse_chilean_date(row.get("date"))
        if not date:
            return None
        description = (row.get("description") or "").strip()
        amount = parse_chilean_amount(row.get("amount_str"))
        if not description and amount == 0:
            return None
        return MovementModel(
            date=date,
            description=description or "Sin descripción",
            amount=amount,
            currency="CLP",
            transaction_type="Cargo" if amount < 0 else "Abono",
            account_id=account_id,
            account_type="corriente",
            raw_data={
                "date_str": row.get("date"),
                "amount_str": row.get("amount_str"),
                "balance_str": row.get("balance_str"),
                "product": "cuenta_corriente",
            },
        )

    # ── CMR credit ────────────────────────────────────────────────

    def _scrape_credit_movements(self) -> List[MovementModel]:
        page = self._ensure_page()
        logger.info("Opening CMR credit card.")

        if not self._open_cmr_product():
            logger.warning("No CMR card found on dashboard.")
            self._save_debug_info("cmr_not_found")
            return []

        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(5000)
        self._save_debug_info("06_cmr_card")
        self._wait_for_cmr_content()

        account_id = getattr(self, "_cmr_account_id", None) or self._extract_cmr_mask()
        if account_id:
            logger.info(f"Using CMR account_id: {account_id}")

        logger.info("Extracting unbilled CMR movements.")
        unbilled = self._paginate_cmr_movements(
            billed=False, account_id=account_id, status="unbilled"
        )
        self._save_debug_info("07_cmr_unbilled")

        logger.info("Switching to billed CMR tab.")
        if self._click_cmr_billed_tab():
            page.wait_for_timeout(2000)
            self._wait_for_cmr_content()
            page.wait_for_timeout(3000)
            self._save_debug_info("08_cmr_billed")
            billed = self._paginate_cmr_movements(
                billed=True, account_id=account_id, status="billed"
            )
        else:
            logger.warning("Could not open billed CMR tab.")
            billed = []

        return unbilled + billed

    def _open_cmr_product(self) -> bool:
        """Open CMR detail from consolidada home.

        Prefer stable Angular ids from ``app-credit-cards`` (#cardDetail0 /
        #cardAccount0) over role names — the product link text mixes bullets
        and SVG icons that break accessible-name matching.
        """
        page = self._ensure_page()
        selectors = [
            "#cardDetail0",
            "a.div-product[id^='cardDetail']",
            "app-credit-cards a.div-product",
            "#cardAccount0",  # "Estado de cuenta" — also lands on CMR detail
            "app-credit-cards button#cardAccount0",
        ]
        for selector in selectors:
            loc = page.locator(selector)
            try:
                count = loc.count()
                for i in range(count):
                    candidate = loc.nth(i)
                    if candidate.is_visible(timeout=2000):
                        self._click(candidate, force=True, skip_hover=True)
                        logger.info(f"Opened CMR via {selector}[{i}].")
                        return True
            except Exception as e:
                logger.debug(f"CMR selector '{selector}' failed: {e}")
                continue

        # Last resort: any visible element mentioning CMR on the product card.
        fallback = page.locator("app-credit-cards, a.div-product").filter(
            has_text=re.compile(r"CMR", re.I)
        )
        try:
            if fallback.first.is_visible(timeout=3000):
                self._click(fallback.first, force=True, skip_hover=True)
                logger.info("Opened CMR via text fallback.")
                return True
        except Exception:
            pass
        return False

    def _extract_cmr_mask(self) -> Optional[str]:
        page = self._ensure_page()
        try:
            card = page.locator("#cardDetail0, app-credit-cards a.div-product").first
            if card.count() > 0:
                text = card.inner_text(timeout=2000)
                match = re.search(r"[•·*]\s*(\d{4})\s*$", text.replace("\n", " "))
                if not match:
                    match = re.search(r"(\d{4})\s*$", text.replace("\n", " "))
                if match:
                    return match.group(1)
        except Exception:
            pass
        try:
            text = page.locator("body").inner_text(timeout=3000)
            match = re.search(
                r"CMR[\s\S]{0,80}?[•·*\s]{2,}(\d{4})",
                text,
                re.IGNORECASE,
            )
            if match:
                return match.group(1)
            match = re.search(
                r"CMR\s+Mastercard[^\d]{0,40}(\d{4})",
                text,
                re.IGNORECASE,
            )
            if match:
                return match.group(1)
        except Exception:
            pass
        return None

    def _wait_for_cmr_content(self) -> None:
        page = self._ensure_page()
        try:
            page.wait_for_function(
                """(host) => {
                    const el = document.querySelector(host);
                    if (!el || !el.shadowRoot) return false;
                    function collect(root) {
                        const found = [root];
                        for (const child of Array.from(root.querySelectorAll("*"))) {
                            if (child.shadowRoot) found.push(...collect(child.shadowRoot));
                        }
                        return found;
                    }
                    return collect(el.shadowRoot).some(
                        r => r.querySelectorAll("table tbody tr td").length > 0
                    );
                }""",
                arg=self.CMR_HOST,
                timeout=self.CMR_WAIT_MS,
            )
        except PlaywrightTimeoutError:
            logger.warning("Timeout waiting for CMR shadow DOM tables.")
        page.wait_for_timeout(500)

    def _click_cmr_billed_tab(self) -> bool:
        page = self._ensure_page()
        result = page.evaluate(
            """({ host, radioId }) => {
                const shadowEl = document.querySelector(host);
                const roots = [];
                if (shadowEl && shadowEl.shadowRoot) roots.push(shadowEl.shadowRoot);
                roots.push(document);

                for (const root of roots) {
                    const radio = root.querySelector("#" + radioId);
                    if (radio) {
                        radio.checked = true;
                        radio.dispatchEvent(new Event("change", { bubbles: true }));
                        radio.click();
                        const label = root.querySelector('label[for="' + radio.id + '"]')
                            || radio.closest("label");
                        if (label) label.click();
                        return true;
                    }
                }
                for (const root of roots) {
                    for (const label of Array.from(root.querySelectorAll("label"))) {
                        if (!(label.innerText || "").toLowerCase().includes("facturado")) {
                            continue;
                        }
                        const forId = label.getAttribute("for");
                        const radio = forId
                            ? root.querySelector("#" + forId)
                            : label.querySelector('input[type="radio"]');
                        if (radio) {
                            radio.checked = true;
                            radio.dispatchEvent(new Event("change", { bubbles: true }));
                            radio.click();
                        }
                        label.click();
                        return true;
                    }
                }
                return false;
            }""",
            {"host": self.CMR_HOST, "radioId": "invoicedMovements"},
        )
        return bool(result)

    def _paginate_cmr_movements(
        self, *, billed: bool, account_id: Optional[str], status: str
    ) -> List[MovementModel]:
        page = self._ensure_page()
        all_movements: List[MovementModel] = []
        seen: set[tuple] = set()

        for page_idx in range(self.MAX_PAGES):
            result = page.evaluate(
                self._CMR_PAGE_JS,
                {"host": self.CMR_HOST, "isBilled": billed},
            )
            rows = result.get("rows") or []
            logger.debug(f"CMR page {page_idx + 1}: {len(rows)} rows ({status}).")

            for row in rows:
                movement = self._row_to_credit_movement(row, account_id, status)
                if not movement:
                    continue
                key = (
                    movement.date.isoformat(),
                    movement.description,
                    str(movement.amount),
                    status,
                )
                if key in seen:
                    continue
                seen.add(key)
                all_movements.append(movement)

            if not result.get("clicked"):
                break

            prev_row = result.get("firstRow") or ""
            changed = False
            try:
                page.wait_for_function(
                    """({ host, prev, billed }) => {
                        const el = document.querySelector(host);
                        const topRoot = (el && el.shadowRoot) || document;
                        function collect(root) {
                            const found = root === document || root === topRoot
                                ? (root === document ? [] : [root])
                                : [root];
                            const base = root === document ? document : root;
                            for (const child of Array.from(base.querySelectorAll("*"))) {
                                if (child.shadowRoot) found.push(...collect(child.shadowRoot));
                            }
                            return found;
                        }
                        const roots = collect(topRoot);
                        if (billed) {
                            for (const r of roots) {
                                for (const tbl of Array.from(r.querySelectorAll("table"))) {
                                    const hdr = (
                                        (tbl.querySelector("thead, tr:first-child") || {})
                                            .innerText || ""
                                    ).toLowerCase();
                                    if (!hdr.includes("fecha de compra")) continue;
                                    const cells = tbl.querySelectorAll(
                                        "tbody tr:first-child td"
                                    );
                                    if (cells.length > 0) {
                                        const sig = Array.from(cells)
                                            .map(c => (c.innerText || "").trim())
                                            .join("|");
                                        return sig !== prev && sig !== "";
                                    }
                                }
                            }
                        }
                        for (const root of roots) {
                            const cells = root.querySelectorAll(
                                "table tbody tr:first-child td"
                            );
                            if (cells.length > 0) {
                                const sig = Array.from(cells)
                                    .map(c => (c.innerText || "").trim())
                                    .join("|");
                                return sig !== prev && sig !== "";
                            }
                        }
                        return false;
                    }""",
                    arg={"host": self.CMR_HOST, "prev": prev_row, "billed": billed},
                    timeout=15000,
                )
                changed = True
            except PlaywrightTimeoutError:
                changed = False

            if not changed:
                break
            page.wait_for_timeout(300)

        return all_movements

    _CMR_PAGE_JS = """
    ({ host, isBilled }) => {
        const shadowEl = document.querySelector(host);
        const topRoot = (shadowEl && shadowEl.shadowRoot) || document;

        function collect(root) {
            const found = root === document ? [] : [root];
            const base = root === document ? document : root;
            for (const el of Array.from(base.querySelectorAll("*"))) {
                if (el.shadowRoot) found.push(...collect(el.shadowRoot));
            }
            return found;
        }
        const roots = collect(topRoot);

        const allTables = roots.flatMap(
            r => Array.from(r.querySelectorAll("table"))
        );
        function isVisible(t) {
            const rect = t.getBoundingClientRect();
            return rect.width > 0 || rect.height > 0;
        }

        let tablesToUse = isBilled
            ? allTables.filter(t => {
                if (!isVisible(t)) return false;
                const hdr = (
                    (t.querySelector("thead, tr:first-child") || {}).innerText || ""
                ).toLowerCase();
                return (
                    hdr.includes("fecha de compra")
                    || hdr.includes("monto total")
                    || hdr.includes("cuota a pagar")
                );
            })
            : allTables.filter(t => isVisible(t));

        if (tablesToUse.length === 0) {
            tablesToUse = allTables.filter(
                t => isVisible(t) && !t.closest("app-last-movements")
            );
        }

        const rows = [];
        for (const table of tablesToUse) {
            for (const row of Array.from(table.querySelectorAll("tbody tr"))) {
                const cells = row.querySelectorAll("td");
                if (cells.length < 4) continue;
                const texts = Array.from(cells).map(c => (c.innerText || "").trim());
                const dateMatch = (texts[0] || "").match(/(\\d{1,2}\\/\\d{1,2}\\/\\d{2,4})/);
                const pendingImg = row.querySelector(
                    "td:first-child img[alt*='pendiente'], td:first-child .td-time-img"
                );
                if (!dateMatch && !pendingImg && texts[0] !== "") continue;
                const date = dateMatch ? dateMatch[1] : "";
                const description = texts[1] || "";
                const totalText = texts[3] || "";
                const cuotaText = texts[5] || "";
                const montoText = cuotaText || totalText;
                const isNeg = montoText.includes("-$");
                const amountMatch = montoText.match(/\\$\\s*([\\d.,]+)/);
                let amountStr = "";
                if (amountMatch) {
                    amountStr = (isNeg ? "" : "-") + amountMatch[1];
                }
                if (description && amountStr) {
                    rows.push({
                        date,
                        description,
                        amount_str: amountStr,
                        owner: texts[2] || "",
                        installments: texts[4] || "",
                    });
                }
            }
        }

        let firstRow = "";
        if (isBilled) {
            outer: for (const r of roots) {
                for (const tbl of Array.from(r.querySelectorAll("table"))) {
                    const hdr = (
                        (tbl.querySelector("thead, tr:first-child") || {}).innerText || ""
                    ).toLowerCase();
                    if (!hdr.includes("fecha de compra")) continue;
                    const cells = tbl.querySelectorAll("tbody tr:first-child td");
                    if (cells.length > 0) {
                        firstRow = Array.from(cells)
                            .map(c => (c.innerText || "").trim())
                            .join("|");
                        break outer;
                    }
                }
            }
        }
        if (!firstRow) {
            for (const r of roots) {
                const cells = r.querySelectorAll("table tbody tr:first-child td");
                if (cells.length > 0) {
                    firstRow = Array.from(cells)
                        .map(c => (c.innerText || "").trim())
                        .join("|");
                    break;
                }
            }
        }

        let clicked = false;
        for (const root of roots) {
            if (clicked) break;
            for (const btn of Array.from(root.querySelectorAll(".btn-pagination, button"))) {
                if (btn.disabled) continue;
                const img = btn.querySelector("img");
                const imgAlt = ((img && img.getAttribute("alt")) || "").toLowerCase();
                const imgSrc = (img && img.getAttribute("src")) || "";
                const label = (
                    btn.getAttribute("aria-label") || btn.innerText || ""
                ).toLowerCase();
                const isNext =
                    imgAlt.includes("avanzar")
                    || imgAlt.includes("siguiente")
                    || imgAlt.includes("next")
                    || imgSrc.includes("right-arrow")
                    || imgSrc.includes("arrow-right")
                    || imgSrc.includes("next")
                    || label.includes("siguiente")
                    || label.includes("next")
                    || label.includes("avanzar");
                if (isNext) {
                    btn.click();
                    clicked = true;
                    break;
                }
            }
        }

        return { rows, firstRow, clicked };
    }
    """

    def _row_to_credit_movement(
        self, row: dict, account_id: Optional[str], status: str
    ) -> Optional[MovementModel]:
        date_str = row.get("date") or ""
        if not date_str:
            # Pending confirmation rows without a date — skip for now.
            return None
        date = parse_chilean_date(date_str.replace("-", "/"))
        if not date:
            return None

        description = (row.get("description") or "").strip()
        amount = parse_chilean_amount(row.get("amount_str"))
        if not description or amount == 0:
            return None

        # CMR charges are expenses; open-banking already signs amount_str.
        if amount > 0:
            amount = -amount

        return MovementModel(
            date=date,
            description=description,
            amount=amount,
            currency="CLP",
            transaction_type="Cargo",
            account_id=account_id,
            account_type="credito",
            raw_data={
                "status": status,
                "date_str": date_str,
                "amount_str": row.get("amount_str"),
                "owner": row.get("owner"),
                "installments": row.get("installments"),
                "product": "cmr",
            },
        )
