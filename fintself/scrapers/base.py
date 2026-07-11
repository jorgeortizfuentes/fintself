import datetime
import os
import random
import time
from abc import ABC, abstractmethod
from typing import List, Literal, Optional, Union

from playwright.sync_api import (
    Browser,
    Locator,
    Page,
    Playwright,
    sync_playwright,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from fintself import settings
from fintself.core.exceptions import DataExtractionError, LoginError
from fintself.core.models import MovementModel
from fintself.utils.logging import logger


class BaseScraper(ABC):
    """
    Abstract base class for all bank scrapers.
    It defines the common interface for authentication and data extraction.
    """

    def __init__(
        self,
        headless: Optional[bool] = None,
        debug_mode: Optional[bool] = None,
        debug_dir: str = "debug_output",
    ):
        """
        Initializes the base scraper.

        Args:
            headless (bool): If True, the browser runs without a graphical interface.
            debug_mode (bool): If True, saves screenshots and HTML for debugging.
            debug_dir (str): Directory where debug files will be saved.
        """
        self.debug_mode = settings.DEBUG if debug_mode is None else debug_mode
        self.headless = settings.SCRAPER_HEADLESS_MODE if headless is None else headless
        self.debug_dir = debug_dir
        if self.debug_mode:
            os.makedirs(self.debug_dir, exist_ok=True)
            self.headless = False

        self.default_timeout = settings.SCRAPER_DEFAULT_TIMEOUT
        self.slow_mo = settings.SCRAPER_SLOW_MO
        self.user_agent = settings.SCRAPER_USER_AGENT
        self.viewport = {
            "width": settings.SCRAPER_VIEWPORT_WIDTH,
            "height": settings.SCRAPER_VIEWPORT_HEIGHT,
        }
        self.locale = settings.SCRAPER_LOCALE
        self.timezone_id = settings.SCRAPER_TIMEZONE_ID
        self.min_human_delay_ms = settings.SCRAPER_MIN_HUMAN_DELAY_MS
        self.max_human_delay_ms = settings.SCRAPER_MAX_HUMAN_DELAY_MS

        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.user: Optional[str] = None
        self.password: Optional[str] = None

    @abstractmethod
    def _get_bank_id(self) -> str:
        """Returns the unique bank identifier (e.g., 'cl_santander')."""
        pass

    @abstractmethod
    def _login(self) -> None:
        """Implements the bank-specific login logic."""
        pass

    @abstractmethod
    def _scrape_movements(self) -> List[MovementModel]:
        """Implements the bank-specific movement extraction logic."""
        pass

    def _ensure_page(self) -> Page:
        """Ensures the page object is initialized, raising an error if not."""
        if not self.page:
            raise DataExtractionError(
                "Page not initialized. Scraper might not have been started correctly."
            )
        return self.page

    def _human_delay(
        self,
        min_override_ms: Optional[float] = None,
        max_override_ms: Optional[float] = None,
    ) -> None:
        """Waits for a random time to simulate human behavior."""
        min_d = (
            min_override_ms if min_override_ms is not None else self.min_human_delay_ms
        )
        max_d = (
            max_override_ms if max_override_ms is not None else self.max_human_delay_ms
        )
        if min_d <= 0 and max_d <= 0:
            return
        delay_seconds = random.uniform(
            min(min_d, max_d) / 1000.0, max(min_d, max_d) / 1000.0
        )
        logger.trace(f"Applying human delay: {delay_seconds:.3f} seconds.")
        time.sleep(delay_seconds)

    def _navigate(self, url: str, timeout_override: Optional[int] = None) -> None:
        """Navigates to a URL with error handling and human-like delay."""
        page = self._ensure_page()
        timeout = (
            timeout_override if timeout_override is not None else self.default_timeout
        )
        logger.debug(f"Navigating to {url} with timeout {timeout}ms.")
        try:
            page.goto(url, timeout=timeout)
            self._human_delay()
        except PlaywrightTimeoutError as e:
            logger.error(f"Timeout navigating to {url}: {e}")
            raise DataExtractionError(f"Timeout navigating to {url}")
        except Exception as e:
            logger.error(f"Error navigating to {url}: {e}", exc_info=self.debug_mode)
            raise DataExtractionError(f"Error navigating to {url}: {e}")

    def _click(
        self,
        selector: Union[str, Locator],
        timeout_override: Optional[int] = None,
        *,
        force: bool = False,
        skip_hover: bool = False,
    ) -> None:
        """Clicks an element with error handling and human-like interaction.

        Args:
            selector: Locator or selector string to click.
            timeout_override: Optional timeout in milliseconds.
            force: If True, forces the click even if the element is covered.
            skip_hover: If True, skips the hover step before clicking. Automatically
                enabled when ``force`` is True because hover checks would fail if
                the element is covered by overlays.
        """
        page = self._ensure_page()
        timeout = (
            timeout_override if timeout_override is not None else self.default_timeout
        )
        logger.debug(f"Clicking selector '{str(selector)}' with timeout {timeout}ms.")
        try:
            element = page.locator(selector) if isinstance(selector, str) else selector
            target = element.first
            target.wait_for(state="visible", timeout=timeout)
            try:
                target.scroll_into_view_if_needed(timeout=timeout)
            except PlaywrightTimeoutError:
                logger.debug(
                    f"Unable to scroll '{str(selector)}' into view before clicking."
                )

            effective_force = force
            perform_hover = not (skip_hover or effective_force)

            if perform_hover:
                try:
                    target.hover(timeout=timeout)
                except PlaywrightTimeoutError as hover_error:
                    logger.debug(
                        f"Hover failed for selector '{str(selector)}': {hover_error}. Retrying click with force."
                    )
                    effective_force = True
                    perform_hover = False

            if perform_hover:
                self._human_delay(min_override_ms=50, max_override_ms=150)
            else:
                self._human_delay(min_override_ms=30, max_override_ms=80)

            try:
                target.click(timeout=timeout, force=effective_force)
            except PlaywrightTimeoutError as click_error:
                if not effective_force:
                    logger.debug(
                        f"Click timeout for selector '{str(selector)}'. Retrying with force."
                    )
                    target.click(timeout=timeout, force=True)
                else:
                    raise click_error

            self._human_delay()
        except PlaywrightTimeoutError as e:
            logger.error(f"Timeout clicking selector '{str(selector)}': {e}")
            raise DataExtractionError(f"Timeout clicking selector '{str(selector)}'")
        except Exception as e:
            logger.error(
                f"Error clicking selector '{str(selector)}': {e}",
                exc_info=self.debug_mode,
            )
            raise DataExtractionError(f"Error clicking selector '{str(selector)}': {e}")

    def _fill(
        self,
        selector: Union[str, Locator],
        text: str,
        delay: int = 50,
        timeout_override: Optional[int] = None,
    ) -> None:
        """Fills an input by clearing it and then typing character by character."""
        page = self._ensure_page()
        timeout = (
            timeout_override if timeout_override is not None else self.default_timeout
        )
        logger.debug(
            f"Filling selector '{str(selector)}' by typing with delay {delay}ms."
        )
        try:
            element = page.locator(selector) if isinstance(selector, str) else selector
            element.first.wait_for(state="visible", timeout=timeout)
            # Clear the input first, then type to simulate human behavior.
            element.first.fill("", timeout=timeout)
            element.first.type(text, delay=delay, timeout=timeout)
            self._human_delay()
        except PlaywrightTimeoutError as e:
            logger.error(f"Timeout filling selector '{str(selector)}': {e}")
            raise DataExtractionError(f"Timeout filling selector '{str(selector)}'")
        except Exception as e:
            logger.error(
                f"Error filling selector '{str(selector)}': {e}",
                exc_info=self.debug_mode,
            )
            raise DataExtractionError(f"Error filling selector '{str(selector)}': {e}")

    def _type(
        self,
        selector: Union[str, Locator],
        text: str,
        delay: int = 100,
        timeout_override: Optional[int] = None,
    ) -> None:
        """Types text into an element character by character."""
        page = self._ensure_page()
        timeout = (
            timeout_override if timeout_override is not None else self.default_timeout
        )
        logger.debug(f"Typing into selector '{str(selector)}'.")
        try:
            element = page.locator(selector) if isinstance(selector, str) else selector
            element.first.wait_for(state="visible", timeout=timeout)
            element.first.type(text, delay=delay, timeout=timeout)
            self._human_delay()
        except PlaywrightTimeoutError as e:
            logger.error(f"Timeout typing into selector '{str(selector)}': {e}")
            raise DataExtractionError(f"Timeout typing into selector '{str(selector)}'")
        except Exception as e:
            logger.error(
                f"Error typing into selector '{str(selector)}': {e}",
                exc_info=self.debug_mode,
            )
            raise DataExtractionError(
                f"Error typing into selector '{str(selector)}': {e}"
            )

    def _wait_for_selector(
        self,
        selector: Union[str, Locator],
        state: Literal["attached", "detached", "hidden", "visible"] = "visible",
        timeout_override: Optional[int] = None,
    ) -> Locator:
        """Waits for a selector to be in a specific state."""
        page = self._ensure_page()
        timeout = (
            timeout_override if timeout_override is not None else self.default_timeout
        )
        logger.debug(
            f"Waiting for selector '{str(selector)}' (state: {state}) with timeout {timeout}ms."
        )
        try:
            element = page.locator(selector) if isinstance(selector, str) else selector
            element.first.wait_for(state=state, timeout=timeout)
            return element
        except PlaywrightTimeoutError as e:
            logger.error(f"Timeout waiting for selector '{str(selector)}': {e}")
            raise DataExtractionError(f"Timeout waiting for selector '{str(selector)}'")
        except Exception as e:
            logger.error(
                f"Error waiting for selector '{str(selector)}': {e}",
                exc_info=self.debug_mode,
            )
            raise DataExtractionError(
                f"Error waiting for selector '{str(selector)}': {e}"
            )

    def _save_debug_info(self, step_name: str) -> None:
        """Saves a screenshot and the current page's HTML for debugging."""
        if not self.debug_mode or not self.page:
            return

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        bank_id = self._get_bank_id()
        debug_path = os.path.join(self.debug_dir, bank_id)
        os.makedirs(debug_path, exist_ok=True)

        screenshot_path = os.path.join(debug_path, f"{timestamp}_{step_name}.png")
        html_path = os.path.join(debug_path, f"{timestamp}_{step_name}.html")

        try:
            self.page.screenshot(path=screenshot_path, full_page=True)
            logger.debug(f"Screenshot saved to: {screenshot_path}")
        except Exception as e:
            logger.warning(f"Could not save screenshot for {step_name}: {e}")

        try:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(self.page.content())
            logger.debug(f"HTML saved to: {html_path}")
        except Exception as e:
            logger.warning(f"Could not save HTML for {step_name}: {e}")

    def scrape(self, user: str, password: str) -> List[MovementModel]:
        """
        Executes the entire scraping process: starts the browser,
        logs in, and extracts the data.
        """
        self.user = user
        self.password = password

        with sync_playwright() as p:
            self.playwright = p
            try:
                logger.info(
                    f"Launching browser for {self._get_bank_id()} (headless: {self.headless})..."
                )
                launch_options = {"headless": self.headless, "slow_mo": self.slow_mo}
                if self.headless:
                    # The default headless mode uses Playwright's lightweight
                    # "headless shell" binary, which some banks (e.g. Banco de
                    # Chile) serve a degraded page to. The "chromium" channel
                    # runs the full browser binary in new-headless mode, which
                    # behaves like a headed session.
                    launch_options["channel"] = "chromium"
                try:
                    self.browser = self.playwright.chromium.launch(**launch_options)
                except Exception:
                    # Fall back to the default binary if the full Chromium
                    # channel is not installed on this machine.
                    launch_options.pop("channel", None)
                    self.browser = self.playwright.chromium.launch(**launch_options)

                context_options = {
                    "user_agent": self.user_agent,
                    "viewport": self.viewport,
                    "locale": self.locale,
                    "timezone_id": self.timezone_id,
                }
                context = self.browser.new_context(**context_options)
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )
                self.page = context.new_page()
                self.page.set_default_timeout(self.default_timeout)

                logger.info(f"Logging into {self._get_bank_id()}...")
                self._login()
                logger.info(f"Successfully logged into {self._get_bank_id()}.")

                logger.info(f"Extracting movements from {self._get_bank_id()}...")
                movements = self._scrape_movements()
                logger.info(
                    f"Extraction of {len(movements)} movements completed for {self._get_bank_id()}."
                )

                return movements

            except (LoginError, DataExtractionError):
                if self.page:
                    self._save_debug_info("scraping_error")
                raise
            except Exception as e:
                logger.error(
                    f"Unexpected error during scraping for {self._get_bank_id()}: {e}",
                    exc_info=True,
                )
                if self.page:
                    self._save_debug_info("unexpected_error")
                raise
            finally:
                if self.browser:
                    self.browser.close()
                    logger.info(f"Browser closed for {self._get_bank_id()}.")
        return []
