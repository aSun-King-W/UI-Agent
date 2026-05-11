"""
Intelligent product filtering module for Taobao.

Provides:
- Rating-based product filtering with multi-page scan
- Product detail page rating extraction (fallback)
- Deduplication and progress reporting
"""

import logging
import re
import time
from typing import List, Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from core.search import Product, TaobaoSearch

logger = logging.getLogger("filter")

# Delay between detail-page visits to avoid triggering anti-detection
DETAIL_PAGE_DELAY = (1.0, 2.5)


class TaobaoFilter:
    """Filter Taobao search results by rating criteria.

    Scans search result pages and collects products that meet a minimum
    rating threshold.  Optionally visits product detail pages for
    accurate rating data when search-result ratings are unavailable.

    Usage::

        search = TaobaoSearch(page)
        search.search("索尼耳机")

        flt = TaobaoFilter(page, search)
        qualified = flt.filter_by_rating(min_rating=0.99)
    """

    def __init__(self, page: Page, searcher: TaobaoSearch):
        self.page = page
        self.searcher = searcher

    # ── Public API ───────────────────────────────────────────────

    def filter_by_rating(
        self,
        min_rating: float = 0.99,
        max_pages: int = 5,
        check_detail: bool = False,
    ) -> List[Product]:
        """Scan search results and return products with rating ≥ *min_rating*.

        Args:
            min_rating:  Minimum rating threshold (0-1).
            max_pages:   Maximum number of search result pages to scan.
            check_detail: If True, visit product detail pages to fill
                          missing ratings (slower but more thorough).

        Returns:
            Deduplicated list of qualifying products.
        """
        logger.info(
            "Filtering: min_rating=%.0f%%, max_pages=%d, check_detail=%s",
            min_rating * 100,
            max_pages,
            check_detail,
        )

        qualified: List[Product] = []
        seen_urls: set = set()

        for page_num in range(1, max_pages + 1):
            logger.info("Scanning page %d/%d...", page_num, max_pages)

            products = self.searcher.extract_products(max_items=40)
            if not products:
                logger.info("No products on page %d, stopping.", page_num)
                break

            for product in products:
                if not product.url or product.url in seen_urls:
                    continue
                seen_urls.add(product.url)

                rating = product.rating

                # If rating is missing and deep-check is enabled, visit detail page
                if rating is None and check_detail:
                    rating = self._get_rating_from_detail(product)

                if rating is not None and rating >= min_rating:
                    qualified.append(product)
                    logger.info(
                        "  ✓ %s | ¥%.2f | rating=%.0f%%",
                        product.title[:40],
                        product.price,
                        rating * 100,
                    )

            # If we have qualifying products, stop scanning further pages
            if qualified:
                logger.info(
                    "Found %d qualifying product(s) on page %d.",
                    len(qualified),
                    page_num,
                )
                break

            # Prepare next page
            if page_num < max_pages:
                if not self.searcher.go_next_page():
                    logger.info("No more pages available.")
                    break

        if not qualified:
            logger.warning(
                "No products met the %.0f%% rating threshold across %d page(s).",
                min_rating * 100,
                page_num,
            )

        return qualified

    # ── Detail-page rating extraction ────────────────────────────

    def _get_rating_from_detail(self, product: Product) -> Optional[float]:
        """Open the product detail page in a new tab and extract rating.

        Returns the rating (0-1) or None if it could not be determined.
        """
        if not product.url:
            return None

        logger.debug("Checking detail page: %s", product.title[:40])

        try:
            # Open detail page in a new tab (preserves search-results state)
            tab = self.page.context.new_page()
            tab.goto(product.url, wait_until="networkidle", timeout=30000)

            rating = self._extract_rating_from_detail_page(tab)

            tab.close()
            time.sleep(DETAIL_PAGE_DELAY[0])
            return rating

        except Exception as e:
            logger.debug("Detail-page check failed for '%s': %s", product.title[:30], e)
            return None

    def _extract_rating_from_detail_page(self, tab: Page) -> Optional[float]:
        """Parse a product detail page for rating information.

        Uses multiple strategies:
        1. Search for text patterns like "好评率 98%" or "98% 好评"
        2. Look for known rating widget selectors
        3. Check embedded JSON data on the page
        """
        # Strategy 1: scan visible text for "好评率 X%" patterns
        rating = self._scan_text_for_rating(tab)
        if rating is not None:
            return rating

        # Strategy 2: query rating-related DOM elements
        rating = self._query_rating_elements(tab)
        if rating is not None:
            return rating

        # Strategy 3: check embedded JSON data
        rating = self._check_embedded_data(tab)
        if rating is not None:
            return rating

        return None

    def _scan_text_for_rating(self, tab: Page) -> Optional[float]:
        """Search visible page text for 好评率 percentage patterns."""
        try:
            body = tab.inner_text("body", timeout=10000)
        except Exception:
            return None

        # Pattern: "好评率 : 98%" or "好评率 98%" or "98% 好评"
        patterns = [
            r"好评率[：:\s]*(\d+\.?\d*)\s*%",
            r"(\d+\.?\d*)\s*%\s*好评",
        ]
        for pat in patterns:
            match = re.search(pat, body)
            if match:
                val = float(match.group(1))
                return val / 100.0 if val > 1 else val

        # Pattern: star rating like "4.8" near "评分" or "综合"
        star_match = re.search(r"(?:评分|综合)[：:\s]*(\d\.\d)", body)
        if star_match:
            val = float(star_match.group(1))
            # Star ratings are usually out of 5
            return val / 5.0 if val > 1.0 else val

        return None

    def _query_rating_elements(self, tab: Page) -> Optional[float]:
        """Look for known rating widget DOM elements."""
        selectors = [
            ".tb-rate-counter",
            ".rate-counter",
            "[class*='rate']",
            "[class*='Rating']",
        ]
        for sel in selectors:
            try:
                el = tab.wait_for_selector(sel, timeout=3000)
                if el and el.is_visible():
                    text = el.inner_text().strip()
                    match = re.search(r"(\d+\.?\d*)\s*%", text)
                    if match:
                        val = float(match.group(1))
                        return val / 100.0 if val > 1 else val
            except PlaywrightTimeout:
                continue
        return None

    def _check_embedded_data(self, tab: Page) -> Optional[float]:
        """Try to extract rating from embedded JSON data on the detail page."""
        try:
            data = tab.evaluate("""() => {
                return window.__INIT_DATA__
                    || window.g_page_config
                    || window.detailData
                    || null;
            }""")
            if data:
                return self._extract_rating_from_json(data)
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_rating_from_json(data: dict) -> Optional[float]:
        """Walk nested JSON to find a rating field."""
        # Check known rating keys
        for key in ["rate", "rating", "goodRate", "好评率"]:
            val = data.get(key)
            if val is not None:
                return TaobaoSearch._parse_rating(str(val))

        # Walk nested structures
        for path in [
            ["item", "rate"],
            ["item", "rating"],
            ["data", "rate"],
            ["data", "rating"],
            ["props", "pageProps", "rate"],
            ["props", "pageProps", "item", "rate"],
        ]:
            val = data
            for key in path:
                if isinstance(val, dict):
                    val = val.get(key)
                else:
                    val = None
                    break
            if val is not None:
                return TaobaoSearch._parse_rating(str(val))

        return None

    # ── Reporting ────────────────────────────────────────────────

    @staticmethod
    def format_result(products: List[Product]) -> str:
        """Format qualifying products into a human-readable report string."""
        if not products:
            return "未找到符合条件的商品。"

        lines = [
            f"找到 {len(products)} 个符合条件的商品（好评率≥99%）：",
            "",
        ]
        for i, p in enumerate(products, 1):
            rating_str = f"好评率 {p.rating * 100:.0f}%" if p.rating else "好评率未知"
            price_str = f"¥{p.price:.2f}" if p.price else "价格未知"
            lines.append(f"{i}. {p.title}")
            lines.append(f"   价格: {price_str} | {rating_str}")
            if p.shop:
                lines.append(f"   店铺: {p.shop}")
            lines.append("")

        return "\n".join(lines)
