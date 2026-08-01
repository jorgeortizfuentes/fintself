"""Tests for the Banco Falabella scraper."""

import re
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from fintself.scrapers.cl.falabella import FalabellaScraper


@pytest.fixture
def scraper() -> FalabellaScraper:
    s = FalabellaScraper.__new__(FalabellaScraper)
    s.debug_mode = False
    s.default_timeout = 30000
    s.min_human_delay_ms = 0
    s.max_human_delay_ms = 0
    s.user = None
    s.password = None
    s.playwright = None
    s.browser = None
    s.page = MagicMock()
    return s


def test_bank_id(scraper: FalabellaScraper):
    assert scraper._get_bank_id() == "cl_falabella"


class TestRowToCheckingMovement:
    def test_cargo(self, scraper: FalabellaScraper):
        movement = scraper._row_to_checking_movement(
            {
                "date": "15/03/2026",
                "description": "Compra supermercado",
                "amount_str": "-12.500",
                "balance_str": "100.000",
            }
        )
        assert movement is not None
        assert movement.date == datetime(2026, 3, 15)
        assert movement.description == "Compra supermercado"
        assert movement.amount == Decimal("-12500")
        assert movement.account_type == "corriente"
        assert movement.transaction_type == "Cargo"

    def test_with_account_id(self, scraper: FalabellaScraper):
        movement = scraper._row_to_checking_movement(
            {
                "date": "01/01/2026",
                "description": "Abono",
                "amount_str": "1.000",
                "balance_str": "",
            },
            account_id="3380",
        )
        assert movement is not None
        assert movement.account_id == "3380"

    def test_spaced_account_number_last4(self):
        # "1 001 371338 0" -> digits 10013713380 -> last 4 = 3380
        digits = re.sub(r"\D", "", "1 001 371338 0")
        assert digits[-4:] == "3380"


    def test_abono(self, scraper: FalabellaScraper):
        movement = scraper._row_to_checking_movement(
            {
                "date": "01/01/2026",
                "description": "Transferencia recibida",
                "amount_str": "50.000",
                "balance_str": "",
            }
        )
        assert movement is not None
        assert movement.amount == Decimal("50000")
        assert movement.transaction_type == "Abono"

    def test_invalid_date_returns_none(self, scraper: FalabellaScraper):
        assert (
            scraper._row_to_checking_movement(
                {
                    "date": "no-date",
                    "description": "x",
                    "amount_str": "1000",
                    "balance_str": "",
                }
            )
            is None
        )


class TestExtractCmrMask:
    def test_mask_from_card_detail(self, scraper: FalabellaScraper):
        card = MagicMock()
        card.count.return_value = 1
        card.inner_text.return_value = "CMR Mastercard Premium\n• • • • 7723"
        scraper.page.locator.return_value.first = card
        assert scraper._extract_cmr_mask() == "7723"

    def test_mask_from_body_fallback(self, scraper: FalabellaScraper):
        card = MagicMock()
        card.count.return_value = 0

        def _locator(sel):
            loc = MagicMock()
            if sel == "body":
                loc.inner_text.return_value = (
                    "CMR Mastercard Premium\n• • • • 7723\nCupo de compras"
                )
            else:
                loc.first = card
            return loc

        scraper.page.locator.side_effect = _locator
        assert scraper._extract_cmr_mask() == "7723"

    def test_mask_missing(self, scraper: FalabellaScraper):
        card = MagicMock()
        card.count.return_value = 0

        def _locator(sel):
            loc = MagicMock()
            if sel == "body":
                loc.inner_text.return_value = "Sin productos"
            else:
                loc.first = card
            return loc

        scraper.page.locator.side_effect = _locator
        assert scraper._extract_cmr_mask() is None


class TestRowToCreditMovement:
    def test_unbilled_charge(self, scraper: FalabellaScraper):
        movement = scraper._row_to_credit_movement(
            {
                "date": "10/02/2026",
                "description": "FALABELLA RETAIL",
                "amount_str": "-25.990",
                "owner": "Titular",
                "installments": "1/1",
            },
            account_id="4321",
            status="unbilled",
        )
        assert movement is not None
        assert movement.account_type == "credito"
        assert movement.account_id == "4321"
        assert movement.amount == Decimal("-25990")
        assert movement.raw_data["status"] == "unbilled"

    def test_positive_amount_forced_negative(self, scraper: FalabellaScraper):
        movement = scraper._row_to_credit_movement(
            {
                "date": "10/02/2026",
                "description": "CMR COMPRA",
                "amount_str": "1.000",
                "owner": "",
                "installments": "",
            },
            account_id=None,
            status="billed",
        )
        assert movement is not None
        assert movement.amount == Decimal("-1000")

    def test_empty_date_skipped(self, scraper: FalabellaScraper):
        assert (
            scraper._row_to_credit_movement(
                {
                    "date": "",
                    "description": "Pendiente",
                    "amount_str": "-500",
                    "owner": "",
                    "installments": "",
                },
                account_id=None,
                status="unbilled",
            )
            is None
        )
