import re
from typing import List, Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect

from fintself.core.exceptions import DataExtractionError, LoginError
from fintself.core.models import MovementModel
from fintself.scrapers.base import BaseScraper
from fintself.utils.logging import logger
from fintself.utils.parsers import parse_chilean_amount, parse_chilean_date


class SantanderScraper(BaseScraper):
    """Scraper to extract movements from Banco Santander Chile."""

    LOGIN_URL = "https://banco.santander.cl/personas"
    DASHBOARD_URL = "https://mibanco.santander.cl/UI.Web.HB/Private_new/frame/#/private/home/main/resumen"
    UNBILLED_URL = "https://mibanco.santander.cl/UI.Web.HB/Private_new/frame/#/private/Saldos_TC/main/bill"
    BILLED_URL = "https://mibanco.santander.cl/UI.Web.HB/Private_new/frame/#/private/Saldos_TC/main/billed"

    def _get_bank_id(self) -> str:
        return "cl_santander"

    def _login(self) -> None:
        """Implements the login logic for Santander Chile."""
        assert self.user is not None, "User must be provided"
        assert self.password is not None, "Password must be provided"

        page = self._ensure_page()
        logger.info("Navigating to Santander login page.")
        self._navigate(self.LOGIN_URL, timeout_override=90000)
        self._save_debug_info("01_login_page")

        logger.info("Clicking on 'Ingresar al sitio privado' button.")
        self._click('role=button[name="Ingresar al sitio privado"]')

        logger.info("Waiting for login iframe.")
        try:
            login_frame = page.frame_locator("#login-frame")
            # Wait for an element inside the frame to ensure it's loaded
            login_frame.locator('role=textbox[name="RUT"]').wait_for(
                state="visible", timeout=20000
            )
        except PlaywrightTimeoutError:
            self._save_debug_info("login_iframe_timeout")
            raise LoginError("Timeout waiting for Santander login iframe.")

        logger.info("Entering credentials.")
        self._type(
            login_frame.locator('role=textbox[name="RUT"]'), self.user, delay=120
        )
        self._type(
            login_frame.locator('role=textbox[name="Clave"]'), self.password, delay=120
        )
        self._save_debug_info("02_credentials_entered")

        logger.info("Submitting login form.")
        self._click(login_frame.locator('role=button[name="Ingresar"]'))

        logger.info("Waiting for post-login confirmation.")
        try:
            expect(page.locator("h3:has-text('Hola')")).to_be_visible(timeout=40000)
            self._save_debug_info("03_login_success")
            logger.info("Login to Santander successful.")
        except PlaywrightTimeoutError:
            self._save_debug_info("post_login_error")
            raise LoginError(
                "Timeout or error after login to Santander. Credentials might be incorrect."
            )

    def _get_all_credit_cards_from_carousel(self) -> List[str]:
        """Extracts all credit card IDs from the carousel on the credit card pages.

        Returns:
            List of card IDs in format '**** XXXX'
        """
        page = self._ensure_page()
        logger.info("Detecting all credit cards in carousel...")

        try:
            # Wait for carousel to load
            page.wait_for_selector("lib-carousel", timeout=10000)
            page.wait_for_timeout(2000)  # Wait for carousel to initialize

            # Get all slides in the carousel
            slides = page.locator("lib-carousel .swiper-slide").all()
            card_ids = []

            for slide in slides:
                try:
                    # Extract card number from the slide
                    # The format is "* XXXX" inside a <p> with class "product"
                    product_text = slide.locator("p.product").inner_text(timeout=2000)
                    # Extract the 4 digits
                    match = re.search(r"\*\s*(\d{4})", product_text)
                    if match:
                        card_id = f"**** {match.group(1)}"
                        if card_id not in card_ids:
                            card_ids.append(card_id)
                            logger.info(f"Found card in carousel: {card_id}")
                except Exception as e:
                    logger.warning(f"Could not extract card number from slide: {e}")
                    continue

            logger.info(f"Total cards found in carousel: {len(card_ids)}")
            return card_ids

        except Exception as e:
            logger.warning(f"Could not detect cards from carousel: {e}")
            self._save_debug_info("carousel_detection_failed")
            return []

    def _navigate_to_card_in_carousel(self, target_card_index: int) -> bool:
        """Navigates to a specific card in the carousel using pagination bullets.

        Pagination bullets (aria-label="Go to slide N") are more reliable than
        the next button, which can briefly report disabled while the swiper is
        still initialising after a fresh navigation.

        Args:
            target_card_index: Zero-based index of the card to navigate to

        Returns:
            True if navigation was successful, False otherwise
        """
        page = self._ensure_page()
        logger.info(f"Navigating to card at index {target_card_index} in carousel...")

        try:
            page.wait_for_selector("lib-carousel", timeout=10000)
            # Wait until the pagination bullets are rendered — that's the signal
            # that swiper has fully initialised.
            page.wait_for_selector(
                "lib-carousel .swiper-pagination-bullet",
                timeout=10000,
            )

            slide_label = f"Go to slide {target_card_index + 1}"
            bullet = page.locator(
                f'lib-carousel span.swiper-pagination-bullet[aria-label="{slide_label}"]'
            ).first

            try:
                bullet.wait_for(state="visible", timeout=5000)
            except PlaywrightTimeoutError:
                logger.warning(
                    f"Pagination bullet for slide {target_card_index + 1} not found. "
                    f"Carousel likely has fewer cards on this view."
                )
                return False

            self._click(bullet, force=True, skip_hover=True)
            page.wait_for_timeout(1500)  # Wait for slide transition

            logger.info(f"Successfully navigated to card at index {target_card_index}")
            return True

        except Exception as e:
            logger.warning(
                f"Could not navigate to card at index {target_card_index}: {e}"
            )
            self._save_debug_info(
                f"carousel_navigation_failed_index_{target_card_index}"
            )
            return False

    def _extract_and_store_account_ids(self) -> None:
        """Scrapes all account IDs from the dashboard, storing them in self.account_ids."""
        page = self._ensure_page()
        logger.info("Extracting and storing all account IDs from the dashboard.")
        # We assume we are on the dashboard after a successful login.
        self._save_debug_info("dashboard_for_ids")

        # --- Extract Checking Account IDs ---
        try:
            account_divs = page.locator("#cuentas div.box-product").all()
            for div in account_divs:
                name_p = div.locator("div.datos p").first.inner_text(timeout=2000)
                number_raw = div.locator("div.datos p").nth(1).inner_text(timeout=2000)
                number_clean = re.sub(r"[^\d]", "", number_raw)

                if "dólar" in name_p.lower():
                    self.account_ids["corriente"]["USD"] = number_clean
                    logger.info(f"Stored checking account USD: {number_clean}")
                else:
                    self.account_ids["corriente"]["CLP"] = number_clean
                    logger.info(f"Stored checking account CLP: {number_clean}")
        except Exception as e:
            logger.warning(f"Could not extract checking account IDs: {e}")
            self._save_debug_info("checking_id_extraction_failed")

        # --- Extract Credit Card IDs from Dashboard ---
        # Note: This will be complemented by extracting IDs from the carousel later
        try:
            card_divs = page.locator("#tarjetas-creditos div.box-product").all()
            if card_divs:
                first_card = card_divs[0]
                card_number_p = first_card.locator("p:has-text('*')")
                card_text = card_number_p.inner_text(timeout=2000)
                match = re.search(r"\*\s*(\d{4})", card_text)
                if match:
                    card_id = f"**** {match.group(1)}"
                    # Assume same ID for both currencies, as the site seems to have one context per card.
                    self.account_ids["credito"]["CLP"] = card_id
                    self.account_ids["credito"]["USD"] = card_id
                    logger.info(f"Stored credit card ID for CLP/USD: {card_id}")
        except Exception as e:
            logger.warning(f"Could not extract credit card IDs: {e}")
            self._save_debug_info("credit_id_extraction_failed")

    def _scrape_movements(self) -> List[MovementModel]:
        """Orchestrates the extraction of all types of card movements."""
        page = self._ensure_page()
        self.account_ids: dict = {"corriente": {}, "credito": {}}
        self._extract_and_store_account_ids()

        all_movements: List[MovementModel] = []

        # Navigate to unbilled page first to detect all cards
        logger.info("--- Navigating to Unbilled page to detect all cards ---")
        self._navigate(self.UNBILLED_URL, timeout_override=60000)
        self._save_debug_info("04_unbilled_page")

        # Detect all credit cards from the carousel
        credit_cards = self._get_all_credit_cards_from_carousel()

        if not credit_cards:
            logger.warning("No credit cards found in carousel. Trying old method...")
            # Fallback to old behavior - process only the current card
            all_movements.extend(
                self._extract_credit_card_movements("no_facturados", "CLP")
            )
            self._switch_currency_tab("USD")
            all_movements.extend(
                self._extract_credit_card_movements("no_facturados", "USD")
            )

            logger.info("--- Starting extraction of Billed CC ---")
            self._navigate(self.BILLED_URL, timeout_override=60000)
            self._save_debug_info("05_billed_page")
            self._switch_currency_tab("CLP")
            all_movements.extend(
                self._extract_credit_card_movements("facturados", "CLP")
            )
            self._switch_currency_tab("USD")
            all_movements.extend(
                self._extract_credit_card_movements("facturados", "USD")
            )
        else:
            # Process each card
            for card_index, card_id in enumerate(credit_cards):
                logger.info(f"\n{'=' * 60}")
                logger.info(
                    f"Processing card {card_index + 1}/{len(credit_cards)}: {card_id}"
                )
                logger.info(f"{'=' * 60}")

                # Update account_ids for this card
                self.account_ids["credito"]["CLP"] = card_id
                self.account_ids["credito"]["USD"] = card_id

                # --- Unbilled Movements ---
                logger.info(f"--- Extracting Unbilled movements for {card_id} ---")
                self._navigate(self.UNBILLED_URL, timeout_override=60000)
                page.wait_for_timeout(2000)  # Wait for page to fully load

                # Navigate to the specific card in the carousel
                if card_index > 0:
                    if not self._navigate_to_card_in_carousel(card_index):
                        logger.warning(
                            f"Could not navigate to card {card_id} in carousel. Skipping..."
                        )
                        continue

                self._save_debug_info(
                    f"04_unbilled_page_card_{card_id.replace(' ', '_')}"
                )

                # Extract CLP movements
                self._switch_currency_tab("CLP")
                all_movements.extend(
                    self._extract_credit_card_movements("no_facturados", "CLP")
                )

                # Extract USD movements
                self._switch_currency_tab("USD")
                all_movements.extend(
                    self._extract_credit_card_movements("no_facturados", "USD")
                )

                # --- Billed Movements ---
                logger.info(f"--- Extracting Billed movements for {card_id} ---")
                self._navigate(self.BILLED_URL, timeout_override=60000)
                page.wait_for_timeout(2000)  # Wait for page to fully load

                # Navigate to the specific card in the carousel
                if card_index > 0:
                    if not self._navigate_to_card_in_carousel(card_index):
                        logger.warning(
                            f"Could not navigate to card {card_id} in carousel. Skipping billed movements..."
                        )
                        continue

                self._save_debug_info(
                    f"05_billed_page_card_{card_id.replace(' ', '_')}"
                )

                # Extract CLP movements
                self._switch_currency_tab("CLP")
                all_movements.extend(
                    self._extract_credit_card_movements("facturados", "CLP")
                )

                # Extract USD movements
                self._switch_currency_tab("USD")
                all_movements.extend(
                    self._extract_credit_card_movements("facturados", "USD")
                )

                logger.info(f"Finished processing card {card_id}")

        # Debit Card (Checking Account)
        all_movements.extend(self._scrape_debit_card_movements())

        logger.info(
            f"Scraping completed. Total movements extracted: {len(all_movements)}"
        )
        return all_movements

    def _switch_currency_tab(self, currency: str) -> None:
        """Switches between the Pesos and Dólares tabs."""
        page = self._ensure_page()
        target_tab = "Dólares" if currency == "USD" else "Pesos"
        logger.info(f"Switching to currency tab: {target_tab}")
        try:
            self._click(f'button:has-text("{target_tab}")')
            expect(
                page.locator(f'mat-button-toggle:has-text("{target_tab}")')
            ).to_have_class(
                re.compile(r"mat-button-toggle-checked|actived"), timeout=15000
            )
            page.wait_for_timeout(2000)  # Wait for content to load
        except (PlaywrightTimeoutError, DataExtractionError):
            self._save_debug_info(f"currency_switch_timeout_{currency}")
            raise DataExtractionError(f"Timeout switching to {target_tab} tab.")

    def _get_account_id(self, account_type: str, currency: str) -> Optional[str]:
        """Retrieves a pre-scraped account ID from the stored dictionary."""
        try:
            account_id = self.account_ids.get(account_type, {}).get(currency)
            if account_id:
                logger.info(
                    f"Retrieved stored account ID for {account_type}/{currency}: {account_id}"
                )
                return account_id
            else:
                logger.warning(
                    f"No stored account ID found for {account_type}/{currency}."
                )
                return None
        except Exception as e:
            logger.error(
                f"Error retrieving stored account ID for {account_type}/{currency}: {e}"
            )
            return None

    def _scrape_debit_card_movements(self) -> List[MovementModel]:
        """Navigates to and scrapes debit card (checking account) movements."""
        page = self._ensure_page()
        all_debit_movements: List[MovementModel] = []

        logger.info(
            "\n--- Starting extraction of Debit Card (Checking Account) movements ---"
        )

        # CLP Checking Account
        try:
            logger.info("Navigating to dashboard for CLP Checking Account movements...")
            self._navigate(self.DASHBOARD_URL, timeout_override=60000)
            expect(page.locator("h3:has-text('Hola')")).to_be_visible(timeout=40000)
            self._save_debug_info("06_dashboard_for_debit_clp")

            logger.info("Navigating to CLP Checking Account movements...")
            # This locator targets the main checking account summary card.
            # The name contains a special character from an icon font.
            checking_account_card_locator = (
                page.get_by_role("region", name="Cuentas ")
                .get_by_role("emphasis")
                .first
            )
            self._click(checking_account_card_locator)
            self._wait_for_selector("text=Mis movimientos", timeout_override=20000)
            self._save_debug_info("07_debit_clp_movements_page")

            all_debit_movements.extend(
                self._extract_debit_card_movements(currency="CLP")
            )
        except Exception as e:
            logger.error(
                f"Error scraping CLP Checking Account movements: {e}", exc_info=True
            )
            self._save_debug_info("debit_clp_scraping_failed")

        # USD Checking Account
        try:
            logger.info("Navigating to dashboard for USD Checking Account movements...")
            self._navigate(self.DASHBOARD_URL, timeout_override=60000)
            expect(page.locator("h3:has-text('Hola')")).to_be_visible(timeout=40000)
            self._save_debug_info("08_dashboard_for_debit_usd")

            logger.info("Navigating to USD Checking Account movements...")
            # This locator targets the USD checking account by finding the second "Disponible" text
            # within the "Cuentas" region.
            usd_account_card_locator = (
                page.get_by_label("Cuentas").get_by_text("Disponible").nth(1)
            )

            if usd_account_card_locator.count() > 0:
                self._click(usd_account_card_locator)
                self._wait_for_selector("text=Mis movimientos", timeout_override=20000)
                self._save_debug_info("09_debit_usd_movements_page")
                all_debit_movements.extend(
                    self._extract_debit_card_movements(currency="USD")
                )
            else:
                logger.warning("USD Checking Account not found. Skipping.")
        except Exception as e:
            logger.error(
                f"Error scraping USD Checking Account movements: {e}", exc_info=True
            )
            self._save_debug_info("debit_usd_scraping_failed")

        return all_debit_movements

    def _extract_debit_card_movements(self, currency: str) -> List[MovementModel]:
        """Extracts debit card (checking account) movements from the current page."""
        page = self._ensure_page()
        logger.info(f"Extracting debit card movements in {currency}...")
        account_id = self._get_account_id(account_type="corriente", currency=currency)
        container_selector = "div.card.table-container.show"

        try:
            self._wait_for_selector(container_selector, timeout_override=30000)
        except DataExtractionError:
            logger.warning(
                f"No table container found for debit card movements in {currency}."
            )
            return []

        rows = page.locator(
            f"{container_selector} table.mat-table tbody tr.mat-row"
        ).all()
        if not rows:
            logger.info(f"No debit card movements found in {currency}.")
            return []

        movements = []
        last_date_str = ""

        for row in rows:
            raw_movement = {}
            if account_id:
                raw_movement["full_account_id"] = account_id

            try:
                date_text = (
                    row.locator("td.mat-column-date").inner_text(timeout=5000).strip()
                )
                if date_text:
                    last_date_str = date_text

                raw_movement["date"] = last_date_str
                raw_movement["description"] = (
                    row.locator("td.mat-column-detail").inner_text(timeout=5000).strip()
                )

                charge_str = (
                    row.locator("td.mat-column-amountCharge")
                    .inner_text(timeout=5000)
                    .strip()
                )
                payment_str = (
                    row.locator("td.mat-column-paymentAmount")
                    .inner_text(timeout=5000)
                    .strip()
                )

                # For debit, charges are negative, payments are positive.
                if charge_str and charge_str not in ["0", ""]:
                    raw_movement["amount"] = f"-{charge_str}"
                elif payment_str and payment_str not in ["0", ""]:
                    raw_movement["amount"] = payment_str
                else:
                    raw_movement["amount"] = "0"

                parsed_date = parse_chilean_date(raw_movement.get("date"))
                if not parsed_date:
                    continue

                amount = parse_chilean_amount(raw_movement.get("amount"))
                if amount.is_zero():
                    continue

                movements.append(
                    MovementModel(
                        date=parsed_date,
                        description=raw_movement.get("description", ""),
                        amount=amount,
                        currency=currency,
                        transaction_type="Cargo" if amount < 0 else "Abono",
                        account_id=account_id,
                        account_type="corriente",
                        raw_data=raw_movement,
                    )
                )
            except Exception as e:
                logger.warning(f"Error parsing a debit card movement row: {e}")
                continue

        logger.info(f"Extracted {len(movements)} debit card movements in {currency}.")
        return movements

    def _extract_credit_card_movements(
        self, status: str, currency: str
    ) -> List[MovementModel]:
        """Extracts credit card movements from the current page."""
        page = self._ensure_page()
        logger.info(f"Extracting {status} movements in {currency}...")
        account_id = self._get_account_id(account_type="credito", currency=currency)
        container_selector = (
            "div.card.table-container.show"
            if status == "no_facturados"
            else "div.container-tabla"
        )

        # Wait for either the movements container or the explicit empty-state message.
        # Using get_by_text avoids the :has-text ancestor-match pitfall.
        empty_state = page.get_by_text("no tienes movimientos", exact=False).first
        try:
            page.wait_for_function(
                """(sel) => {
                    const container = document.querySelector(sel);
                    if (container) return true;
                    const paragraphs = document.querySelectorAll('p, span');
                    for (const el of paragraphs) {
                        if (el.offsetParent !== null &&
                            el.textContent &&
                            el.textContent.toLowerCase().includes('no tienes movimientos')) {
                            return true;
                        }
                    }
                    return false;
                }""",
                arg=container_selector,
                timeout=30000,
            )
        except PlaywrightTimeoutError:
            logger.warning(
                f"Neither table container nor empty-state appeared for {status} movements in {currency}."
            )
            self._save_debug_info(f"no_container_{status}_{currency}")
            return []

        # If the bank explicitly says there are no movements, exit quietly.
        try:
            if empty_state.is_visible(timeout=1000):
                logger.info(
                    f"Santander reports no {status} movements in {currency} for this card."
                )
                return []
        except Exception:
            pass

        try:
            self._wait_for_selector(container_selector, timeout_override=5000)
        except DataExtractionError:
            logger.warning(
                f"No table container found for {status} movements in {currency}."
            )
            return []

        rows = page.locator(
            f"{container_selector} table.mat-table tbody tr.mat-row"
        ).all()
        if not rows:
            logger.info(f"No {status} movements found in {currency}.")
            return []

        movements = []
        last_date_str = ""

        for row in rows:
            raw_movement = {}
            if account_id:
                raw_movement["full_account_id"] = account_id

            try:
                date_text = (
                    row.locator("td.mat-column-date").inner_text(timeout=5000).strip()
                )
                if date_text:
                    last_date_str = date_text

                raw_movement["date"] = last_date_str
                raw_movement["description"] = (
                    row.locator("td.mat-column-detail").inner_text(timeout=5000).strip()
                )

                if status == "no_facturados":
                    charge = (
                        row.locator("td.mat-column-amountCharge")
                        .inner_text(timeout=5000)
                        .strip()
                    )
                    payment = (
                        row.locator("td.mat-column-paymentAmount")
                        .inner_text(timeout=5000)
                        .strip()
                    )
                    raw_movement["amount"] = (
                        f"-{charge}" if charge and charge not in ["0", ""] else payment
                    )
                else:
                    # For billed movements, Santander shows:
                    # - Expenses (gastos) as positive values - we need them negative
                    # - Refunds (reembolsos) as negative values - we need them positive
                    # So we invert the sign to match the expected behavior
                    amount_text = (
                        row.locator("td.mat-column-amount")
                        .inner_text(timeout=5000)
                        .strip()
                    )
                    # Parse the amount to check if it's positive or negative
                    if amount_text.startswith("-"):
                        # Negative amount (refund) - make it positive
                        raw_movement["amount"] = amount_text[
                            1:
                        ]  # Remove the minus sign
                    else:
                        # Positive amount (expense) - make it negative
                        raw_movement["amount"] = f"-{amount_text}"

                parsed_date = parse_chilean_date(raw_movement.get("date"))
                if not parsed_date:
                    continue

                amount = parse_chilean_amount(raw_movement.get("amount"))

                movements.append(
                    MovementModel(
                        date=parsed_date,
                        description=raw_movement.get("description", ""),
                        amount=amount,
                        currency=currency,
                        transaction_type="Cargo" if amount < 0 else "Abono",
                        account_id=account_id,
                        account_type="credito",
                        raw_data=raw_movement,
                    )
                )
            except Exception as e:
                logger.warning(f"Error parsing a movement row: {e}")
                continue

        logger.info(f"Extracted {len(movements)} {status} movements in {currency}.")
        return movements
