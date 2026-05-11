"""Smoke tests for core/browser.py — unit-level, no real browser needed."""
from core.browser import BrowserEngine, retry_on_failure
from playwright.sync_api import TimeoutError as PlaywrightTimeout
import logging

logging.basicConfig(level=logging.INFO)


def test_retry_on_failure_success():
    result = retry_on_failure(lambda: 42, max_retries=2)
    assert result == 42


def test_retry_on_failure_raises_after_exhaustion():
    called = [0]
    def fail():
        called[0] += 1
        raise PlaywrightTimeout("intentional")
    try:
        retry_on_failure(fail, max_retries=2, retry_delay=1)
        assert False, "Should have raised"
    except PlaywrightTimeout:
        assert called[0] == 3


def test_browser_engine_defaults():
    engine = BrowserEngine(headless=True, slow_mo=0)
    assert engine.headless is True
    assert engine.slow_mo == 0
    assert engine.timeout == 30000


def test_screenshot_dir_resolution():
    engine = BrowserEngine(headless=True, slow_mo=0)
    sd = engine.screenshot_dir
    assert "assets" in sd and "screenshots" in sd


def test_page_property_raises_before_start():
    engine = BrowserEngine(headless=True, slow_mo=0)
    try:
        _ = engine.page
        assert False, "Should have raised"
    except RuntimeError:
        pass
