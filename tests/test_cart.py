"""Smoke tests for core/cart.py — unit-level logic only."""
from unittest.mock import MagicMock

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from core.cart import TaobaoCart
from core.search import Product


def make_mock_page():
    page = MagicMock()
    page.context = MagicMock()
    return page


# ── Constructor ─────────────────────────────────────────────────

def test_constructor():
    page = make_mock_page()
    cart = TaobaoCart(page)
    assert cart.page is page


# ── add_to_cart with no URL ────────────────────────────────────

def test_add_to_cart_no_url():
    page = make_mock_page()
    cart = TaobaoCart(page)
    product = Product(title="Test", price=0.0, url="")
    assert cart.add_to_cart(product) is False


# ── add_to_cart failure returns False ──────────────────────────

def test_add_to_cart_failure():
    page = make_mock_page()
    # new_page().goto will raise because it's not set up
    page.context.new_page.return_value = MagicMock()
    page.context.new_page.return_value.goto.side_effect = Exception("mock fail")

    cart = TaobaoCart(page)
    product = Product(
        title="Sony Headphones", price=1999, url="https://item.taobao.com/123"
    )
    assert cart.add_to_cart(product) is False


# ── _dismiss_overlays ──────────────────────────────────────────

def test_dismiss_overlays_no_popup():
    """Should not crash when there are no overlays."""
    page = make_mock_page()
    tab = MagicMock()

    def raise_timeout(*a, **kw):
        raise PlaywrightTimeout("not found")
    tab.wait_for_selector.side_effect = raise_timeout

    cart = TaobaoCart(page)
    cart._dismiss_overlays(tab)  # should not raise


# ── _select_sku ────────────────────────────────────────────────

def test_select_sku_no_groups():
    """Should not crash when there are no SKU groups."""
    page = make_mock_page()
    tab = MagicMock()
    tab.query_selector_all.return_value = []
    cart = TaobaoCart(page)
    cart._select_sku(tab)  # should not raise


# ── _verify_added via popup ────────────────────────────────────

def test_verify_added_popup():
    page = make_mock_page()
    tab = MagicMock()
    popup = MagicMock()
    popup.is_visible.return_value = True
    popup.inner_text.return_value = "成功添加至购物车"

    def side_effect(sel, **kw):
        if "cart-popup" in sel:
            return popup
        raise Exception("not found")

    tab.wait_for_selector.side_effect = side_effect
    cart = TaobaoCart(page)
    assert cart._verify_added(tab) is True


# ── _verify_added no indicator ─────────────────────────────────

def test_verify_added_false():
    """When no success indicator is found, should return False."""
    page = make_mock_page()
    tab = MagicMock()

    def raise_timeout(*a, **kw):
        raise PlaywrightTimeout("not found")
    tab.wait_for_selector.side_effect = raise_timeout

    cart = TaobaoCart(page)
    assert cart._verify_added(tab) is False


# ── _set_quantity (quantity <= 1 is no-op) ─────────────────────

def test_set_quantity_default():
    """Setting quantity to 1 should be a no-op (no fill calls)."""
    page = make_mock_page()
    tab = MagicMock()
    cart = TaobaoCart(page)
    cart._set_quantity(tab, 1)
    tab.wait_for_selector.assert_not_called()


# ── summarize_results ──────────────────────────────────────────

def test_summarize_results_all_success():
    p1 = Product(title="A", price=100, url="u1")
    p2 = Product(title="B", price=200, url="u2")
    results = [(p1, True), (p2, True)]
    report = TaobaoCart.summarize_results(results)
    assert "成功 2 个" in report
    assert "失败 0 个" in report
    assert "✓" in report


def test_summarize_results_mixed():
    p1 = Product(title="A", price=100, url="u1")
    p2 = Product(title="B", price=200, url="u2")
    results = [(p1, True), (p2, False)]
    report = TaobaoCart.summarize_results(results)
    assert "成功 1 个" in report
    assert "失败 1 个" in report
    assert "✓" in report
    assert "✗" in report
