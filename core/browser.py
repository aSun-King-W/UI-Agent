"""
Browser engine wrapper for Playwright with stealth anti-detection.

Provides:
- Chromium launch with headed/headless toggle
- playwright-stealth injection
- Unified page context management
- Timeout & retry strategies
"""

import os
import time
import logging
from typing import Callable, Optional
from pathlib import Path

from playwright.sync_api import (
    sync_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeout,
)
from playwright_stealth import Stealth

stealth = Stealth()

logger = logging.getLogger("browser_engine")

# Default viewport — mimic a typical desktop screen
DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}

# Common Windows Chrome user agent
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class BrowserEngine:
    """Manages Chromium browser lifecycle with stealth anti-detection.

    Usage::

        engine = BrowserEngine(headless=False)
        page = engine.start()
        page.goto("https://www.taobao.com")
        engine.close()

    Or as context manager::

        with BrowserEngine() as engine:
            engine.page.goto("https://www.taobao.com")
    """

    def __init__(
        self,
        headless: bool = False,
        slow_mo: int = 100,
        viewport: Optional[dict] = None,
        user_agent: Optional[str] = None,
        timeout: int = 30000,
        screenshot_dir: Optional[str] = None,
    ):
        """
        Args:
            headless: Run browser in headless mode (no GUI).
            slow_mo: Slow down Playwright operations by ms (mimics human speed).
            viewport: Browser viewport dimensions.
            user_agent: Custom User-Agent string.
            timeout: Default timeout for navigation & element waiting (ms).
            screenshot_dir: Directory to save screenshots.
        """
        self.headless = headless
        self.slow_mo = slow_mo
        self.viewport = viewport or DEFAULT_VIEWPORT
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.timeout = timeout
        self.screenshot_dir = screenshot_dir or self._default_screenshot_dir()

        # Internal state
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # ── Lifecycle ────────────────────────────────────────────────

    def start(self) -> Page:
        """Launch browser, create context with stealth, return main page."""
        logger.info(
            "Starting browser | headless=%s slow_mo=%s viewport=%s",
            self.headless,
            self.slow_mo,
            self.viewport,
        )

        self._playwright = sync_playwright().start()
        self._browser = self._launch_browser()
        self._context = self._create_context()
        self._page = self._context.new_page()
        self._page.set_default_timeout(self.timeout)

        logger.info("Browser started successfully.")
        return self._page

    def close(self):
        """Close browser and clean up resources."""
        if self._browser:
            try:
                self._browser.close()
            except Exception as e:
                logger.warning("Error closing browser: %s", e)
        if self._playwright:
            self._playwright.stop()
        logger.info("Browser closed.")

    def __enter__(self) -> "BrowserEngine":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ── Page access ─────────────────────────────────────────────

    @property
    def page(self) -> Page:
        """Get the current main page."""
        if not self._page:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._page

    def new_page(self) -> Page:
        """Create a new page within the same context."""
        page = self._context.new_page()
        page.set_default_timeout(self.timeout)
        return page

    # ── Screenshots ─────────────────────────────────────────────

    def screenshot(self, name: Optional[str] = None) -> str:
        """Take a screenshot and save to screenshot_dir.

        Returns:
            Full path to the saved screenshot.
        """
        os.makedirs(self.screenshot_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        label = f"_{name}" if name else ""
        filename = f"screenshot{label}_{ts}.png"
        filepath = os.path.join(self.screenshot_dir, filename)

        self.page.screenshot(path=filepath, full_page=False)
        logger.info("Screenshot saved: %s", filepath)
        return filepath

    def fullpage_screenshot(self, name: Optional[str] = None) -> str:
        """Take a full-page screenshot."""
        os.makedirs(self.screenshot_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        label = f"_{name}" if name else ""
        filename = f"fullpage{label}_{ts}.png"
        filepath = os.path.join(self.screenshot_dir, filename)

        self.page.screenshot(path=filepath, full_page=True)
        logger.info("Full-page screenshot saved: %s", filepath)
        return filepath

    # ── Navigation helpers ──────────────────────────────────────

    def navigate(self, url: str, *, wait_until: str = "networkidle") -> bool:
        """Navigate to URL with timeout and retry.

        Args:
            url: Target URL.
            wait_until: Playwright waitUntil strategy.

        Returns:
            True if navigation succeeded, False otherwise.
        """
        return retry_on_failure(
            func=lambda: self.page.goto(url, wait_until=wait_until),
            max_retries=2,
            retry_delay=2000,
        )

    def wait_for_page_ready(self):
        """Wait for page to reach a stable state."""
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeout:
            logger.warning("Page load timeout, continuing anyway...")

    # ── Private helpers ─────────────────────────────────────────

    def _launch_browser(self) -> Browser:
        """Launch Chromium with anti-detection args."""
        args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            f"--window-size={self.viewport['width']},{self.viewport['height']}",
        ]

        return self._playwright.chromium.launch(
            headless=self.headless,
            args=args,
            slow_mo=self.slow_mo,
        )

    def _create_context(self) -> BrowserContext:
        """Create a browser context with stealth config."""
        context = self._browser.new_context(
            viewport=self.viewport,
            user_agent=self.user_agent,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            permissions=[],
        )

        # Inject stealth scripts to mask automation fingerprints
        stealth.apply_stealth_sync(context)

        return context

    @staticmethod
    def _default_screenshot_dir() -> str:
        """Default screenshots directory relative to project root."""
        # core/browser.py -> project root/assets/screenshots/
        here = Path(__file__).resolve().parent.parent
        return str(here / "assets" / "screenshots")


def retry_on_failure(func: Callable, max_retries: int = 2, retry_delay: int = 1000):
    """Retry a function on Playwright timeout with exponential backoff.

    Args:
        func: Callable to retry.
        max_retries: Maximum number of retry attempts.
        retry_delay: Base delay between retries (ms), doubles each attempt.

    Returns:
        The function's return value, or raises the last exception.
    """
    last_exc = None
    delay = retry_delay

    for attempt in range(1 + max_retries):
        try:
            result = func()
            if attempt > 0:
                logger.info("Retry successful on attempt %d", attempt + 1)
            return result
        except PlaywrightTimeout as e:
            last_exc = e
            logger.warning(
                "Attempt %d/%d failed: %s. Retrying in %dms...",
                attempt + 1,
                1 + max_retries,
                e,
                delay,
            )
            time.sleep(delay / 1000.0)
            delay *= 2

    logger.error("All %d attempts failed.", 1 + max_retries)
    raise last_exc
