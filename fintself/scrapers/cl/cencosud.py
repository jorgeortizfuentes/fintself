from typing import List

from fintself.core.exceptions import DataExtractionError, LoginError
from fintself.core.models import MovementModel
from fintself.scrapers.base import BaseScraper
from fintself.utils.logging import logger
from fintself.utils.parsers import parse_chilean_amount, parse_chilean_date


class CencosudScraper(BaseScraper):
    """Scraper to extract movements from the Cencosud card."""

    LOGIN_URL = "https://www.mitarjetacencosud.cl/login"
    DASHBOARD_URL = "https://www.mitarjetacencosud.cl/dashboard"

    def _get_bank_id(self) -> str:
        return "cl_cencosud"

    def _login(self) -> None:
        """Performs the login on mitarjetacencosud.cl."""
        assert self.user is not None, "User must be provided"
        assert self.password is not None, "Password must be provided"

        self._ensure_page()
        logger.info("Logging into Cencosud.")
        self._navigate(self.LOGIN_URL)
        self._save_debug_info("01_login_page")

        logger.info("Entering credentials.")
        self._type("#webt-login-prelogin-input-rut", self.user, delay=100)
        self._fill("#webt-login-prelogin-input-password", self.password)
        self._save_debug_info("02_credentials_entered")

        logger.info("Submitting login form.")
        self._click("#webt-login-prelogin-button-continue")

        # Cencosud frequently triggers an image captcha after the login click.
        # In visible mode the user has to solve it by hand, so we wait for the
        # dashboard to load with a generous timeout (3 min) and key off the
        # same selector the rest of the scraper relies on.
        logger.info(
            "Waiting for dashboard to load (solve any captcha manually if it appears)."
        )
        try:
            self._wait_for_selector('div[code="MOVIMIENTOS"]', timeout_override=180000)
            self._save_debug_info("03_login_success")
            logger.info("Login to Cencosud successful.")
        except DataExtractionError:
            self._save_debug_info("login_failed")
            raise LoginError(
                "Timeout after login to Cencosud. Check credentials or solve "
                "the captcha within 3 minutes when running in visible mode."
            )

    def _scrape_movements(self) -> List[MovementModel]:
        """Orchestrates the extraction of billed and unbilled movements."""
        self._close_popup()
        unbilled = self._extract_unbilled_movements()

        logger.info("Navigating to dashboard to extract billed movements.")
        self._navigate(self.DASHBOARD_URL, timeout_override=15000)
        self._wait_for_selector('div[code="MOVIMIENTOS"]', timeout_override=20000)
        self._close_popup()

        billed = self._extract_billed_movements()

        all_movements = unbilled + billed
        logger.info(f"Scraping completed. Total movements: {len(all_movements)}")
        return all_movements

    def _close_popup(self) -> None:
        """Closes popups if they appear."""
        page = self._ensure_page()
        popup_close_button = page.locator(".dy-lb-close")
        try:
            if popup_close_button.is_visible(timeout=5000):
                self._click(popup_close_button)
                logger.info("Popup closed.")
        except Exception:
            logger.debug("Popup not found or could not be closed.")

    def _extract_unbilled_movements(self) -> List[MovementModel]:
        """Extracts 'Unbilled' movements."""
        page = self._ensure_page()
        logger.info("Extracting unbilled movements...")
        self._click('div[code="MOVIMIENTOS"]')
        self._click('role=link[name="No facturados"]')
        page.wait_for_load_state("networkidle")
        self._save_debug_info("04_unbilled_page")

        try:
            account_id_selector = "div.header__options span.select--card__number"
            element = self._wait_for_selector(
                account_id_selector, timeout_override=15000
            )
            account_id = element.inner_text().strip()
            logger.info(f"Found account ID: {account_id}")
        except Exception as e:
            logger.warning(
                f"Could not extract account ID, will be left blank. Error: {e}"
            )
            self._save_debug_info("account_id_extraction_failed_unbilled")
            account_id = "N/A"

        rows = page.locator(".national-movements-content-table .table__body__row").all()
        movements = []
        for row in rows:
            try:
                date_str = row.locator(
                    ".table__body__row__image-column.row-1 > div"
                ).inner_text()
                desc = row.locator(".table__body__row__column.row-2 > div").inner_text()
                amount_str = row.locator(
                    ".table__body__row__image-column.row-5 > div"
                ).inner_text()

                date = parse_chilean_date(date_str)
                if not date:
                    continue

                movements.append(
                    MovementModel(
                        date=date,
                        description=desc,
                        amount=-parse_chilean_amount(amount_str),
                        currency="CLP",
                        transaction_type="Cargo",
                        account_id=account_id,
                        account_type="credito",
                        raw_data={
                            "status": "unbilled",
                            "date_str": date_str,
                            "amount_str": amount_str,
                            "full_account_id": account_id,
                        },
                    )
                )
            except Exception as e:
                logger.warning(f"Error parsing unbilled row: {e}")

        logger.info(f"Extracted {len(movements)} unbilled movements.")
        return movements

    def _extract_billed_movements(self) -> List[MovementModel]:
        """Extracts 'Billed' movements."""
        page = self._ensure_page()
        logger.info("Extracting billed movements...")
        self._click('div[code="MOVIMIENTOS"]')
        self._click('role=link[name="Facturados"]')
        page.wait_for_load_state("networkidle")
        self._save_debug_info("05_billed_page")

        try:
            account_id_selector = "div.header__options span.select--card__number"
            element = self._wait_for_selector(
                account_id_selector, timeout_override=15000
            )
            account_id = element.inner_text().strip()
            logger.info(f"Found account ID: {account_id}")
        except Exception as e:
            logger.warning(
                f"Could not extract account ID, will be left blank. Error: {e}"
            )
            self._save_debug_info("account_id_extraction_failed_billed")
            account_id = "N/A"

        rows = page.locator(".invoice-table-wrapper .invoice-table__body__row").all()
        movements = []
        for row in rows:
            try:
                date_str = row.locator(".invoice-row-1 div:nth-child(2)").inner_text()
                desc = row.locator(".invoice-row-2 > div").inner_text()
                amount_str = row.locator(
                    ".invoice-row-5 div > div:last-child"
                ).inner_text()

                date = parse_chilean_date(date_str)
                if not date:
                    continue

                movements.append(
                    MovementModel(
                        date=date,
                        description=desc,
                        amount=-parse_chilean_amount(amount_str),
                        currency="CLP",
                        transaction_type="Cargo",
                        account_id=account_id,
                        account_type="credito",
                        raw_data={
                            "status": "billed",
                            "date_str": date_str,
                            "amount_str": amount_str,
                            "full_account_id": account_id,
                        },
                    )
                )
            except Exception as e:
                logger.warning(f"Error parsing billed row: {e}")

        logger.info(f"Extracted {len(movements)} billed movements.")
        return movements
