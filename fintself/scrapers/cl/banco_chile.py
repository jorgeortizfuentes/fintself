import re
from typing import List, Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect, Locator

from fintself.core.exceptions import DataExtractionError, LoginError
from fintself.core.models import MovementModel
from fintself.scrapers.base import BaseScraper
from fintself.utils.logging import logger
from fintself.utils.parsers import parse_chilean_amount, parse_chilean_date


class BancoChileScraper(BaseScraper):
    """
    Scraper for Banco de Chile.
    """

    LOGIN_URL = "https://sitiospublicos.bancochile.cl/personas"
    LOGIN_TIMEOUT = 45000  # 45 seconds for login operations
    FORM_TIMEOUT = 15000  # 15 seconds for form elements
    PAGE_LOAD_TIMEOUT = 30000  # 30 seconds for page loads

    def _get_bank_id(self) -> str:
        return "cl_banco_chile"

    def _find_element_with_fallbacks(
        self, selectors: List[str], timeout: int = 5000, visible: bool = True
    ) -> Optional[Locator]:
        """Try multiple selectors and return the first one that works."""
        page = self._ensure_page()

        # Split timeout across all selectors
        selector_timeout = (
            max(1000, timeout // len(selectors)) if len(selectors) > 1 else timeout
        )

        for i, selector in enumerate(selectors):
            try:
                element = page.locator(selector)
                if visible:
                    if element.is_visible(timeout=selector_timeout):
                        logger.debug(
                            f"Found element with selector '{selector}' (attempt {i + 1})"
                        )
                        return element
                else:
                    if element.count() > 0:
                        logger.debug(
                            f"Found element with selector '{selector}' (attempt {i + 1})"
                        )
                        return element
            except Exception as e:
                logger.debug(f"Selector '{selector}' failed: {e}")
                continue

        logger.debug(f"No element found with any of {len(selectors)} selectors")
        return None

    def _click_with_fallbacks(self, selectors: List[str], timeout: int = 5000) -> bool:
        """Try to click using multiple selectors."""
        element = self._find_element_with_fallbacks(selectors, timeout)
        if element:
            try:
                # Try direct click first
                element.click()
                return True
            except Exception as e:
                logger.warning(f"Failed direct click: {e}")
                # Try JavaScript click as fallback
                try:
                    page = self._ensure_page()
                    page.evaluate("el => el.click()", element)
                    logger.info("Successfully clicked using JavaScript")
                    return True
                except Exception as e2:
                    logger.warning(f"Failed JavaScript click: {e2}")
                    # Try force click as last resort
                    try:
                        element.click(force=True)
                        logger.info("Successfully clicked with force")
                        return True
                    except Exception as e3:
                        logger.warning(f"Failed force click: {e3}")
        return False

    def _type_with_fallbacks(
        self, selectors: List[str], text: str, timeout: int = 5000
    ) -> bool:
        """Try to type text using multiple selectors."""
        element = self._find_element_with_fallbacks(selectors, timeout)
        if element:
            try:
                element.fill(text)
                return True
            except Exception as e:
                logger.warning(f"Failed to type in element: {e}")
        return False

    def _login(self) -> None:
        """Implements the login logic for Banco de Chile."""
        assert self.user is not None, "User must be provided"
        assert self.password is not None, "Password must be provided"

        page = self._ensure_page()
        logger.info("Logging into Banco de Chile.")
        self._navigate(self.LOGIN_URL)
        self._save_debug_info("01_login_page")

        # Look for login button - try multiple possible selectors
        logger.info("Looking for login access button.")
        login_selectors = [
            'a:has-text("Banco en Línea")',
            'a:has-text("Ingresar")',
            'button:has-text("Ingresar")',
            'a[href*="login"]',
            'button[data-test*="login"]',
            'a:has-text("Acceder")',
            ".login-button",
            '[data-cy="login"]',
            'a.btn:has-text("Banco")',
            'button.btn:has-text("Banco")',
        ]

        login_clicked = self._click_with_fallbacks(login_selectors, timeout=5000)
        if login_clicked:
            logger.info("Successfully clicked login button")
            # Wait for page navigation after clicking login button
            page.wait_for_timeout(3000)  # Give time for navigation

            # Wait for any loading to complete
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except PlaywrightTimeoutError:
                logger.warning("Page load state timeout, continuing...")

        if not login_clicked:
            # If no login button found, try direct navigation to login URL
            logger.info(
                "No login button found, trying direct navigation to login page."
            )
            login_urls = [
                "https://login.portales.bancochile.cl/login",
                "https://portalpersonas.bancochile.cl/login",
                "https://sitiospublicos.bancochile.cl/personas/login",
            ]

            login_form_found = False
            for login_url in login_urls:
                try:
                    logger.info(f"Trying to navigate to: {login_url}")
                    self._navigate(login_url)

                    # Wait for page to load
                    page.wait_for_timeout(2000)

                    # Check if we found a login form
                    form_selectors = [
                        'input[name="username"]',
                        'input[name="rut"]',
                        'role=textbox[name="RUT"]',
                        'input[placeholder*="RUT"]',
                    ]
                    if self._find_element_with_fallbacks(form_selectors, timeout=8000):
                        logger.info(
                            f"Successfully navigated to login page: {login_url}"
                        )
                        login_form_found = True
                        break
                except Exception as e:
                    logger.warning(f"Failed to navigate to {login_url}: {e}")
                    continue

            if not login_form_found:
                logger.warning("Could not find login form through direct navigation")

        # Wait for login form elements with extended timeout and multiple attempts
        form_selectors = [
            'input[placeholder*="RUT"]',
            'input[placeholder*="rut"]',
            'role=textbox[name="RUT"]',
            'input[name="username"]',
            'input[name="rut"]',
            'input[name="user"]',
            "#username",
            "#rut",
            "#user",
            'input[type="text"]:visible',
            'input[autocomplete="username"]',
            'input[data-testid="username"]',
            ".username-input",
            ".rut-input",
        ]

        # Try multiple times with increasing timeouts
        form_element = None
        for attempt in range(3):
            timeout = self.FORM_TIMEOUT + (
                attempt * 5000
            )  # Increase timeout each attempt
            logger.info(
                f"Attempt {attempt + 1}: Looking for login form (timeout: {timeout}ms)"
            )

            form_element = self._find_element_with_fallbacks(
                form_selectors, timeout=timeout
            )
            if form_element:
                break

            if attempt < 2:  # Don't wait after last attempt
                logger.info("Form not found, waiting and trying again...")
                page.wait_for_timeout(3000)

                # Take a debug screenshot to see what's on screen
                self._save_debug_info(f"form_search_attempt_{attempt + 1}")

        if not form_element:
            # Take final debug screenshot
            self._save_debug_info("login_form_not_found_final")

            # Check if we're on an unexpected page
            current_url = page.url
            logger.error(f"Current URL: {current_url}")

            # Check for common error indicators
            error_indicators = [
                ':has-text("mantenimiento")',
                ':has-text("maintenance")',
                ':has-text("error")',
                ':has-text("bloqueado")',
                ".error-page",
                ".maintenance-page",
            ]

            error_found = self._find_element_with_fallbacks(
                error_indicators, timeout=3000
            )
            if error_found:
                error_text = error_found.inner_text()[:200]
                raise LoginError(f"Error or maintenance page detected: {error_text}")

            raise LoginError(
                f"Could not find login form after multiple attempts. Current URL: {current_url}"
            )

        logger.info("Found login form successfully")

        self._save_debug_info("01a_login_frame_loaded")

        logger.info("Entering credentials.")

        # Find and fill username/RUT field with improved selectors order
        username_selectors = [
            'input[placeholder*="RUT"]',
            'input[autocomplete="username"]',
            'role=textbox[name="RUT"]',
            'input[name="username"]',
            'input[name="rut"]',
            "#username",
            "#rut",
            'input[type="text"]:visible:first',
        ]

        username_filled = self._type_with_fallbacks(
            username_selectors, self.user, timeout=5000
        )
        if not username_filled:
            self._save_debug_info("username_field_not_found")
            raise LoginError(
                "Could not find or fill username/RUT field with improved selectors"
            )

        logger.info("Successfully filled username field")

        # Find and fill password field with improved selectors
        password_selectors = [
            'input[type="password"]:visible',
            'input[autocomplete="current-password"]',
            'input[name="password"]',
            'role=textbox[name="Contraseña"]',
            'input[placeholder*="contraseña"]',
            'input[placeholder*="Contraseña"]',
            "#password",
        ]

        password_filled = self._type_with_fallbacks(
            password_selectors, self.password, timeout=5000
        )
        if not password_filled:
            self._save_debug_info("password_field_not_found")
            raise LoginError(
                "Could not find or fill password field with improved selectors"
            )

        logger.info("Successfully filled password field")

        self._save_debug_info("02_credentials_entered")

        logger.info("Submitting login form.")

        # Find and click submit button with improved selectors
        submit_selectors = [
            'role=button[name="Ingresar a cuenta"]',
            'button[type="submit"]:visible',
            'input[type="submit"]:visible',
            'button:has-text("Ingresar")',
            'button:has-text("Entrar")',
            'button:has-text("Acceder")',
            "button.btn-primary",
            "button.login-button",
            ".login-submit",
            '[data-cy="submit"]',
        ]

        submit_clicked = self._click_with_fallbacks(submit_selectors, timeout=5000)

        if not submit_clicked:
            # Try pressing Enter as fallback
            logger.info("Could not find submit button, trying Enter key as fallback")
            try:
                page.keyboard.press("Enter")
            except Exception as e:
                logger.warning(f"Enter key fallback failed: {e}")
                self._save_debug_info("submit_button_not_found")
                # Don't raise error here, continue and let the post-login check handle it

        logger.info("Waiting for post-login page.")

        # Multiple indicators of successful login
        success_selectors = [
            'button:has-text("Mis Productos")',
            'a:has-text("Mis Productos")',
            'nav:has-text("Productos")',
            ".main-menu",
            ".dashboard",
            'h1:has-text("Bienvenido")',
            '[data-testid="dashboard"]',
        ]

        login_successful = False
        try:
            for selector in success_selectors:
                try:
                    page.wait_for_selector(selector, timeout=8000)
                    logger.info(f"Login success detected with selector: {selector}")
                    login_successful = True
                    break
                except PlaywrightTimeoutError:
                    continue

            if login_successful:
                self._save_debug_info("03_login_success")
                logger.info("Login to Banco de Chile successful.")
            else:
                # Additional checks for error messages
                error_selectors = [
                    ':has-text("usuario o contraseña")',
                    ':has-text("credenciales")',
                    ':has-text("error")',
                    ".error-message",
                    ".alert-danger",
                ]

                error_found = self._find_element_with_fallbacks(
                    error_selectors, timeout=3000
                )
                if error_found:
                    error_text = error_found.inner_text()[:100]  # First 100 chars
                    self._save_debug_info("login_error_detected")
                    raise LoginError(f"Login failed with error: {error_text}")
                else:
                    self._save_debug_info("post_login_timeout")
                    raise LoginError(
                        "Timeout waiting for post-login page. Check credentials or for maintenance page."
                    )

        except Exception as e:
            if isinstance(e, LoginError):
                raise
            self._save_debug_info("post_login_unexpected_error")
            raise LoginError(f"Unexpected error during login verification: {str(e)}")

    def _dismiss_overlays(self) -> None:
        """Dismiss any overlays that might block clicks."""
        page = self._ensure_page()

        try:
            # Look for common overlay elements
            overlay_selectors = [
                ".cdk-overlay-backdrop",
                ".fondo",
                ".overlay",
                ".modal-backdrop",
                '[class*="backdrop"]',
            ]

            for selector in overlay_selectors:
                try:
                    overlays = page.locator(selector)
                    if overlays.count() > 0:
                        logger.info(f"Found overlay with selector: {selector}")
                        # Try to click outside or dismiss
                        try:
                            # Click the overlay to dismiss it
                            overlays.first.click(timeout=1000)
                            page.wait_for_timeout(500)
                        except Exception:
                            # Try pressing Escape
                            try:
                                page.keyboard.press("Escape")
                                page.wait_for_timeout(500)
                            except Exception:
                                pass
                except Exception:
                    continue

        except Exception as e:
            logger.debug(f"Error dismissing overlays: {e}")

    def _close_popup(self) -> None:
        """Closes the initial marketing popup if it appears."""
        page = self._ensure_page()
        logger.info("Checking for marketing popup.")

        popup_close_selectors = [
            "button.btn.default.pull-right:has(i.ion-ios-close-empty):not([hidden])",
            "button.close",
            "button:has-text('×')",
            "[aria-label='Close']",
            ".modal-close",
            ".popup-close",
            "button[data-dismiss='modal']",
        ]

        try:
            popup_closed = self._click_with_fallbacks(
                popup_close_selectors, timeout=10000
            )
            if popup_closed:
                logger.info("Marketing popup closed successfully.")
                self._save_debug_info("04_popup_closed")
                # Wait a moment for the popup to fully disappear
                page.wait_for_timeout(1000)
            else:
                logger.info("No marketing popup found or could not be closed.")
        except Exception as e:
            logger.info(f"Error handling popup (continuing): {e}")

    def _extract_movements_from_table(
        self, currency: str, account_id: str
    ) -> List[MovementModel]:
        """Extracts all movements from the currently displayed table, handling pagination."""
        page = self._ensure_page()
        movements: List[MovementModel] = []

        # Wait for either the table or a "no info" message with improved selectors
        table_or_message_selectors = [
            "table.bch-table",
            "div.bch-alert:has-text('No existe información')",
            "div.alert:has-text('No existe')",
            ".no-data-message",
            "table:has(tbody tr)",
            ".movements-table",
        ]

        try:
            # Try to find either table or no-data message
            element_found = False
            for selector in table_or_message_selectors:
                try:
                    page.wait_for_selector(selector, timeout=8000)
                    element_found = True
                    logger.info(f"Found element with selector: {selector}")
                    break
                except PlaywrightTimeoutError:
                    continue

            if not element_found:
                logger.warning(
                    f"No table or info message found for account {account_id}"
                )
                return []

        except Exception as e:
            logger.warning(
                f"Error waiting for table/message for account {account_id}: {e}"
            )
            return []

        # Check for "no data" messages with multiple possible selectors
        no_data_selectors = [
            "div.bch-alert:has-text('No existe información')",
            "div.alert:has-text('No existe')",
            "div:has-text('No hay movimientos')",
            "div:has-text('Sin movimientos')",
            ".no-data",
            ".empty-state",
        ]

        no_data_element = self._find_element_with_fallbacks(
            no_data_selectors, timeout=3000
        )
        if no_data_element:
            logger.info(f"No movements found for account {account_id} in {currency}.")
            return []

        logger.info(f"Extracting movements for account {account_id} in {currency}.")
        self._save_debug_info(f"movements_table_{currency}_{account_id}")

        page_num = 1
        while True:
            logger.info(f"Scraping page {page_num} for account {account_id}.")

            # Check for table rows with improved selectors
            row_selectors = [
                "table.bch-table tbody tr.bch-row",
                "table tbody tr:not(.no-data)",
                "tbody tr.movement-row",
                "table tr[data-row]",
                "tbody tr:has(td)",
            ]

            rows_found = False
            for selector in row_selectors:
                try:
                    page.wait_for_selector(selector, timeout=5000)
                    rows_found = True
                    break
                except PlaywrightTimeoutError:
                    continue

            if not rows_found:
                logger.info("Movement table is present, but contains no rows.")
                break

            # Get table rows with improved selectors to exclude detail/collapsed rows
            row_selector = "table.bch-table tbody tr.bch-row:not(.table-collapse-row)"
            if not page.locator(row_selector).count():
                # Fallback selector if the specific class names don't work
                row_selector = "table tbody tr:has(td):not(:has(.collapse))"

            rows = page.locator(row_selector).all()
            logger.info(f"Found {len(rows)} rows to process on page {page_num}")

            for i, row in enumerate(rows):
                try:
                    # Try multiple selectors for each column in case structure changes
                    date_selectors = [
                        "td.cdk-column-fechaContable",
                        "td:nth-child(1)",
                        ".date-column",
                        "td[data-column='date']",
                    ]
                    desc_selectors = [
                        "td.cdk-column-descripcion",
                        "td:nth-child(2)",
                        ".description-column",
                        "td[data-column='description']",
                    ]
                    cargo_selectors = [
                        "td.cdk-column-cargo",
                        "td:nth-child(3)",
                        ".debit-column",
                        "td[data-column='debit']",
                    ]
                    abono_selectors = [
                        "td.cdk-column-abono",
                        "td:nth-child(4)",
                        ".credit-column",
                        "td[data-column='credit']",
                    ]

                    # Find elements within this specific row using multiple selectors
                    date_element = None
                    desc_element = None
                    cargo_element = None
                    abono_element = None

                    # Try each selector for date
                    for sel in date_selectors:
                        try:
                            candidate = row.locator(sel)
                            if candidate.count() > 0:
                                date_element = candidate
                                break
                        except Exception:
                            continue

                    # Try each selector for description
                    for sel in desc_selectors:
                        try:
                            candidate = row.locator(sel)
                            if candidate.count() > 0:
                                desc_element = candidate
                                break
                        except Exception:
                            continue

                    # Try each selector for cargo
                    for sel in cargo_selectors:
                        try:
                            candidate = row.locator(sel)
                            if candidate.count() > 0:
                                cargo_element = candidate
                                break
                        except Exception:
                            continue

                    # Try each selector for abono
                    for sel in abono_selectors:
                        try:
                            candidate = row.locator(sel)
                            if candidate.count() > 0:
                                abono_element = candidate
                                break
                        except Exception:
                            continue

                    if not all([date_element, desc_element]):
                        logger.warning(
                            f"Could not find required elements in row {i + 1}"
                        )
                        continue

                    date_str = date_element.inner_text().strip() if date_element else ""
                    description = (
                        desc_element.inner_text().strip() if desc_element else ""
                    )
                    cargo_str = (
                        cargo_element.inner_text().strip() if cargo_element else ""
                    )
                    abono_str = (
                        abono_element.inner_text().strip() if abono_element else ""
                    )

                    # Validate and parse date
                    if not date_str:
                        logger.warning(f"Empty date in row {i + 1}, skipping")
                        continue

                    date = parse_chilean_date(date_str)
                    if not date:
                        logger.warning(
                            f"Could not parse date '{date_str}' in row {i + 1}, skipping"
                        )
                        continue

                    # Determine amount and transaction type with better logic
                    if (
                        cargo_str.strip()
                        and cargo_str.strip() != "-"
                        and cargo_str.strip() != "0"
                    ):
                        amount_str = f"-{cargo_str.strip()}"
                        transaction_type = "Cargo"
                    elif (
                        abono_str.strip()
                        and abono_str.strip() != "-"
                        and abono_str.strip() != "0"
                    ):
                        amount_str = abono_str.strip()
                        transaction_type = "Abono"
                    else:
                        logger.debug(
                            f"Row {i + 1} has no valid amount (cargo: '{cargo_str}', abono: '{abono_str}'), skipping"
                        )
                        continue

                    amount = parse_chilean_amount(amount_str)
                    if amount.is_zero():
                        logger.debug(f"Row {i + 1} has zero amount, skipping")
                        continue

                    # Create movement with enhanced data
                    movement = MovementModel(
                        date=date,
                        description=description,
                        amount=amount,
                        currency=currency,
                        transaction_type=transaction_type,
                        account_id=account_id,
                        account_type="corriente",
                        raw_data={
                            "date_str": date_str,
                            "cargo_str": cargo_str,
                            "abono_str": abono_str,
                            "full_account_id": account_id,
                            "page_number": page_num,
                            "row_index": i + 1,
                        },
                    )
                    movements.append(movement)
                    logger.debug(
                        f"Added movement: {description[:50]}... Amount: {amount} {currency}"
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to parse row {i + 1} for account {account_id}: {e}"
                    )
                    # Save debug info for problematic rows
                    try:
                        self._save_debug_info(
                            f"parse_error_page_{page_num}_row_{i + 1}"
                        )
                    except Exception:
                        pass  # Don't let debug saving break the flow

            # Check for next page button with multiple selectors
            next_page_selectors = [
                'button[aria-label="Próxima página"]',
                'button[aria-label="Next page"]',
                'button:has-text("Siguiente")',
                ".mat-paginator-navigation-next",
                "button.mat-paginator-navigation-next",
                'button[data-cy="next-page"]',
            ]

            next_button = self._find_element_with_fallbacks(
                next_page_selectors, timeout=3000
            )

            if not next_button or next_button.is_disabled():
                logger.info(f"Last page of movements reached for account {account_id}.")
                break

            paginator_label = page.locator(
                "div.mat-paginator-range-actions .mat-paginator-label"
            )
            paginator_text_before = ""
            try:
                # A short timeout because if it's not there, we shouldn't wait long.
                paginator_text_before = paginator_label.inner_text(timeout=3000)
            except PlaywrightTimeoutError:
                logger.warning(
                    "Paginator label not found before clicking next. Waiting may be unreliable."
                )

            logger.info(f"Going to next page of movements for account {account_id}.")
            page_num += 1
            self._click(next_button)

            # Wait for the paginator text to change, which is a reliable signal that the
            # new page's data has loaded. This avoids flaky 'networkidle' waits.
            if paginator_text_before:
                try:
                    expect(paginator_label).not_to_have_text(
                        paginator_text_before, timeout=20000
                    )
                except PlaywrightTimeoutError:
                    logger.warning(
                        "Paginator text did not change after clicking next. "
                        "This might indicate a page load issue."
                    )
            else:
                # If we couldn't get paginator text, use a less reliable wait.
                page.wait_for_load_state("domcontentloaded", timeout=20000)

            self._save_debug_info(
                f"movements_table_{currency}_{account_id}_page_{page_num}"
            )

        logger.info(
            f"Extracted {len(movements)} movements for account {account_id} in {currency}."
        )
        return movements

    def _scrape_movements(self) -> List[MovementModel]:
        """Orchestrates the extraction of movements by iterating through accounts and currencies."""
        page = self._ensure_page()
        all_movements: List[MovementModel] = []

        # Wait longer for the page to fully load after login
        page.wait_for_timeout(5000)

        # Close any popup that might interfere
        self._close_popup()

        # Extract account movements (debit accounts)
        logger.info("Extracting account movements")
        account_movements = self._scrape_account_movements()
        all_movements.extend(account_movements)

        # Extract credit card movements
        logger.info("Extracting credit card movements")
        credit_card_movements = self._scrape_credit_card_movements()
        all_movements.extend(credit_card_movements)

        logger.info(
            f"Scraping completed. Total movements extracted: {len(all_movements)}"
        )

        # Validate movements before returning
        if all_movements:
            # Check for duplicates based on date, description, and amount
            seen_movements = set()
            unique_movements = []

            for movement in all_movements:
                movement_key = (
                    movement.date,
                    movement.description,
                    movement.amount,
                    movement.account_id,
                )
                if movement_key not in seen_movements:
                    seen_movements.add(movement_key)
                    unique_movements.append(movement)
                else:
                    logger.debug(
                        f"Duplicate movement detected and removed: {movement.description}"
                    )

            if len(unique_movements) != len(all_movements):
                logger.info(
                    f"Removed {len(all_movements) - len(unique_movements)} duplicate movements"
                )

            return unique_movements

        return all_movements

    def _scrape_account_movements(self) -> List[MovementModel]:
        """Scrapes debit account movements from the Saldos y Movimientos section."""
        page = self._ensure_page()
        movements: List[MovementModel] = []

        logger.info("Navigating to 'Saldos y Movimientos' section.")

        # Navigate to movements section with improved selectors
        products_selectors = [
            'button:has-text("Mis Productos")',
            'a:has-text("Mis Productos")',
            ".main-menu-products",
        ]
        products_clicked = self._click_with_fallbacks(products_selectors, timeout=10000)

        if not products_clicked:
            raise DataExtractionError("Could not find 'Mis Productos' menu")

        page.wait_for_timeout(2000)  # Wait for menu to appear

        # Try to dismiss any overlays that might be blocking clicks
        self._dismiss_overlays()

        # Try multiple approaches to navigate to movements section
        navigation_successful = False

        # Approach 1: Regular click with better selectors
        movements_selectors = [
            'a[href="#/movimientos/cuenta/saldos-movimientos"]',
            'a:has-text("Saldos y Movimientos")',
            'a:has-text("Movimientos")',
            ".movements-link",
        ]
        movements_clicked = self._click_with_fallbacks(
            movements_selectors, timeout=8000
        )

        if movements_clicked:
            page.wait_for_timeout(3000)
            navigation_successful = True
            logger.info(
                "Successfully navigated to movements section using regular click"
            )

        # Approach 2: JavaScript navigation as fallback
        if not navigation_successful:
            try:
                logger.info("Trying JavaScript navigation fallback")
                page.evaluate(
                    "window.location.hash = '#/movimientos/cuenta/saldos-movimientos'"
                )
                page.wait_for_timeout(3000)
                navigation_successful = True
                logger.info(
                    "Successfully navigated to movements section using JavaScript"
                )
            except Exception as e:
                logger.warning(f"JavaScript navigation failed: {e}")

        # Approach 3: Direct URL navigation
        if not navigation_successful:
            try:
                logger.info("Trying direct URL navigation")
                current_url = page.url
                base_url = current_url.split("#")[0]  # Remove existing hash
                new_url = f"{base_url}#/movimientos/cuenta/saldos-movimientos"
                page.goto(new_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(3000)
                navigation_successful = True
                logger.info("Successfully navigated using direct URL")
            except Exception as e:
                logger.warning(f"Direct URL navigation failed: {e}")

        if not navigation_successful:
            logger.warning(
                "All navigation approaches failed, continuing anyway to test credit cards"
            )
            # Don't raise error here, just continue to credit cards

        self._save_debug_info("05_movements_section_clicked")

        # Only proceed with account movements if navigation was successful
        if not navigation_successful:
            logger.warning(
                "Skipping account movements extraction due to navigation failure"
            )
            return []

        # Wait for account selection modal with improved selectors
        modal_selectors = [
            'h2:has-text("Seleccione una cuenta")',
            'h1:has-text("Seleccione una cuenta")',
            '.modal-title:has-text("Seleccione")',
            'mat-select[name="monedas"]',
        ]

        modal_found = False
        for selector in modal_selectors:
            try:
                page.wait_for_selector(selector, timeout=10000)
                modal_found = True
                logger.info(
                    f"Account selection modal opened (detected with: {selector})"
                )
                break
            except PlaywrightTimeoutError:
                continue

        if not modal_found:
            self._save_debug_info("account_selection_modal_not_found")
            logger.warning(
                "Account selection modal did not appear, skipping account movements"
            )
            return []

        self._save_debug_info("06_account_selection_modal_opened")

        # Get all currency options with improved error handling
        currency_selectors = [
            'mat-select[name="monedas"]',
            ".currency-selector",
            'select[name="currency"]',
        ]
        currency_dropdown_clicked = self._click_with_fallbacks(
            currency_selectors, timeout=10000
        )

        if not currency_dropdown_clicked:
            raise DataExtractionError("Could not find or click currency dropdown")

        page.wait_for_timeout(1000)  # Wait for options to load

        # Try multiple selectors for currency options
        option_selectors = [
            "mat-option span.mat-option-text",
            "mat-option",
            "option",
            ".currency-option",
        ]

        currency_options_loc = None
        for selector in option_selectors:
            options = page.locator(selector)
            if options.count() > 0:
                currency_options_loc = options
                break

        if not currency_options_loc or currency_options_loc.count() == 0:
            raise DataExtractionError("No currency options found in dropdown")

        currency_texts = []
        for i in range(currency_options_loc.count()):
            try:
                text = currency_options_loc.nth(i).inner_text().strip()
                if text:  # Only add non-empty options
                    currency_texts.append(text)
            except Exception as e:
                logger.warning(f"Could not get text for currency option {i}: {e}")

        # Close dropdown
        try:
            currency_options_loc.first.click()
        except Exception:
            # Alternative ways to close dropdown
            try:
                page.keyboard.press("Escape")
            except Exception:
                logger.warning("Could not close currency dropdown")

        if not currency_texts:
            raise DataExtractionError("No valid currency options found")

        logger.info(f"Found currencies: {currency_texts}")

        for i_currency, currency_text in enumerate(currency_texts):
            logger.info(f"Processing currency: {currency_text}")

            # Select currency in modal. The modal should already be open.
            self._click('mat-select[name="monedas"]')
            self._click(f'mat-option:has-text("{currency_text}")')
            page.wait_for_timeout(2000)  # Give time for accounts to load

            currency_code_match = re.search(r"\((.*?)\)", currency_text)
            if not currency_code_match:
                logger.warning(
                    f"Could not extract currency code from '{currency_text}', skipping."
                )
                continue
            currency_code = currency_code_match.group(1).strip()

            account_labels = [
                loc.inner_text().strip()
                for loc in page.locator(
                    "mat-radio-button .mat-radio-label-content"
                ).all()
            ]
            logger.info(
                f"Found {len(account_labels)} accounts for {currency_code}: {account_labels}"
            )

            for i_account, account_label in enumerate(account_labels):
                logger.info(
                    f"Processing account {i_account + 1}/{len(account_labels)}: {account_label}"
                )

                # Use nth to select the correct radio button to avoid ambiguity
                radio_selectors = [
                    "mat-radio-button",
                    ".radio-button",
                    "input[type='radio']",
                ]
                account_radio = None

                for selector in radio_selectors:
                    radios = page.locator(selector)
                    if radios.count() > i_account:
                        account_radio = radios.nth(i_account)
                        break

                if not account_radio:
                    logger.error(f"Could not find radio button for account {i_account}")
                    continue

                # Extract account ID with improved regex
                account_id_patterns = [
                    r"([\d-]+)",  # Original pattern
                    r"(\d{2}-\d{3}-\d{5}-\d{2})",  # Specific Chilean account format
                    r"(\d+)",  # Just numbers
                ]

                account_id = None
                for pattern in account_id_patterns:
                    match = re.search(pattern, account_label)
                    if match:
                        account_id = match.group(1).strip()
                        break

                if not account_id:
                    account_id = f"unknown_{i_account}_{account_label[:20]}"
                    logger.warning(
                        f"Could not extract account ID from '{account_label}', using: {account_id}"
                    )

                # Click radio button with error handling
                try:
                    # Instructions say click twice for this specific site
                    account_radio.click(click_count=2, delay=100)
                    logger.info(f"Selected account radio button for {account_id}")
                except Exception as e:
                    logger.warning(
                        f"Error clicking radio button: {e}, trying single click"
                    )
                    try:
                        account_radio.click()
                    except Exception as e2:
                        logger.error(f"Could not click radio button: {e2}")
                        continue

                # Click accept button with multiple selectors
                accept_selectors = [
                    'bch-button[id="modalPrimaryBtn"] button:has-text("Aceptar")',
                    'button:has-text("Aceptar")',
                    'button:has-text("Confirmar")',
                    'button[type="submit"]',
                    ".modal-confirm",
                    "#modalPrimaryBtn",
                ]

                accept_clicked = self._click_with_fallbacks(
                    accept_selectors, timeout=5000
                )
                if not accept_clicked:
                    logger.error("Could not find or click accept button")
                    continue

                account_movements = self._extract_movements_from_table(
                    currency_code, account_id
                )
                movements.extend(account_movements)

                is_last_overall_account = (i_currency == len(currency_texts) - 1) and (
                    i_account == len(account_labels) - 1
                )

                if not is_last_overall_account:
                    logger.info("Going back to account selection modal.")

                    # Try multiple selectors for "select another account" button/link
                    account_selection_selectors = [
                        'button:has-text("Seleccionar otra cuenta")',
                        'a:has-text("SELECCIONAR OTRA CUENTA")',
                        'a:has-text("Seleccionar otra cuenta")',
                        'button:has-text("SELECCIONAR OTRA CUENTA")',
                        'button:has-text("Cambiar cuenta")',
                        'a:has-text("Cambiar cuenta")',
                        '[data-test*="select-account"]',
                        'a[href*="seleccionar"]',
                        ".account-selector-link",
                        "button.change-account",
                    ]

                    selection_clicked = self._click_with_fallbacks(
                        account_selection_selectors, timeout=5000
                    )

                    if not selection_clicked:
                        logger.warning(
                            "Could not find 'select another account' button, trying navigation workaround"
                        )
                        # Multiple fallback strategies
                        navigation_successful = False

                        # Strategy 1: Direct link to movements section
                        try:
                            page.locator(
                                'a[href="#/movimientos/cuenta/saldos-movimientos"]'
                            ).click(timeout=3000)
                            navigation_successful = True
                            logger.info("Used direct link navigation")
                        except Exception:
                            pass

                        # Strategy 2: Through main menu
                        if not navigation_successful:
                            try:
                                self._click_with_fallbacks(
                                    [
                                        'button:has-text("Mis Productos")',
                                        'a:has-text("Mis Productos")',
                                    ],
                                    timeout=5000,
                                )
                                page.wait_for_timeout(1000)
                                self._click_with_fallbacks(
                                    [
                                        'a[href="#/movimientos/cuenta/saldos-movimientos"]'
                                    ],
                                    timeout=5000,
                                )
                                navigation_successful = True
                                logger.info("Used main menu navigation")
                            except Exception as e:
                                logger.warning(f"Main menu navigation failed: {e}")

                        # Strategy 3: Browser refresh as last resort
                        if not navigation_successful:
                            logger.warning("Trying page refresh as last resort")
                            try:
                                page.reload(wait_until="domcontentloaded")
                                # Navigate back to movements section after refresh
                                self._click_with_fallbacks(
                                    ['button:has-text("Mis Productos")'], timeout=10000
                                )
                                self._click_with_fallbacks(
                                    [
                                        'a[href="#/movimientos/cuenta/saldos-movimientos"]'
                                    ],
                                    timeout=5000,
                                )
                                navigation_successful = True
                                logger.info("Page refresh navigation successful")
                            except Exception as e:
                                logger.error(f"Page refresh navigation failed: {e}")
                                # If all strategies fail, we'll break out of the loop
                                break

                    # Wait for account selection modal with improved approach
                    modal_selectors = [
                        'h2:has-text("Seleccione una cuenta")',
                        'h1:has-text("Seleccione una cuenta")',
                        '.modal-title:has-text("Seleccione")',
                        'mat-select[name="monedas"]',
                        ".account-selector",
                        ".currency-selector",
                    ]

                    modal_element = None
                    for selector in modal_selectors:
                        try:
                            page.wait_for_selector(selector, timeout=8000)
                            modal_element = page.locator(selector)
                            logger.info(
                                f"Found account selection modal with selector: {selector}"
                            )
                            break
                        except PlaywrightTimeoutError:
                            continue

                    if not modal_element:
                        logger.warning(
                            "Could not find account selection modal after navigation"
                        )
                        # Try waiting a bit longer and check again
                        page.wait_for_timeout(3000)
                        modal_element = self._find_element_with_fallbacks(
                            modal_selectors, timeout=5000
                        )

                        if not modal_element:
                            logger.error(
                                "Account selection modal not found after multiple attempts"
                            )
                            # This might indicate a serious navigation issue
                            self._save_debug_info(
                                f"modal_not_found_after_account_{account_id}"
                            )
                            break  # Break out to avoid infinite loop

                    # If there are more accounts for the same currency, we need to reselect the currency
                    # to have the list of accounts ready for the next iteration.
                    if i_account < len(account_labels) - 1:
                        try:
                            currency_dropdown_selectors = [
                                'mat-select[name="monedas"]',
                                ".currency-selector",
                                'select[name="currency"]',
                            ]
                            dropdown_clicked = self._click_with_fallbacks(
                                currency_dropdown_selectors, timeout=5000
                            )

                            if dropdown_clicked:
                                page.wait_for_timeout(1000)  # Wait for options to load
                                option_selectors = [
                                    f'mat-option:has-text("{currency_text}")',
                                    f'option:has-text("{currency_text}")',
                                ]
                                option_clicked = self._click_with_fallbacks(
                                    option_selectors, timeout=5000
                                )

                                if option_clicked:
                                    page.wait_for_timeout(
                                        2000
                                    )  # Wait for accounts to load
                                    logger.info(
                                        f"Reselected currency {currency_text} for next account"
                                    )
                                else:
                                    logger.warning(
                                        f"Could not reselect currency {currency_text}"
                                    )
                            else:
                                logger.warning("Could not click currency dropdown")
                        except Exception as e:
                            logger.warning(f"Error reselecting currency: {e}")
                            # Continue anyway, might still work

        logger.info(f"Account movements extracted: {len(movements)}")
        return movements

    def _scrape_credit_card_movements(self) -> List[MovementModel]:
        """Scrapes credit card movements from both non-invoiced and invoiced sections."""
        page = self._ensure_page()
        movements: List[MovementModel] = []

        logger.info("Navigating to credit card section")

        try:
            # Navigate to main menu
            products_selectors = [
                'button:has-text("Mis Productos")',
                'a:has-text("Mis Productos")',
                ".main-menu-products",
            ]
            products_clicked = self._click_with_fallbacks(
                products_selectors, timeout=10000
            )

            if not products_clicked:
                logger.warning("Could not find 'Mis Productos' menu for credit card")
                return []

            page.wait_for_timeout(2000)

            # Click on "Tarjeta de Crédito" button
            credit_card_selectors = [
                'button[id="41300"]:has-text("Tarjeta de Crédito")',
                'button:has-text("Tarjeta de Crédito")',
                'a:has-text("Tarjeta de Crédito")',
                ".credit-card-menu",
            ]

            credit_card_clicked = self._click_with_fallbacks(
                credit_card_selectors, timeout=10000
            )
            if not credit_card_clicked:
                logger.warning("Could not find 'Tarjeta de Crédito' menu")
                return []

            page.wait_for_timeout(2000)
            self._save_debug_info("07_credit_card_menu_opened")

            # Extract movements from non-invoiced section
            non_invoiced_movements = self._extract_credit_card_movements_section(
                section_type="no-facturados",
                link_selector='a[href="#/tarjeta-credito/consultar/saldos"]',
                link_text="Saldos y Movimientos No Facturados",
            )
            movements.extend(non_invoiced_movements)

            # Extract movements from invoiced section
            invoiced_movements = self._extract_credit_card_movements_section(
                section_type="facturados",
                link_selector='a[href="#/tarjeta-credito/consultar/facturados"]',
                link_text="Movimientos Facturados",
            )
            movements.extend(invoiced_movements)

        except Exception as e:
            logger.warning(f"Error extracting credit card movements: {e}")
            self._save_debug_info("credit_card_error")

        logger.info(f"Credit card movements extracted: {len(movements)}")
        return movements

    def _extract_credit_card_movements_section(
        self, section_type: str, link_selector: str, link_text: str
    ) -> List[MovementModel]:
        """Extracts credit card movements from a specific section (invoiced or non-invoiced)."""
        page = self._ensure_page()
        movements: List[MovementModel] = []

        logger.info(f"Extracting {section_type} credit card movements")

        try:
            # Navigate to the specific section
            section_selectors = [
                link_selector,
                f'a:has-text("{link_text}")',
                f".{section_type}-link",
            ]

            section_clicked = self._click_with_fallbacks(
                section_selectors, timeout=5000
            )

            # The side menu collapses after entering a credit-card sub-page, so
            # the link may no longer be in the DOM. Fall back to hash navigation
            # (and re-open the credit-card menu if that also fails).
            if not section_clicked:
                logger.info(
                    f"Side-menu link for {section_type} not found, trying hash navigation"
                )
                hash_target = (
                    link_selector.split('"')[1] if '"' in link_selector else ""
                )
                if hash_target:
                    try:
                        page.evaluate(f"window.location.hash = '{hash_target}'")
                        page.wait_for_timeout(3000)
                        section_clicked = True
                        logger.info(f"Navigated to {section_type} section via hash URL")
                    except Exception as e:
                        logger.warning(f"Hash navigation failed: {e}")

            if not section_clicked:
                logger.warning(f"Could not navigate to {section_type} section")
                return []

            page.wait_for_timeout(3000)
            self._save_debug_info(f"08_{section_type}_section_opened")

            # Extract from Nacional tab
            nacional_movements = self._extract_credit_card_tab_movements(
                "Nacional", section_type
            )
            movements.extend(nacional_movements)

            # Extract from Internacional tab
            internacional_movements = self._extract_credit_card_tab_movements(
                "Internacional", section_type
            )
            movements.extend(internacional_movements)

        except Exception as e:
            logger.warning(f"Error extracting {section_type} movements: {e}")

        return movements

    def _extract_credit_card_tab_movements(
        self, tab_name: str, section_type: str
    ) -> List[MovementModel]:
        """Extracts movements from a specific tab (Nacional/Internacional) within a credit card section."""
        page = self._ensure_page()
        movements: List[MovementModel] = []

        logger.info(f"Extracting {tab_name} movements from {section_type} section")

        try:
            # Click on the tab
            tab_selectors = [
                f'div.mat-tab-label:has-text("{tab_name}")',
                f'button:has-text("{tab_name}")',
                f'.mat-tab-label:has-text("{tab_name}")',
            ]

            tab_clicked = self._click_with_fallbacks(tab_selectors, timeout=5000)
            if not tab_clicked:
                logger.info(f"Could not find {tab_name} tab, might be already selected")

            page.wait_for_timeout(2000)
            self._save_debug_info(f"09_{section_type}_{tab_name}_tab")

            # Check for "no information" message
            no_data_selectors = [
                "div.bch-alert:has-text('No existe información')",
                "div.alert:has-text('No existe')",
                "div:has-text('No hay movimientos')",
                "div:has-text('Sin movimientos')",
                ".no-data",
                ".empty-state",
            ]

            no_data_element = self._find_element_with_fallbacks(
                no_data_selectors, timeout=3000
            )
            if no_data_element:
                logger.info(f"No {tab_name} movements found in {section_type} section")
                return []

            # Extract movements from table
            movements = self._extract_credit_card_movements_from_table(
                tab_name, section_type
            )

        except Exception as e:
            logger.warning(
                f"Error extracting {tab_name} tab movements from {section_type}: {e}"
            )

        return movements

    def _extract_credit_card_movements_from_table(
        self, currency: str, section_type: str
    ) -> List[MovementModel]:
        """Extracts credit card movements from the currently displayed table."""
        page = self._ensure_page()
        movements: List[MovementModel] = []

        try:
            # Wait for table to load
            table_selectors = [
                "table.bch-table",
                "table:has(tbody tr)",
                ".movements-table",
            ]

            table_found = False
            for selector in table_selectors:
                try:
                    page.wait_for_selector(selector, timeout=8000)
                    table_found = True
                    break
                except PlaywrightTimeoutError:
                    continue

            if not table_found:
                logger.info(
                    f"No table found for {currency} movements in {section_type}"
                )
                return []

            # Get table rows (excluding collapse/detail rows)
            row_selector = "table.bch-table tbody tr.bch-row:not(.table-collapse-row)"
            rows = page.locator(row_selector).all()

            if not rows:
                logger.info(f"No movement rows found for {currency} in {section_type}")
                return []

            logger.info(
                f"Found {len(rows)} credit card movement rows for {currency} in {section_type}"
            )

            for i, row in enumerate(rows):
                try:
                    # Extract data from row columns based on the HTML structure provided
                    date_selectors = [
                        "td.cdk-column-fechaTransaccion",
                        "td:nth-child(1)",
                    ]
                    type_selectors = [
                        "td.cdk-column-tipoMovimientoLabel",
                        "td:nth-child(2)",
                    ]
                    desc_selectors = ["td.cdk-column-descripcion", "td:nth-child(3)"]
                    cuotas_selectors = ["td.cdk-column-cuotas", "td:nth-child(4)"]
                    cargo_selectors = ["td.cdk-column-cargo", "td:nth-child(5)"]
                    pago_selectors = ["td.cdk-column-pago", "td:nth-child(6)"]

                    # Find elements within this specific row
                    date_element = None
                    desc_element = None
                    tipo_element = None
                    cuotas_element = None
                    cargo_element = None
                    pago_element = None

                    for sel in date_selectors:
                        candidate = row.locator(sel)
                        if candidate.count() > 0:
                            date_element = candidate
                            break

                    for sel in desc_selectors:
                        candidate = row.locator(sel)
                        if candidate.count() > 0:
                            desc_element = candidate
                            break

                    for sel in type_selectors:
                        candidate = row.locator(sel)
                        if candidate.count() > 0:
                            tipo_element = candidate
                            break

                    for sel in cuotas_selectors:
                        candidate = row.locator(sel)
                        if candidate.count() > 0:
                            cuotas_element = candidate
                            break

                    for sel in cargo_selectors:
                        candidate = row.locator(sel)
                        if candidate.count() > 0:
                            cargo_element = candidate
                            break

                    for sel in pago_selectors:
                        candidate = row.locator(sel)
                        if candidate.count() > 0:
                            pago_element = candidate
                            break

                    if not all([date_element, desc_element]):
                        logger.warning(
                            f"Could not find required elements in credit card row {i + 1}"
                        )
                        continue

                    # Extract text content
                    date_str = date_element.inner_text().strip() if date_element else ""
                    description = (
                        desc_element.inner_text().strip() if desc_element else ""
                    )
                    tipo_str = tipo_element.inner_text().strip() if tipo_element else ""
                    cuotas_str = (
                        cuotas_element.inner_text().strip() if cuotas_element else ""
                    )
                    cargo_str = (
                        cargo_element.inner_text().strip() if cargo_element else ""
                    )
                    pago_str = pago_element.inner_text().strip() if pago_element else ""

                    # Parse date
                    date = parse_chilean_date(date_str)
                    if not date:
                        logger.warning(
                            f"Could not parse date '{date_str}' in credit card row {i + 1}"
                        )
                        continue

                    # Determine amount and transaction type
                    amount_str = ""
                    transaction_type = "Credit Card"

                    if (
                        cargo_str.strip()
                        and cargo_str.strip() != "-"
                        and cargo_str.strip() != "0"
                    ):
                        amount_str = f"-{cargo_str.strip()}"  # Negative for charges
                        if tipo_str:
                            transaction_type = f"Credit Card - {tipo_str}"
                    elif (
                        pago_str.strip()
                        and pago_str.strip() != "-"
                        and pago_str.strip() != "0"
                    ):
                        amount_str = pago_str.strip()  # Positive for payments
                        transaction_type = "Credit Card - Payment"
                    else:
                        logger.debug(
                            f"Credit card row {i + 1} has no valid amount, skipping"
                        )
                        continue

                    amount = parse_chilean_amount(amount_str)
                    if amount.is_zero():
                        logger.debug(
                            f"Credit card row {i + 1} has zero amount, skipping"
                        )
                        continue

                    # Create enhanced description
                    enhanced_desc = description
                    if cuotas_str and cuotas_str != "-":
                        enhanced_desc += f" (Cuotas: {cuotas_str})"

                    # Determine account type and currency
                    account_type = "credito"
                    currency_code = "CLP" if currency == "Nacional" else "USD"
                    account_id = f"credit_card_{currency.lower()}_{section_type}"

                    # Create movement
                    movement = MovementModel(
                        date=date,
                        description=enhanced_desc,
                        amount=amount,
                        currency=currency_code,
                        transaction_type=transaction_type,
                        account_id=account_id,
                        account_type=account_type,
                        raw_data={
                            "date_str": date_str,
                            "tipo_movimiento": tipo_str,
                            "cuotas": cuotas_str,
                            "cargo_str": cargo_str,
                            "pago_str": pago_str,
                            "section_type": section_type,
                            "currency_type": currency,
                            "row_index": i + 1,
                        },
                    )
                    movements.append(movement)
                    logger.debug(
                        f"Added credit card movement: {enhanced_desc[:50]}... Amount: {amount} {currency_code}"
                    )

                except Exception as e:
                    logger.warning(f"Failed to parse credit card row {i + 1}: {e}")

        except Exception as e:
            logger.warning(f"Error extracting credit card movements from table: {e}")

        return movements
