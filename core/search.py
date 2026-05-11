"""
Product search module for Taobao.

Provides:
- Keyword search (home page input or direct URL)
- Structured product data extraction (title, price, reviews, rating, link)
- Pagination support (next page detection and navigation)
- Multiple extraction strategies with fallbacks
"""

import re
import json
import logging
from dataclasses import dataclass, asdict, field
from typing import List, Optional
from urllib.parse import quote

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger("search")

TAOBAO_HOME = "https://www.taobao.com/"
TAOBAO_SEARCH = "https://s.taobao.com/search?q={keyword}&s=0"


@dataclass
class Product:
    """Represents a single product listing in search results."""

    title: str
    price: float
    url: str
    reviews: int = 0
    rating: Optional[float] = None  # 0-1, None if not found
    shop: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── Search interaction ──────────────────────────────────────────


class TaobaoSearch:
    """Handle Taobao product search and result extraction.

    Usage::

        search = TaobaoSearch(page)
        ok = search.search("索尼耳机")
        if ok:
            products = search.extract_products()
            for p in products:
                print(p.title, p.price)
    """

    def __init__(self, page: Page):
        self.page = page
        self._last_keyword: Optional[str] = None

    # ── Public API ───────────────────────────────────────────────

    def search(self, keyword: str) -> bool:
        """Execute a product search on Taobao.

        Attempts home-page search box first (more realistic), then
        falls back to direct search-results URL navigation.

        Returns:
            True if the search results page loaded.
        """
        self._last_keyword = keyword
        logger.info("Searching for: %s", keyword)

        try:
            return self._search_via_homepage(keyword)
        except Exception as exc:
            logger.warning(
                "Home-page search failed (%s), using direct URL.", exc
            )
            return self._search_via_direct_url(keyword)

    def extract_products(self, max_items: int = 20) -> List[Product]:
        """Extract product listings from the current search results page.

        Uses a multi-strategy approach:
        1. Look for known product-item container selectors (DOM)
        2. Fallback: extract from embedded JSON in <script> tags
        3. Last resort: parse visible text blocks

        Args:
            max_items: Cap on returned products.

        Returns:
            List of Product objects (may be empty).
        """
        self._wait_for_results()

        products = self._extract_from_dom(max_items)
        if products:
            logger.info("Extracted %d products via DOM strategy.", len(products))
            return products

        products = self._extract_from_embedded_json(max_items)
        if products:
            logger.info("Extracted %d products via embedded JSON.", len(products))
            return products

        products = self._extract_from_text_blocks(max_items)
        if products:
            logger.info("Extracted %d products via text-block parsing.", len(products))
            return products

        logger.warning("No products found on search results page.")
        return []

    def has_next_page(self) -> bool:
        """Check whether a next page of results is available."""
        selectors = [
            ".next a.next",
            "a[aria-label='下一页']",
            ".pagination a.next",
            "//a[contains(text(), '下一页')]",
        ]
        for sel in selectors:
            try:
                el = self.page.wait_for_selector(sel, timeout=3000)
                if el and el.is_visible() and not el.get_attribute("disabled"):
                    return True
            except PlaywrightTimeout:
                continue
        return False

    def go_next_page(self) -> bool:
        """Navigate to the next results page.

        Returns:
            True if navigation succeeded.
        """
        if not self.has_next_page():
            return False

        selectors = [
            ".next a.next",
            "a[aria-label='下一页']",
            ".pagination a.next",
            "//a[contains(text(), '下一页')]",
        ]
        for sel in selectors:
            try:
                el = self.page.wait_for_selector(sel, timeout=3000)
                if el and el.is_visible():
                    el.click()
                    self.page.wait_for_load_state("networkidle", timeout=15000)
                    logger.info("Navigated to next page.")
                    return True
            except PlaywrightTimeout:
                continue
        return False

    def get_page_state_summary(self) -> dict:
        """Return a text summary of the current page for LLM decision-making.

        Returns a dict with:
        - url: current URL
        - title: page title
        - total_results: result count text (if found)
        - visible_text: condensed visible text
        - product_count: number of product-like elements found
        """
        summary = {
            "url": self.page.url,
            "title": self.page.title(),
            "total_results": self._extract_total_results_text(),
            "product_count": len(self._find_product_items()),
        }
        return summary

    # ── Search strategies ────────────────────────────────────────

    def _search_via_homepage(self, keyword: str) -> bool:
        """Navigate to Taobao home, type keyword, click search."""
        self.page.goto(TAOBAO_HOME, wait_until="networkidle")

        # Find search input — try multiple known selectors
        search_input = self._find_search_input()
        if not search_input:
            raise RuntimeError("Search input not found on Taobao home page.")

        search_input.click()
        search_input.fill("")
        search_input.type(keyword, delay=80)

        # Find and click search button
        search_btn = self._find_search_button()
        if search_btn and search_btn.is_visible():
            search_btn.click()
        else:
            # Fallback: press Enter
            self.page.keyboard.press("Enter")

        self.page.wait_for_load_state("networkidle", timeout=20000)
        return True

    def _search_via_direct_url(self, keyword: str) -> bool:
        """Navigate directly to the search results URL."""
        url = TAOBAO_SEARCH.format(keyword=quote(keyword))
        self.page.goto(url, wait_until="networkidle")
        return True

    # ── Extraction strategies ────────────────────────────────────

    def _extract_from_dom(self, max_items: int) -> List[Product]:
        """Strategy 1: find product items via DOM selectors and extract fields."""
        items = self._find_product_items()
        if not items:
            return []

        products = []
        for item in items[:max_items]:
            try:
                product = self._parse_item(item)
                if product and product.title and product.price > 0:
                    products.append(product)
            except Exception as e:
                logger.debug("Skipping item: %s", e)
                continue
        return products

    def _extract_from_embedded_json(self, max_items: int) -> List[Product]:
        """Strategy 2: extract product data from embedded JSON <script> tags."""
        try:
            data = self.page.evaluate("""() => {
                // Try common global data sources
                return window.__INIT_DATA__
                    || window.g_page_config
                    || window.rawData
                    || null;
            }""")
            if not data:
                return []

            items = self._parse_embedded_items(data, max_items)
            if items:
                return items
        except Exception as e:
            logger.debug("Embedded JSON extraction failed: %s", e)

        # Try extracting from script tags with JSON content
        try:
            scripts = self.page.query_selector_all("script")
            for script in scripts:
                content = script.inner_text()
                if not content:
                    continue
                # Look for JSON-like arrays of item data
                for pattern in [
                    r'"items":\s*(\[.*?\])',
                    r'"auctions":\s*(\[.*?\])',
                    r'"itemList":\s*(\[.*?\])',
                ]:
                    match = re.search(pattern, content, re.DOTALL)
                    if match:
                        try:
                            raw = json.loads(match.group(1))
                            return self._parse_auction_list(raw, max_items)
                        except (json.JSONDecodeError, Exception):
                            continue
        except Exception as e:
            logger.debug("Script-tag extraction failed: %s", e)

        return []

    def _extract_from_text_blocks(self, max_items: int) -> List[Product]:
        """Strategy 3: parse visible text blocks (fallback)."""
        try:
            body_text = self.page.inner_text("body")
        except Exception:
            return []

        products = []
        # Split by common item separators
        blocks = re.split(r"\n{2,}", body_text)

        for block in blocks[:max_items]:
            block = block.strip()
            if not block or len(block) < 20:
                continue

            title = self._guess_title(block)
            if not title:
                continue

            price = self._guess_price(block)
            reviews = self._guess_reviews(block)
            rating = self._guess_rating(block)

            products.append(
                Product(
                    title=title,
                    price=price,
                    url="",
                    reviews=reviews,
                    rating=rating,
                )
            )
        return products

    # ── DOM helpers ──────────────────────────────────────────────

    def _find_search_input(self):
        """Locate the Taobao search input box."""
        selectors = [
            "#q",
            "input.search-combobox-input",
            "input[placeholder*='搜索']",
            "input[placeholder*='淘宝']",
        ]
        for sel in selectors:
            try:
                el = self.page.wait_for_selector(sel, timeout=5000)
                if el and el.is_visible():
                    return el
            except PlaywrightTimeout:
                continue
        return None

    def _find_search_button(self):
        """Locate the Taobao search submit button."""
        selectors = [
            ".btn-search",
            "button[type='submit']",
            ".search-button",
        ]
        for sel in selectors:
            try:
                el = self.page.wait_for_selector(sel, timeout=3000)
                if el and el.is_visible():
                    return el
            except PlaywrightTimeout:
                continue
        return None

    def _find_product_items(self):
        """Return all product item elements visible on the page."""
        selectors = [
            "#mainsrp-itemlist .items .item",
            ".m-itemlist .items .item",
            "#J_ItemList .item",
            ".grid-item",
            "div[data-index]",
            ".item-card",
        ]
        for sel in selectors:
            try:
                items = self.page.query_selector_all(sel)
                visible = [i for i in items if i.is_visible()]
                if visible:
                    logger.debug(
                        "Found %d product items via: %s", len(visible), sel
                    )
                    return visible
            except Exception:
                continue
        return []

    def _parse_item(self, item) -> Optional[Product]:
        """Extract Product fields from a single DOM item element."""
        # Title & URL
        title_el = self._child(item, [
            ".title a",
            ".title",
            "h3 a",
            "a[title]",
        ])
        title = ""
        url = ""
        if title_el:
            title = (title_el.get_attribute("title")
                     or title_el.inner_text() or "").strip()
            href = title_el.get_attribute("href") or ""
            if href and not href.startswith("http"):
                href = "https:" + href if href.startswith("//") else href
            url = href

        # Price
        price = 0.0
        price_el = self._child(item, [
            ".price",
            ".g_price",
            ".price strong",
            "span.price",
        ])
        if price_el:
            price_text = price_el.inner_text().strip()
            price = self._parse_price(price_text)

        # Reviews (评价数 / 付款人数)
        reviews = 0
        review_el = self._child(item, [
            ".deal-cnt",
            ".pay-num",
        ])
        if review_el:
            reviews = self._parse_reviews(review_el.inner_text())

        # Rating (好评率)
        rating = None
        rating_el = self._child(item, [
            ".rating",
            ".star",
        ])
        if rating_el:
            rating = self._parse_rating(rating_el.inner_text())

        # Shop name
        shop = None
        shop_el = self._child(item, [
            ".shop",
            ".shop a",
            ".seller",
        ])
        if shop_el:
            shop = shop_el.inner_text().strip()

        return Product(
            title=title,
            price=price,
            url=url,
            reviews=reviews,
            rating=rating,
            shop=shop,
        )

    def _child(self, parent, selectors: List[str]):
        """Find the first matching child element from multiple selectors."""
        for sel in selectors:
            try:
                el = parent.query_selector(sel)
                if el:
                    return el
            except Exception:
                continue
        return None

    # ── Embedded JSON parsing ────────────────────────────────────

    def _parse_embedded_items(self, data: dict, max_items: int) -> List[Product]:
        """Try to extract products from various known JSON structures."""
        # Taobao: g_page_config.auctions[]
        auctions = self._nested_get(data, ["auctions"])
        if auctions and isinstance(auctions, list):
            return self._parse_auction_list(auctions, max_items)

        # General: items[] or itemList[]
        for key in ["items", "itemList", "products", "data"]:
            items = data.get(key, [])
            if items and isinstance(items, list):
                return self._parse_auction_list(items, max_items)

        # Nested under props
        items = self._nested_get(data, [
            "props", "pageProps", "items",
        ])
        if items and isinstance(items, list):
            return self._parse_auction_list(items, max_items)

        return []

    def _parse_auction_list(self, auctions: list, max_items: int) -> List[Product]:
        """Parse a list of auction/item dicts into Product objects."""
        products = []
        for auc in auctions[:max_items]:
            if not isinstance(auc, dict):
                continue
            try:
                product = self._parse_auction(auc)
                if product and product.title:
                    products.append(product)
            except Exception:
                continue
        return products

    def _parse_auction(self, auc: dict) -> Optional[Product]:
        """Parse a single auction dict into a Product."""
        title = (auc.get("title") or auc.get("raw_title") or "").strip()
        if not title:
            return None

        # Price
        price = self._parse_price(
            str(auc.get("view_price") or auc.get("price") or auc.get("priceText") or "0")
        )

        # URL
        url = ""
        for key in ["detail_url", "url", "item_url", "link"]:
            val = auc.get(key)
            if val:
                url = val if val.startswith("http") else "https:" + val
                break

        # Reviews
        reviews = 0
        for key in ["view_sales", "sales", "deal_cnt", "reviewCount", "pay_num"]:
            val = auc.get(key)
            if val is not None:
                reviews = self._parse_reviews(str(val))
                break

        # Rating
        rating = None
        for key in ["view_rate", "rating", "rate", "goodRate"]:
            val = auc.get(key)
            if val is not None:
                rating = self._parse_rating(str(val))
                if rating is not None:
                    break

        # Shop
        shop = auc.get("nick") or auc.get("shopName") or auc.get("shop") or None

        return Product(
            title=self._clean_title(title),
            price=price,
            url=url,
            reviews=reviews,
            rating=rating,
            shop=shop,
        )

    # ── Text parsing helpers ─────────────────────────────────────

    @staticmethod
    def _parse_price(text: str) -> float:
        """Extract a numeric price from text like '¥99.00' or '69.9'."""
        match = re.search(r"(\d+\.?\d*)", text.replace(",", ""))
        return float(match.group(1)) if match else 0.0

    @staticmethod
    def _parse_reviews(text: str) -> int:
        """Extract review/deal count from text like '2000人付款' or '5.6万'."""
        text = text.replace(",", "").replace(" ", "")
        match = re.search(r"(\d+\.?\d*)\s*万", text)
        if match:
            return int(float(match.group(1)) * 10000)
        match = re.search(r"(\d+)", text)
        return int(match.group(1)) if match else 0

    @staticmethod
    def _parse_rating(text: str) -> Optional[float]:
        """Extract rating from text like '好评率 98%' or '4.8'."""
        match = re.search(r"(\d+\.?\d*)\s*%", text)
        if match:
            val = float(match.group(1))
            return val / 100.0 if val > 1 else val
        match = re.search(r"(\d+\.\d+)", text)
        if match:
            val = float(match.group(1))
            return val if val <= 1.0 else None
        return None

    @staticmethod
    def _guess_title(block: str) -> Optional[str]:
        """Heuristic: find a plausible title in a text block."""
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        # Pick the longest line as the title heuristic
        if lines:
            longest = max(lines, key=len)
            return longest if len(longest) > 6 else None
        return None

    @staticmethod
    def _guess_price(block: str) -> float:
        """Heuristic: find first price-like pattern in a text block."""
        match = re.search(r"¥?\s*(\d+\.\d{2})", block)
        if match:
            return float(match.group(1))
        match = re.search(r"(\d+\.\d{2})", block)
        return float(match.group(1)) if match else 0.0

    @staticmethod
    def _guess_reviews(block: str) -> int:
        """Heuristic: find review count in a text block."""
        return TaobaoSearch._parse_reviews(block)

    @staticmethod
    def _guess_rating(block: str) -> Optional[float]:
        """Heuristic: find rating in a text block."""
        return TaobaoSearch._parse_rating(block)

    @staticmethod
    def _clean_title(title: str) -> str:
        """Remove HTML tags and collapse whitespace in title text."""
        title = re.sub(r"<[^>]+>", "", title)
        return re.sub(r"\s+", " ", title).strip()

    @staticmethod
    def _nested_get(data: dict, keys: List[str]):
        """Safely traverse nested dict keys."""
        current = data
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
        return current

    # ── Meta extraction ──────────────────────────────────────────

    def _wait_for_results(self):
        """Wait for search results container to appear after search."""
        selectors = [
            "#mainsrp-itemlist",
            ".m-itemlist",
            "#J_ItemList",
            ".grid-item",
        ]
        for sel in selectors:
            try:
                self.page.wait_for_selector(sel, timeout=15000)
                return
            except PlaywrightTimeout:
                continue
        logger.debug("Search results container not detected.")

    def _extract_total_results_text(self) -> Optional[str]:
        """Extract the 'total results' text shown on the search page."""
        selectors = [
            ".total",
            ".result-count",
            ".search-result",
        ]
        for sel in selectors:
            try:
                el = self.page.wait_for_selector(sel, timeout=3000)
                if el:
                    return el.inner_text().strip()
            except PlaywrightTimeout:
                continue
        return None
