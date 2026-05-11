"""Smoke tests for core/search.py — unit-level parsing logic only."""
from unittest.mock import MagicMock

from core.search import TaobaoSearch, Product, TAOBAO_HOME, TAOBAO_SEARCH


def make_mock_page():
    page = MagicMock()
    page.context = MagicMock()
    return page


# ── Product dataclass ───────────────────────────────────────────

def test_product_defaults():
    p = Product(title="Test", price=99.0, url="https://example.com")
    assert p.reviews == 0
    assert p.rating is None
    assert p.shop is None
    d = p.to_dict()
    assert d["title"] == "Test"
    assert d["price"] == 99.0


# ── Price parsing ───────────────────────────────────────────────

def test_parse_price():
    s = TaobaoSearch(make_mock_page())
    assert s._parse_price("¥99.00") == 99.0
    assert s._parse_price("69.9") == 69.9
    assert s._parse_price("1,234.56") == 1234.56
    assert s._parse_price("0") == 0.0
    assert s._parse_price("abc") == 0.0


# ── Reviews parsing ─────────────────────────────────────────────

def test_parse_reviews():
    s = TaobaoSearch(make_mock_page())
    assert s._parse_reviews("2000人付款") == 2000
    assert s._parse_reviews("5.6万") == 56000
    assert s._parse_reviews("12.8万+") == 128000
    assert s._parse_reviews("0") == 0
    assert s._parse_reviews("abc") == 0


# ── Rating parsing ──────────────────────────────────────────────

def test_parse_rating():
    s = TaobaoSearch(make_mock_page())
    assert s._parse_rating("好评率 98%") == 0.98
    assert s._parse_rating("98%") == 0.98
    assert s._parse_rating("") is None
    assert s._parse_rating("4.9") is None  # ambiguous without %


# ── Auction dict parsing ────────────────────────────────────────

def test_parse_auction():
    s = TaobaoSearch(make_mock_page())
    auc = {
        "title": "索尼耳机 1000XM5",
        "raw_title": "索尼耳机 1000XM5",
        "view_price": "2499.00",
        "detail_url": "//detail.tmall.com/item.htm?id=123",
        "view_sales": "5000人付款",
        "view_rate": "98%",
        "nick": "索尼官方旗舰店",
    }
    p = s._parse_auction(auc)
    assert p is not None
    assert p.title == "索尼耳机 1000XM5"
    assert p.price == 2499.0
    assert "detail.tmall.com" in p.url
    assert p.reviews == 5000
    assert p.rating == 0.98
    assert p.shop == "索尼官方旗舰店"


def test_parse_auction_missing_title():
    s = TaobaoSearch(make_mock_page())
    assert s._parse_auction({"price": "99"}) is None


# ── Auction list parsing ────────────────────────────────────────

def test_parse_auction_list():
    s = TaobaoSearch(make_mock_page())
    auctions = [
        {"title": "A", "raw_title": "A", "view_price": "10"},
        {"title": "B", "raw_title": "B", "view_price": "20"},
        {"title": "C", "raw_title": "C", "view_price": "30"},
        {"title": "D", "raw_title": "D", "view_price": "40"},
    ]
    products = s._parse_auction_list(auctions, max_items=3)
    assert len(products) == 3
    assert products[0].title == "A"
    assert products[2].title == "C"


# ── Embedded items parsing ──────────────────────────────────────

def test_parse_embedded_items_auctions():
    s = TaobaoSearch(make_mock_page())
    data = {
        "auctions": [
            {"title": "X", "raw_title": "X", "view_price": "99"},
        ]
    }
    products = s._parse_embedded_items(data, 10)
    assert len(products) == 1
    assert products[0].title == "X"


def test_parse_embedded_items_general():
    s = TaobaoSearch(make_mock_page())
    data = {"items": [{"title": "Y", "raw_title": "Y", "view_price": "50"}]}
    products = s._parse_embedded_items(data, 10)
    assert len(products) == 1
    assert products[0].price == 50.0


# ── Title cleaning ──────────────────────────────────────────────

def test_clean_title():
    s = TaobaoSearch(make_mock_page())
    assert s._clean_title("  Sony  <span>耳机</span>  ") == "Sony 耳机"


# ── Constructor ─────────────────────────────────────────────────

def test_constructor():
    page = make_mock_page()
    s = TaobaoSearch(page)
    assert s.page is page
    assert s._last_keyword is None
