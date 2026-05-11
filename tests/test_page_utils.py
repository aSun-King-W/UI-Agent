"""Smoke tests for core/page_utils.py — unit-level logic only."""
from unittest.mock import MagicMock

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from core.page_utils import PageUtils


def make_page(url="https://www.taobao.com/"):
    page = MagicMock()
    page.url = url
    page.title.return_value = "淘宝网"
    return page


# ── Constructor ─────────────────────────────────────────────────

def test_constructor():
    page = make_page()
    utils = PageUtils(page)
    assert utils.page is page


# ── Page mode inference ─────────────────────────────────────────

def test_infer_login():
    page = make_page("https://login.taobao.com/")
    utils = PageUtils(page)
    assert utils._infer_page_mode() == "login"


def test_infer_search():
    page = make_page("https://s.taobao.com/search?q=test")
    utils = PageUtils(page)
    assert utils._infer_page_mode() == "search_results"


def test_infer_detail():
    page = make_page("https://item.taobao.com/item.htm?id=123")
    utils = PageUtils(page)
    assert utils._infer_page_mode() == "product_detail"


def test_infer_detail_tmall():
    page = make_page("https://detail.tmall.com/item.htm?id=123")
    utils = PageUtils(page)
    assert utils._infer_page_mode() == "product_detail"


def test_infer_cart():
    page = make_page("https://cart.taobao.com/")
    utils = PageUtils(page)
    assert utils._infer_page_mode() == "cart"


def test_infer_home():
    page = make_page("https://www.taobao.com/")
    utils = PageUtils(page)
    assert utils._infer_page_mode() == "home"


def test_infer_unknown():
    page = make_page("https://www.example.com/")
    utils = PageUtils(page)
    assert utils._infer_page_mode() == "unknown"


# ── get_page_state structure ───────────────────────────────────

def test_get_page_state_keys():
    page = make_page()
    page.query_selector_all.return_value = []
    page.inner_text.return_value = "some text"

    utils = PageUtils(page)
    state = utils.get_page_state()

    assert "url" in state
    assert "title" in state
    assert "mode" in state
    assert "interactive" in state
    assert "headings" in state
    assert "text_preview" in state
    assert "status_messages" in state


# ── wait_for_element ──────────────────────────────────────────

def test_wait_for_element_found():
    page = make_page()
    utils = PageUtils(page)
    assert utils.wait_for_element("button", timeout=100) is True


def test_wait_for_element_not_found():
    page = make_page()
    page.wait_for_selector.side_effect = PlaywrightTimeout("not found")
    utils = PageUtils(page)
    assert utils.wait_for_element(".gone", timeout=100) is False


# ── wait_for_stable ───────────────────────────────────────────

def test_wait_for_stable():
    page = make_page()
    utils = PageUtils(page)
    assert utils.wait_for_stable(timeout=100) is True


def test_wait_for_stable_timeout():
    page = make_page()
    page.wait_for_load_state.side_effect = PlaywrightTimeout("timeout")
    utils = PageUtils(page)
    assert utils.wait_for_stable(timeout=100) is False


# ── wait_for_text ─────────────────────────────────────────────

def test_wait_for_text():
    page = make_page()
    utils = PageUtils(page)
    # Delegates to wait_for_element which returns True by default
    assert utils.wait_for_text("hello", timeout=100) is True


# ── page summary string ───────────────────────────────────────

def test_get_page_summary():
    page = make_page()
    page.query_selector_all.return_value = []
    page.inner_text.return_value = ""

    utils = PageUtils(page)
    summary = utils.get_page_summary()
    assert isinstance(summary, str)
    assert len(summary) > 0


# ── scroll helpers ────────────────────────────────────────────

def test_scroll_to_top():
    page = make_page()
    utils = PageUtils(page)
    utils.scroll_to_top()
    page.evaluate.assert_called_with("window.scrollTo(0, 0)")


def test_scroll_to_bottom():
    page = make_page()
    utils = PageUtils(page)
    utils.scroll_to_bottom()
    page.evaluate.assert_called_with("window.scrollTo(0, document.body.scrollHeight)")


def test_scroll_by():
    page = make_page()
    utils = PageUtils(page)
    utils.scroll_by(dx=0, dy=300)
    page.evaluate.assert_called()


def test_scroll_to_element():
    page = make_page()
    el = MagicMock()
    page.wait_for_selector.return_value = el
    utils = PageUtils(page)
    assert utils.scroll_to_element(".foo") is True
    el.scroll_into_view_if_needed.assert_called_once()


def test_scroll_to_element_not_found():
    page = make_page()
    page.wait_for_selector.side_effect = Exception("not found")
    utils = PageUtils(page)
    assert utils.scroll_to_element(".gone") is False


# ── get_visible_text ──────────────────────────────────────────

def test_get_visible_text():
    page = make_page()
    page.inner_text.return_value = "Hello World"
    utils = PageUtils(page)
    text = utils.get_visible_text()
    assert "Hello World" in text


# ── highlight_screenshot ──────────────────────────────────────

def test_highlight_screenshot_no_element():
    page = make_page()
    page.wait_for_selector.side_effect = Exception("not found")
    utils = PageUtils(page)
    assert utils.highlight_screenshot(".gone") is None


# ── element_screenshot ────────────────────────────────────────

def test_element_screenshot_no_element():
    page = make_page()
    page.wait_for_selector.side_effect = Exception("not found")
    utils = PageUtils(page)
    assert utils.element_screenshot(".gone") is None
