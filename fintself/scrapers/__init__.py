from typing import Dict, Optional, Type

from fintself.core.exceptions import ScraperNotFound
from fintself.scrapers.base import BaseScraper
from fintself.utils.logging import logger

# Import specific scrapers here so the factory can find them
from .cl import (
    BancoChileScraper,
    BancoEstadoScraper,
    BiceScraper,
    CencosudScraper,
    SantanderScraper,
    ScotiabankScraper,
)

# Dictionary that maps bank IDs to scraper classes
_SCRAPERS: Dict[str, Type[BaseScraper]] = {
    "cl_santander": SantanderScraper,
    "cl_cencosud": CencosudScraper,
    "cl_banco_chile": BancoChileScraper,
    "cl_estado": BancoEstadoScraper,
    "cl_bice": BiceScraper,
    "cl_scotiabank": ScotiabankScraper,
}


def get_scraper(
    bank_id: str, headless: Optional[bool] = None, debug_mode: Optional[bool] = None
) -> BaseScraper:
    """
    Factory function to get a bank scraper instance.

    If headless or debug_mode are not provided, the values will be taken
    from the environment variables defined in settings.

    Args:
        bank_id (str): The unique identifier for the bank (e.g., "cl_santander").
        headless (Optional[bool]): If True, runs headless. If None, uses env setting.
        debug_mode (Optional[bool]): If True, enables debug. If None, uses env setting.

    Returns:
        BaseScraper: An instance of the requested scraper.

    Raises:
        ScraperNotFound: If the bank_id does not match any known scraper.
    """
    scraper_class = _SCRAPERS.get(bank_id)
    if not scraper_class:
        logger.error(f"Scraper '{bank_id}' not found.")
        raise ScraperNotFound(bank_id)

    logger.debug(
        f"Instantiating scraper for '{bank_id}'. "
        f"Debug override: {debug_mode}, Headless override: {headless}"
    )
    return scraper_class(headless=headless, debug_mode=debug_mode)


def list_available_scrapers() -> Dict[str, str]:
    """
    Lists all available bank scrapers.

    Returns:
        Dict[str, str]: A dictionary where the key is the bank_id and the value is a description.
    """
    descriptions = {
        "cl_santander": "Scraper for Banco Santander (Chile).",
        "cl_cencosud": "Scraper for Tarjeta Cencosud Scotiabank (Chile).",
        "cl_banco_chile": "Scraper for Banco de Chile (Chile).",
        "cl_estado": "Scraper for Cuenta RUT Banco Estado (Chile).",
        "cl_bice": "Scraper for Banco Bice (Chile).",
        "cl_scotiabank": "Scraper for Scotiabank (Chile).",
    }

    return {
        bank_id: descriptions.get(
            bank_id, f"Scraper for {bank_id.replace('_', ' ').title()}"
        )
        for bank_id in _SCRAPERS.keys()
    }
