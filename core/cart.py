"""
Shopping cart operations for Taobao.

Provides:
- Product detail page navigation
- SKU attribute selection (color, size, etc.)
- Add-to-cart action
- Success verification
"""

import logging
import time
import re
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from core.search import Product

logger = logging.getLogger("cart")


class TaobaoCart:
    """Handle add-to-cart operations on Taobao / Tmall product detail pages.

    Usage::

        cart = TaobaoCart(page)
        added = cart.add_to_cart(product)
    """

    def __init__(self, page: Page):
        self.page = page

    # ── Public API ───────────────────────────────────────────────

    def add_to_cart(
        self,
        product: Product,
        quantity: int = 1,
    ) -> bool:
        """Add a product to the shopping cart.

        Opens the detail page in a new tab, selects the first available
        SKU option (if required), clicks add-to-cart, and verifies success.

        Args:
            product:  The product to add (must have a valid URL).
            quantity: Number of items to add (default 1).

        Returns:
            True if the product was successfully added to cart.
        """
        if not product.url:
            logger.warning("Product has no URL, cannot add to cart.")
            return False

        logger.info("Adding to cart: %s", product.title[:50])

        tab = None
        try:
            tab = self.page.context.new_page()
            tab.goto(product.url, wait_until="networkidle", timeout=30000)
            tab.wait_for_load_state("networkidle", timeout=15000)

            self._dismiss_overlays(tab)
            self._select_sku(tab)
            self._set_quantity(tab, quantity)
            clicked = self._click_add_to_cart(tab)

            if not clicked:
                logger.warning("Could not click add-to-cart button.")
                self._screenshot(tab, "add_to_cart_failed")
                return False

            success = self._verify_added(tab)
            if success:
                logger.info("✓ Added to cart: %s", product.title[:50])
            else:
                logger.warning("Add-to-cart clicked but verification ambiguous.")

            return success

        except Exception as e:
            logger.error("Failed to add to cart: %s", e)
            if tab:
                self._screenshot(tab, "cart_error")
            return False
        finally:
            if tab:
                try:
                    tab.close()
                except Exception:
                    pass
            time.sleep(1)

    # ── SKU / spec selection ─────────────────────────────────────

    def _select_sku(self, tab: Page):
        """Select the first available option in each SKU group.

        Taobao detail pages require selecting attributes like colour,
        size, etc. before the add-to-cart button becomes active.
        """
        sku_groups = self._find_sku_groups(tab)
        if not sku_groups:
            logger.debug("No SKU groups detected.")
            return

        logger.debug("Found %d SKU group(s).", len(sku_groups))

        for group in sku_groups:
            try:
                options = group.query_selector_all("a, button, span, li")
                clicked = False
                for opt in options:
                    class_ = (opt.get_attribute("class") or "").lower()
                    disabled = opt.get_attribute("disabled")
                    if disabled is not None:
                        continue
                    if "disable" in class_ or "gray" in class_ or "disabled" in class_:
                        continue
                    if not opt.is_visible():
                        continue

                    opt.click()
                    time.sleep(0.5)
                    clicked = True
                    logger.debug("SKU option selected.")
                    break

                if not clicked:
                    logger.warning("No available option in SKU group.")
            except Exception as e:
                logger.debug("SKU group interaction error: %s", e)
                continue

        time.sleep(0.5)

    def _find_sku_groups(self, tab: Page) -> list:
        """Locate SKU / specification groups on the detail page."""
        selectors = [
            "dl.J_TSaleProp",
            "dl.tb-sku",
            ".sku-group",
            "[class*='sku']",
            ".J_TSaleProp",
            "div.props",
        ]
        for sel in selectors:
            try:
                groups = tab.query_selector_all(sel)
                if groups:
                    return groups
            except Exception:
                continue
        return []

    def _find_add_to_cart_button(self, tab: Page):
        """Locate the '加入购物车' button on the detail page."""
        selectors = [
            "#J_LinkBuy",
            "a.addcart",
            ".tb-btn-addcart",
            "#J_juValid",
            "//a[contains(text(), '加入购物车')]",
            "//button[contains(text(), '加入购物车')]",
            "[class*='addcart']",
            "[class*='AddCart']",
        ]
        for sel in selectors:
            try:
                btn = tab.wait_for_selector(sel, timeout=5000)
                if btn and btn.is_visible():
                    return btn
            except PlaywrightTimeout:
                continue
        return None

    # ── Quantity ─────────────────────────────────────────────────

    def _set_quantity(self, tab: Page, quantity: int):
        """Set the purchase quantity if greater than 1."""
        if quantity <= 1:
            return

        try:
            selectors = [
                "#J_Quantity",
                "input.quantity-input",
                "input[name='quantity']",
                "[class*='quantity'] input",
            ]
            for sel in selectors:
                try:
                    inp = tab.wait_for_selector(sel, timeout=3000)
                    if inp and inp.is_visible():
                        inp.fill("")
                        inp.type(str(quantity), delay=60)
                        logger.debug("Quantity set to %d.", quantity)
                        return
                except PlaywrightTimeout:
                    continue
        except Exception as e:
            logger.debug("Could not set quantity: %s", e)

    # ── Add-to-cart action ───────────────────────────────────────

    def _click_add_to_cart(self, tab: Page) -> bool:
        """Find and click the add-to-cart button."""
        btn = self._find_add_to_cart_button(tab)
        if not btn:
            logger.warning("Add-to-cart button not found.")
            return False

        # Ensure button is enabled (not greyed out before SKU selection)
        try:
            btn.wait_for_element_state("enabled", timeout=5000)
        except PlaywrightTimeout:
            logger.warning("Add-to-cart button still disabled after SKU selection.")

        btn.click()
        logger.debug("Add-to-cart button clicked.")
        time.sleep(1.5)
        return True

    # ── Verification ─────────────────────────────────────────────

    def _verify_added(self, tab: Page) -> bool:
        """Verify that the item was successfully added to cart.

        Checks for success indicators:
        1. A success popup / side panel
        2. Button text changes ("已添加")
        3. URL fragment change (some Taobao versions)
        4. Toast messages
        """
        # Strategy 1: visible success dialog
        for sel in [
            ".cart-popup",
            ".addcart-popup",
            "#J_CartPopup",
            ".tb-cart-popup",
            "[class*='cart-popup']",
            ".success-tip",
        ]:
            try:
                el = tab.wait_for_selector(sel, timeout=5000)
                if el and el.is_visible():
                    text = el.inner_text()
                    if "成功" in text or "添加" in text or "购物车" in text:
                        logger.debug("Verified via popup: %s", text[:40])
                        return True
            except PlaywrightTimeout:
                continue

        # Strategy 2: button text changed
        btn = self._find_add_to_cart_button(tab)
        if btn:
            try:
                text = btn.inner_text()
                if "已添加" in text or "成功" in text:
                    logger.debug("Verified via button text.")
                    return True
            except Exception:
                pass

        # Strategy 3: success toast
        for sel in [".J_TToast", ".toast", "[class*='toast']"]:
            try:
                el = tab.wait_for_selector(sel, timeout=3000)
                if el and el.is_visible():
                    text = el.inner_text()
                    if "成功" in text or "添加" in text:
                        logger.debug("Verified via toast.")
                        return True
                # Check if it was a transient toast that already disappeared
            except PlaywrightTimeout:
                continue

        # Strategy 4: URL indicates cart action
        if "cart" in tab.url.lower() or "success" in tab.url.lower():
            logger.debug("Verified via URL.")
            return True

        logger.debug("No clear success indicator found.")
        return False

    # ── Overlay handling ─────────────────────────────────────────

    def _dismiss_overlays(self, tab: Page):
        """Close any popup overlays that might block interaction."""
        close_selectors = [
            ".close-popup",
            ".popup-close",
            ".dialog-close",
            "button.close",
            "[class*='popup-close']",
            "[class*='dialog-close']",
            ".J_Close",
        ]
        for sel in close_selectors:
            try:
                el = tab.wait_for_selector(sel, timeout=2000)
                if el and el.is_visible():
                    el.click()
                    logger.debug("Overlay dismissed: %s", sel)
                    time.sleep(0.5)
            except PlaywrightTimeout:
                continue

    # ── Utilities ────────────────────────────────────────────────

    def _screenshot(self, tab: Page, name: str):
        """Save a debug screenshot."""
        import os
        from pathlib import Path

        ts = time.strftime("%Y%m%d_%H%M%S")
        d = Path(__file__).resolve().parent.parent / "assets" / "screenshots"
        os.makedirs(d, exist_ok=True)
        path = str(d / f"{name}_{ts}.png")
        try:
            tab.screenshot(path=path)
            logger.info("Screenshot saved: %s", path)
        except Exception:
            pass

    @staticmethod
    def summarize_results(results: list) -> str:
        """Format a list of (product, success_flag) tuples into a report."""
        total = len(results)
        succeeded = sum(1 for _, ok in results if ok)
        failed = total - succeeded

        lines = [
            f"购物车操作完成：成功 {succeeded} 个，失败 {failed} 个。",
            "",
        ]
        for product, ok in results:
            icon = "✓" if ok else "✗"
            lines.append(f"{icon} {product.title[:50]}")
            if product.price:
                lines.append(f"   ¥{product.price:.2f}")

        return "\n".join(lines)
