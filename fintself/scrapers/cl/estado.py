from typing import List

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from fintself.core.exceptions import DataExtractionError, LoginError
from fintself.core.models import MovementModel
from fintself.scrapers.base import BaseScraper
from fintself.utils.logging import logger
from fintself.utils.parsers import parse_chilean_amount, parse_chilean_date


class BancoEstadoScraper(BaseScraper):
    """Scraper to extract movements from Banco Estado Chile - CuentaRUT."""

    LOGIN_URL = "https://www.bancoestado.cl/content/bancoestado-public/cl/es/home/home.html#/login"

    def _get_bank_id(self) -> str:
        return "cl_estado"

    def _login(self) -> None:
        """Implements the login logic for Banco Estado."""
        assert self.user is not None, "User must be provided"
        assert self.password is not None, "Password must be provided"

        page = self._ensure_page()
        logger.info("Logging into Banco Estado.")
        self._navigate(self.LOGIN_URL, timeout_override=90000)
        self._save_debug_info("01_login_page")

        # Wait for login form to load
        logger.info("Waiting for login form.")
        try:
            self._wait_for_selector("input#rut", timeout_override=20000)
        except DataExtractionError:
            self._save_debug_info("login_form_not_found")
            raise LoginError("Could not find Banco Estado login form")

        self._save_debug_info("02_login_form_found")

        # Remove readonly attribute from RUT field using JavaScript
        logger.info("Preparing RUT field.")
        page.evaluate("""
            const rutInput = document.getElementById('rut');
            if (rutInput) {
                rutInput.removeAttribute('readonly');
            }
        """)

        # Fill credentials
        logger.info("Entering credentials.")
        # Clean RUT: remove dots and hyphens
        clean_rut = self.user.replace(".", "").replace("-", "")
        self._type(page.locator("input#rut"), clean_rut, delay=120)
        self._type(page.locator("input#pass"), self.password, delay=120)
        self._save_debug_info("03_credentials_entered")

        # Wait a bit to see if a modal appears
        page.wait_for_timeout(2000)

        # Close any modal that might be blocking the login button
        modal_close_selectors = [
            ".msd-modalhome--container-content-close",
            ".msd-modalhome--container .close",
            'button[aria-label="Cerrar"]',
            'button[aria-label="Cerrar modal"]',
        ]

        for selector in modal_close_selectors:
            try:
                close_btn = page.locator(selector).first
                if close_btn.is_visible(timeout=2000):
                    logger.info(f"Closing modal with selector: {selector}")
                    self._click(close_btn, force=True, skip_hover=True)
                    page.wait_for_timeout(1000)
                    break
            except Exception:
                continue

        self._save_debug_info("03a_before_login_click")

        # Submit form by clicking the button
        logger.info("Submitting login form.")
        login_button = page.locator("button#btnLogin")
        self._click(login_button)

        # Wait for login to complete - the login panel should close
        logger.info("Waiting for post-login.")
        try:
            page.wait_for_function(
                """
                () => {
                    const loginPanel = document.getElementById('sidenavLoginApp');
                    return !loginPanel || !loginPanel.classList.contains('open-sidenav');
                }
            """,
                timeout=30000,
            )
            logger.info("Login panel closed successfully")
        except PlaywrightTimeoutError:
            self._save_debug_info("post_login_timeout")
            raise LoginError(
                "Timeout waiting for post-login. Credentials might be incorrect."
            )

        # Wait a bit for post-login modals/announcements
        page.wait_for_timeout(3000)

        # Close any post-login announcement modal
        close_btn_selectors = [
            ".msd-modalhome--container-content-close",
            ".msd-sideBar__container button.close",
            'button[aria-label="Cerrar"]',
            'button[aria-label="Cerrar modal"]',
        ]

        for selector in close_btn_selectors:
            try:
                close_btn = page.locator(selector).first
                if close_btn.is_visible(timeout=2000):
                    logger.info(f"Closing post-login modal with selector: {selector}")
                    self._click(close_btn, force=True, skip_hover=True)
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                continue

        self._save_debug_info("04_login_success")
        logger.info("Login to Banco Estado successful.")

        # Make sure no announcement bars or overlays remain before continuing
        self._dismiss_annoyances(context="post_login")

    def _scrape_movements(self) -> List[MovementModel]:
        """Extracts movements from CuentaRUT."""
        page = self._ensure_page()
        all_movements: List[MovementModel] = []

        logger.info("Navigating to CuentaRUT movements")

        # Wait for dashboard to load
        page.wait_for_timeout(5000)
        self._save_debug_info("05_dashboard")

        # Close any banners that might block interactions on the dashboard
        self._dismiss_annoyances(context="dashboard")

        # Click on "Movimientos" button for CuentaRUT
        def _click_movements_button() -> None:
            button_selectors = [
                'button[aria-label*="movimientos de CuentaRUT"]',
                'button[aria-label*="Saldos y movimientos de CuentaRUT"]',
                'button:has-text("Ver movimientos")',
                'button:has-text("Ver detalle")',
            ]

            for button_selector in button_selectors:
                locator = page.locator(button_selector).first
                try:
                    if locator.is_visible(timeout=2000):
                        self._click(locator, force=True, skip_hover=True)
                        logger.info(
                            f"Clicked Movimientos button for CuentaRUT using selector: {button_selector}"
                        )
                        return
                except Exception:
                    continue
            raise DataExtractionError("Movimientos button for CuentaRUT not found")

        try:
            _click_movements_button()
            self._save_debug_info("06_clicked_movimientos")

            # Wait for movements page to load
            page.wait_for_timeout(8000)
            self._save_debug_info("07_movements_page")

        except Exception as e:
            logger.warning(f"First attempt to navigate to movements failed: {e}")
            self._dismiss_annoyances(context="dashboard_retry")
            try:
                _click_movements_button()
                self._save_debug_info("06_clicked_movimientos_retry")
                page.wait_for_timeout(8000)
                self._save_debug_info("07_movements_page")
            except Exception as retry_error:
                logger.error(f"Error navigating to movements: {retry_error}")
                self._save_debug_info("navigation_error")
                raise DataExtractionError(
                    f"Could not navigate to movements: {retry_error}"
                )

        # Extract movements from the page
        try:
            movements = self._extract_movements_from_page()
            all_movements.extend(movements)
        except Exception as e:
            logger.error(f"Error extracting movements: {e}")
            self._save_debug_info("extraction_error")
            raise DataExtractionError(f"Could not extract movements: {e}")

        logger.info(f"Total movements extracted: {len(all_movements)}")
        return all_movements

    def _dismiss_annoyances(self, context: str = "") -> None:
        """Attempts to close or remove overlays that block interactions."""
        page = self._ensure_page()

        try:
            page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass

        selectors_to_try = [
            ".msd-modalhome--container-content-close",
            ".msd-modalhome--container .close",
            'button[aria-label="Cerrar"]',
            'button[aria-label="Cerrar modal"]',
            'button[aria-label="Close"]',
            'button[aria-label="Close Infobar"]',
            'button:has-text("No por ahora")',
            ".msd-sideBar__container__header span.close",
            ".msd-sideBar__container button.close",
        ]

        for selector in selectors_to_try:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=1500):
                    context_suffix = f" ({context})" if context else ""
                    logger.info(
                        f"Closing overlay{context_suffix} with selector: {selector}"
                    )
                    self._click(locator, force=True, skip_hover=True)
                    page.wait_for_timeout(1200)
            except Exception:
                continue

        # Press Escape as a generic dismissal action
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        except Exception:
            pass

        # Remove stubborn overlays that do not respond to clicks
        try:
            page.evaluate(
                """
                const selectors = [
                    '#remove-modal',
                    '#evg-infobar-with-user-attr',
                    '.evg-infobar-middle',
                    '.evg-btn-dismissal',
                    'msd-side-nav.msd-holidays-type-2',
                ];
                selectors.forEach((selector) => {
                    document.querySelectorAll(selector).forEach((element) => {
                        element.remove();
                    });
                });
                """
            )
        except Exception:
            pass

    def _extract_movements_from_page(self) -> List[MovementModel]:
        """Extracts movements from the movements page."""
        page = self._ensure_page()
        movements: List[MovementModel] = []

        logger.info("Extracting movements from page")

        # Wait for either the movements table or the empty-state container.
        try:
            page.wait_for_selector(
                "table tbody tr, .info_no_movements",
                timeout=15000,
            )
        except PlaywrightTimeoutError:
            logger.warning(
                "Neither a movements table nor an empty-state message appeared in time"
            )
            self._save_debug_info("no_table_found")
            return []

        # Confirmed empty state: bank explicitly tells us there are no movements
        # in the current period (anchored to a specific container, not body).
        try:
            empty_state = page.locator(".info_no_movements").first
            if empty_state.is_visible(timeout=1000):
                logger.info(
                    "Banco Estado reports no movements for the current period "
                    "on this CuentaRUT (historical movements may live in Cartolas/PDFs)."
                )
                return []
        except Exception:
            pass

        # Otherwise we expect a real table with rows
        try:
            table = page.locator("table").first
            if not table.is_visible(timeout=5000):
                logger.warning("Movements table not visible")
                return []
        except Exception:
            logger.warning("Could not find movements table")
            return []

        self._save_debug_info("08_movements_table_found")

        # Extract rows from table
        try:
            rows = page.locator("table tbody tr").all()
            logger.info(f"Found {len(rows)} rows in movements table")

            for i, row in enumerate(rows):
                try:
                    # Get all cells in the row
                    cells = row.locator("td").all()

                    if len(cells) < 5:
                        logger.debug(f"Row {i + 1} has less than 5 cells, skipping")
                        continue

                    # Extract data from cells
                    # Structure: Tags | Fecha | Descripción | Canal | Abonos/Cargos | Saldos | Descargar
                    # Index:     0    | 1     | 2           | 3     | 4             | 5      | 6
                    date_str = cells[1].inner_text().strip()
                    description = cells[2].inner_text().strip()
                    canal = cells[3].inner_text().strip()
                    amount_str = cells[4].inner_text().strip()

                    # Parse date
                    if not date_str:
                        logger.warning(f"Empty date in row {i + 1}, skipping")
                        continue

                    date = parse_chilean_date(date_str)
                    if not date:
                        logger.warning(
                            f"Could not parse date '{date_str}' in row {i + 1}, skipping"
                        )
                        continue

                    # Parse amount
                    if not amount_str:
                        logger.debug(f"Row {i + 1} has no amount, skipping")
                        continue

                    amount = parse_chilean_amount(amount_str)
                    if amount.is_zero():
                        logger.debug(f"Row {i + 1} has zero amount, skipping")
                        continue

                    # Determine transaction type based on amount sign
                    transaction_type = "Cargo" if amount < 0 else "Abono"

                    # Create movement
                    movement = MovementModel(
                        date=date,
                        description=description,
                        amount=amount,
                        currency="CLP",
                        transaction_type=transaction_type,
                        account_id="cuenta_rut",
                        account_type="debito",
                        raw_data={
                            "date_str": date_str,
                            "amount_str": amount_str,
                            "canal": canal,
                            "row_index": i + 1,
                        },
                    )
                    movements.append(movement)
                    logger.debug(
                        f"Added movement: {description[:50]}... Amount: {amount} CLP"
                    )

                except Exception as e:
                    logger.warning(f"Failed to parse row {i + 1}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error processing table rows: {e}")
            self._save_debug_info("table_processing_error")

        logger.info(f"Extracted {len(movements)} movements")
        return movements
