"""Shared pytest fixtures for fintself tests."""

from __future__ import annotations

from typing import Iterator

import pytest
from playwright.sync_api import Browser, Page, sync_playwright


@pytest.fixture(scope="session")
def _playwright() -> Iterator:
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def chromium_browser(_playwright) -> Iterator[Browser]:
    browser = _playwright.chromium.launch(headless=True)
    try:
        yield browser
    finally:
        browser.close()


@pytest.fixture
def fixture_page(chromium_browser: Browser) -> Iterator[Page]:
    """A fresh Playwright page per test; load fixture HTML via ``page.set_content``."""
    ctx = chromium_browser.new_context(locale="es-CL")
    page = ctx.new_page()
    try:
        yield page
    finally:
        ctx.close()
