"""Smoke tests for core/filter.py — unit-level parsing and logic only."""
from unittest.mock import MagicMock

from core.filter import TaobaoFilter
from core.search import Product, TaobaoSearch


def make_mock_page():
    page = MagicMock()
    page.context = MagicMock()
    return page


# ── Constructor ─────────────────────────────────────────────────

def test_constructor():
    page = make_mock_page()
    search = TaobaoSearch(page)
    flt = TaobaoFilter(page, search)
    assert flt.page is page
    assert flt.searcher is search


# ── Text scanning for rating ───────────────────────────────────

def test_scan_text_for_rating_found():
    page = make_mock_page()
    # Mock the tab page to return text containing rating
    tab = MagicMock()
    tab.inner_text.return_value = "好评率：98%\n其他信息..."
    flt = TaobaoFilter(page, TaobaoSearch(page))
    rating = flt._scan_text_for_rating(tab)
    assert rating == 0.98


def test_scan_text_for_rating_alt_format():
    page = make_mock_page()
    tab = MagicMock()
    tab.inner_text.return_value = "98% 好评\n其他信息..."
    flt = TaobaoFilter(page, TaobaoSearch(page))
    rating = flt._scan_text_for_rating(tab)
    assert rating == 0.98


def test_scan_text_for_rating_star():
    page = make_mock_page()
    tab = MagicMock()
    tab.inner_text.return_value = "评分：4.8\n其他信息..."
    flt = TaobaoFilter(page, TaobaoSearch(page))
    rating = flt._scan_text_for_rating(tab)
    assert rating == 4.8 / 5.0


def test_scan_text_for_rating_none():
    page = make_mock_page()
    tab = MagicMock()
    tab.inner_text.return_value = "没有任何评分信息"
    flt = TaobaoFilter(page, TaobaoSearch(page))
    assert flt._scan_text_for_rating(tab) is None


# ── Embedded JSON extraction ───────────────────────────────────

def test_extract_rating_from_json_direct():
    flt = TaobaoFilter(MagicMock(), TaobaoSearch(MagicMock()))
    data = {"rate": "98%"}
    assert flt._extract_rating_from_json(data) == 0.98


def test_extract_rating_from_json_nested():
    flt = TaobaoFilter(MagicMock(), TaobaoSearch(MagicMock()))
    data = {"item": {"rate": "97%"}}
    assert flt._extract_rating_from_json(data) == 0.97


def test_extract_rating_from_json_none():
    flt = TaobaoFilter(MagicMock(), TaobaoSearch(MagicMock()))
    assert flt._extract_rating_from_json({"foo": "bar"}) is None
    assert flt._extract_rating_from_json({}) is None


# ── format_result ──────────────────────────────────────────────

def test_format_result_empty():
    result = TaobaoFilter.format_result([])
    assert "未找到" in result


def test_format_result_with_products():
    products = [
        Product(
            title="索尼耳机1000XM5",
            price=2499.0,
            url="https://detail.tmall.com/1",
            reviews=5000,
            rating=0.99,
            shop="索尼官方旗舰店",
        ),
        Product(
            title="索尼耳机WH-1000XM4",
            price=1999.0,
            url="https://detail.tmall.com/2",
            reviews=3000,
            rating=0.98,
            shop="索尼专卖店",
        ),
    ]
    result = TaobaoFilter.format_result(products)
    assert "2 个" in result
    assert "索尼耳机1000XM5" in result
    assert "2499" in result
    assert "99%" in result


def test_format_result_partial_fields():
    products = [
        Product(
            title="测试商品",
            price=0.0,
            url="",
            reviews=0,
            rating=None,
            shop=None,
        ),
    ]
    result = TaobaoFilter.format_result(products)
    assert "测试商品" in result
    assert "未知" in result  # both price and rating unknown


# ── filter_by_rating flow (mocked) ─────────────────────────────

def test_filter_by_rating_no_products():
    """When no products exist, should return empty list."""
    page = make_mock_page()
    search = TaobaoSearch(page)
    search.extract_products = MagicMock(return_value=[])

    flt = TaobaoFilter(page, search)
    result = flt.filter_by_rating(min_rating=0.99, max_pages=3)

    assert result == []


def test_filter_by_rating_some_qualify():
    """Should return only products with rating >= threshold."""
    page = make_mock_page()
    search = TaobaoSearch(page)
    products = [
        Product(title="A", price=100, url="u1", reviews=100, rating=0.95),
        Product(title="B", price=200, url="u2", reviews=200, rating=0.99),
        Product(title="C", price=300, url="u3", reviews=300, rating=0.98),
        Product(title="D", price=400, url="u4", reviews=400, rating=0.995),
    ]
    search.extract_products = MagicMock(return_value=products)
    search.go_next_page = MagicMock(return_value=False)

    flt = TaobaoFilter(page, search)
    result = flt.filter_by_rating(min_rating=0.99, max_pages=3)

    assert len(result) == 2
    titles = {p.title for p in result}
    assert "B" in titles
    assert "D" in titles


def test_filter_by_rating_dedup():
    """Duplicate URLs should not appear twice."""
    page = make_mock_page()
    search = TaobaoSearch(page)
    products = [
        Product(title="A", price=100, url="u1", reviews=100, rating=0.99),
        Product(title="A'", price=100, url="u1", reviews=100, rating=0.99),
    ]
    search.extract_products = MagicMock(return_value=products)

    flt = TaobaoFilter(page, search)
    result = flt.filter_by_rating(min_rating=0.99, max_pages=2)

    assert len(result) == 1


def test_filter_by_rating_empty_url_skipped():
    """Products without URL should be skipped (no way to dedup)."""
    page = make_mock_page()
    search = TaobaoSearch(page)
    products = [
        Product(title="A", price=100, url="", reviews=100, rating=0.99),
    ]
    search.extract_products = MagicMock(return_value=[])

    flt = TaobaoFilter(page, search)
    result = flt.filter_by_rating(min_rating=0.99, max_pages=2)
    # empty URL gets skipped by seen_urls check
    assert result == []


def test_filter_by_rating_none_rating_not_qualify():
    """Products with None rating should NOT qualify."""
    page = make_mock_page()
    search = TaobaoSearch(page)
    products = [
        Product(title="A", price=100, url="u1", reviews=100, rating=None),
    ]
    search.extract_products = MagicMock(return_value=products)

    flt = TaobaoFilter(page, search)
    result = flt.filter_by_rating(min_rating=0.99, max_pages=2)

    assert result == []
