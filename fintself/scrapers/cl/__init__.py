# This file makes the 'cl' directory a Python package.
# It exposes scrapers from this country so they can be imported from other modules.

from .banco_chile import BancoChileScraper
from .bice import BiceScraper
from .cencosud import CencosudScraper
from .estado import BancoEstadoScraper
from .falabella import FalabellaScraper
from .santander import SantanderScraper

__all__ = [
    "BancoChileScraper",
    "BancoEstadoScraper",
    "BiceScraper",
    "CencosudScraper",
    "FalabellaScraper",
    "SantanderScraper",
]
