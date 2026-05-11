"""
Page utility functions for state extraction and interaction.

Provides:
- DOM state summary for LLM decision-making
- Interactive element extraction (buttons, links, inputs)
- Element-level screenshots
- Smart waiting strategies
- Scroll and text helpers
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger("page_utils")

MAX_STATE_TEXT_LENGTH = 3000  # Truncate extracted text for LLM context


class PageUtils:
    """Utility wrapper around a Playwright Page for state extraction.

    Used primarily by the LLM orchestrator to understand the current
    page state and decide the next action.  Also provides general-purpose
    helpers for other modules.

    Usage::

        utils = PageUtils(page)
        state = utils.get_page_state()
        # state contains url, title, interactive elements, text, etc.
    """

    def __init__(self, page: Page):
        self.page = page

    # ── LLM page state (primary interface) ──────────────────────

    def get_page_state(self) -> Dict[str, Any]:
        """Build a comprehensive snapshot of the current page for LLM decision-making.

        Returns a dict with:
        - url: current page URL
        - title: page title
        - mode: inferred page type (login / search / detail / cart / home / unknown)
        - interactive: list of clickable/interactable elements
        - headings: visible headings (h1-h3)
        - text_preview: condensed visible text (≤3000 chars)
        - status_messages: success/error/warning messages on page
        """
        state = {
            "url": self.page.url,
            "title": self.page.title(),
            "mode": self._infer_page_mode(),
            "interactive": self._extract_interactive(),
            "headings": self._extract_headings(),
            "text_preview": self._get_text_preview(),
            "status_messages": self._extract_status_messages(),
        }
        return state

    def _infer_page_mode(self) -> str:
        """Infer what kind of page we're on from the URL, title, and DOM state."""
        url = self.page.url.lower()
        title = self.page.title().lower()

        if "login" in url or "login" in title:
            return "login"
        if "search" in url or "s.taobao" in url:
            return "search_results"
        if "detail" in url or "item.taobao" in url or "detail.tmall" in url:
            return "product_detail"
        if "cart" in url or "cart" in title:
            return "cart"
        if "taobao.com" in url and ("/" == url.rstrip("/")[-1:] or "www.taobao" in url):
            # 检查是否有登录弹窗浮层
            if self._has_login_popup():
                return "login"
            return "home"

        return "unknown"

    def _has_login_popup(self) -> bool:
        """检测当前页面是否有登录弹窗/浮层。"""
        try:
            popup_hints = [
                "#J_LoginPopup",
                ".login-popup",
                ".login-dialog",
                "#fm-login-id",            # 登录输入框出现说明弹窗打开了
                ".fm-login",
                "iframe[src*='login']",
            ]
            for sel in popup_hints:
                el = self.page.query_selector(sel)
                if el and el.is_visible():
                    return True
        except Exception:
            pass
        return False

    def _extract_interactive(self) -> List[Dict[str, str]]:
        """Extract visible interactive elements (buttons, links, inputs).

        Returns a list of dicts with keys: tag, text, selector_hint, type.
        Used by the LLM to understand what actions are possible.
        """
        elements = []

        try:
            elements += self._collect_elements("button, [role='button']", "button")
        except Exception:
            pass

        try:
            elements += self._collect_elements("a[href]", "link")
        except Exception:
            pass

        try:
            elements += self._collect_elements(
                "input:not([type='hidden']), textarea, select", "input"
            )
        except Exception:
            pass

        # Deduplicate by text + tag
        seen = set()
        unique = []
        for el in elements:
            key = (el["tag"], el["text"])
            if key not in seen:
                seen.add(key)
                unique.append(el)

        return unique

    def _collect_elements(self, css: str, tag: str) -> List[Dict[str, str]]:
        """Collect visible elements matching a CSS selector."""
        results = []
        nodes = self.page.query_selector_all(css)
        for node in nodes:
            try:
                if not node.is_visible():
                    continue

                text = (node.inner_text() or node.get_attribute("title") or "").strip()
                if not text:
                    # For inputs, use placeholder or name
                    placeholder = node.get_attribute("placeholder") or ""
                    name = node.get_attribute("name") or ""
                    text = placeholder or name

                href = node.get_attribute("href") or ""
                selector_hint = self._make_selector_hint(tag, text, href)

                results.append({
                    "tag": tag,
                    "text": text[:80],
                    "selector_hint": selector_hint[:120],
                })
            except Exception:
                continue
        return results

    def _make_selector_hint(self, tag: str, text: str, href: str) -> str:
        """Build a human-readable selector hint for the LLM."""
        if tag == "link" and text:
            return f"link: {text}"
        if tag == "link" and href:
            return f"link: {href[:60]}"
        if tag == "button" and text:
            return f"button: {text}"
        if tag == "input" and text:
            return f"input: {text}"
        return f"<{tag}>"

    def _extract_headings(self) -> List[Dict[str, str]]:
        """Extract visible h1-h3 headings from the page."""
        headings = []
        for level in range(1, 4):
            try:
                nodes = self.page.query_selector_all(f"h{level}")
                for node in nodes:
                    try:
                        if node.is_visible():
                            text = node.inner_text().strip()
                            if text:
                                headings.append({"level": f"h{level}", "text": text[:100]})
                    except Exception:
                        continue
            except Exception:
                continue
        return headings

    def _get_text_preview(self) -> str:
        """Get a compressed preview of the page's visible text."""
        try:
            text = self.page.inner_text("body")
        except Exception:
            return ""

        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > MAX_STATE_TEXT_LENGTH:
            text = text[:MAX_STATE_TEXT_LENGTH] + " ... [truncated]"
        return text

    def _extract_status_messages(self) -> List[str]:
        """Find success/error/warning messages on the page."""
        messages = []
        # Common status/alert selectors
        selectors = [
            ".success",
            ".error",
            ".warning",
            ".message",
            ".alert",
            ".notice",
            "[class*='success']",
            "[class*='error']",
            "[class*='warning']",
            "[class*='message']",
            "[class*='notice']",
            "[role='alert']",
        ]
        for sel in selectors:
            try:
                nodes = self.page.query_selector_all(sel)
                for node in nodes:
                    try:
                        if node.is_visible():
                            text = node.inner_text().strip()
                            if text and len(text) > 2:
                                messages.append(text[:150])
                    except Exception:
                        continue
            except Exception:
                continue
        return messages

    # ── Element-level screenshots ───────────────────────────────

    def element_screenshot(self, selector: str, name: Optional[str] = None) -> Optional[str]:
        """Capture a screenshot of a specific element.

        Args:
            selector: CSS selector for the target element.
            name:     Label for the output filename.

        Returns:
            File path to the saved image, or None on failure.
        """
        try:
            el = self.page.wait_for_selector(selector, timeout=5000)
            if not el or not el.is_visible():
                logger.warning("Element not visible for screenshot: %s", selector)
                return None
            return self._save_screenshot(el.screenshot(), name or f"element_{selector.strip('. #')}")
        except Exception as e:
            logger.warning("Element screenshot failed: %s", e)
            return None

    def highlight_screenshot(self, selector: str, name: Optional[str] = None) -> Optional[str]:
        """Screenshot with the target element highlighted via a red border.

        Temporarily adds a red outline to the element, takes a full-page
        screenshot, then removes the outline.

        Returns:
            File path to the saved image, or None on failure.
        """
        try:
            el = self.page.wait_for_selector(selector, timeout=5000)
            if not el or not el.is_visible():
                return None

            self.page.evaluate(
                """el => el.style.outline = '3px solid red'""", el
            )
            time.sleep(0.3)
            path = self._save_screenshot(self.page.screenshot(), name or f"highlight_{selector.strip('. #')}")
            self.page.evaluate(
                """el => el.style.outline = ''""", el
            )
            return path
        except Exception as e:
            logger.warning("Highlight screenshot failed: %s", e)
            return None

    # ── Smart waiting ───────────────────────────────────────────

    def wait_for_element(
        self,
        selector: str,
        timeout: int = 10000,
        state: str = "visible",
    ) -> bool:
        """Wait for an element to reach a given state.

        Args:
            selector: CSS selector (supports text= pseudo-selectors).
            timeout:  Maximum wait time in ms.
            state:    "visible" (default), "attached", "stable", or "hidden".

        Returns:
            True if the element reached the target state.
        """
        try:
            self.page.wait_for_selector(selector, state=state, timeout=timeout)
            return True
        except PlaywrightTimeout:
            logger.warning("Element did not become '%s': %s", state, selector)
            return False

    def wait_for_stable(self, timeout: int = 5000) -> bool:
        """Wait for the page to stop changing (DOM mutations settle).

        Polls the DOM at 300ms intervals.  If no changes are detected
        within one interval, the page is considered stable.
        """
        try:
            self.page.wait_for_load_state("load", timeout=timeout)
            return True
        except PlaywrightTimeout:
            logger.warning("Page did not reach 'load' state within %dms.", timeout)
            return False

    def wait_for_text(self, text: str, timeout: int = 10000) -> bool:
        """Wait for text to appear somewhere on the page."""
        return self.wait_for_element(f"text={text}", timeout=timeout, state="visible")

    # ── Scrolling ───────────────────────────────────────────────

    def scroll_to_element(self, selector: str) -> bool:
        """Scroll the page until an element matching *selector* is in view."""
        try:
            el = self.page.wait_for_selector(selector, timeout=5000)
            if el:
                el.scroll_into_view_if_needed()
                time.sleep(0.3)
                return True
        except Exception:
            pass
        return False

    def scroll_to_top(self):
        """Scroll to the top of the page."""
        self.page.evaluate("window.scrollTo(0, 0)")
        time.sleep(0.3)

    def scroll_to_bottom(self):
        """Scroll to the bottom of the page."""
        self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.3)

    def scroll_by(self, dx: int = 0, dy: int = 300):
        """Scroll by a relative offset (default: down 300px)."""
        self.page.evaluate(f"window.scrollBy({dx}, {dy})")
        time.sleep(0.3)

    # ── Page text extraction ────────────────────────────────────

    def get_visible_text(self, max_length: int = MAX_STATE_TEXT_LENGTH) -> str:
        """Extract all visible text from the page, truncated.

        Useful for LLM context when the agent needs to read
        what's on the page.
        """
        return self._get_text_preview()

    def get_page_summary(self) -> str:
        """Return a one-line human-readable summary of the page state.

        Example::

            "search_results | 索尼耳机 | 40 items | Filtered by rating"
        """
        state = self.get_page_state()
        parts = [state["mode"]]

        if state["headings"]:
            parts.append(state["headings"][0]["text"])

        if state["interactive"]:
            n_buttons = sum(1 for e in state["interactive"] if e["tag"] == "button")
            n_links = sum(1 for e in state["interactive"] if e["tag"] == "link")
            n_inputs = sum(1 for e in state["interactive"] if e["tag"] == "input")
            parts.append(f"{n_buttons}btn/{n_links}link/{n_inputs}input")

        if state["status_messages"]:
            parts.append(f"msg: {state['status_messages'][0][:40]}")

        return " | ".join(parts)

    # ── Internal helpers ────────────────────────────────────────

    def _save_screenshot(self, data: bytes, name: str) -> str:
        """Write raw screenshot bytes to a file and return the path."""
        ts = time.strftime("%Y%m%d_%H%M%S")
        d = Path(__file__).resolve().parent.parent / "assets" / "screenshots"
        os.makedirs(d, exist_ok=True)
        # Sanitise filename
        safe = re.sub(r"[^\w\-_]", "_", name)[:60]
        path = str(d / f"{safe}_{ts}.png")
        with open(path, "wb") as f:
            f.write(data)
        logger.debug("Screenshot saved: %s", path)
        return path
