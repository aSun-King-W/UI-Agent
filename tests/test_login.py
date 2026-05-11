"""Smoke tests for core/login.py — unit-level, no real browser needed."""
from unittest.mock import MagicMock, patch
import tempfile
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from core.login import TaobaoLogin


def make_mock_page():
    page = MagicMock()
    page.context = MagicMock()
    page.context.cookies.return_value = []
    page.context.add_cookies.return_value = None
    return page


def test_no_cookie_file_returns_false():
    page = make_mock_page()
    login = TaobaoLogin(page)
    with patch("core.login.COOKIE_FILE") as mc:
        mc.exists.return_value = False
        assert login.is_logged_in() is False


def test_constructor():
    page = make_mock_page()
    login = TaobaoLogin(page)
    assert login.page is page
    assert login.logged_in is False


def test_login_failure_returns_false():
    page = make_mock_page()
    login = TaobaoLogin(page)
    page.goto.side_effect = Exception("mock failure")
    assert login.login("user", "pass") is False


def test_clear_cookies():
    page = make_mock_page()
    login = TaobaoLogin(page)
    tmp = Path(tempfile.mktemp(suffix=".json"))
    tmp.write_text("[]")
    with patch("core.login.COOKIE_FILE", tmp):
        login.clear_cookies()
        assert not tmp.exists()


def test_detect_logged_in_false():
    page = make_mock_page()
    def always_raise(*args, **kwargs):
        raise PlaywrightTimeout("not found")
    page.wait_for_selector.side_effect = always_raise
    login = TaobaoLogin(page)
    assert login._detect_logged_in() is False
