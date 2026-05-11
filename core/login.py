"""
Taobao login module with stealth anti-detection and cookie persistence.

Provides:
- Full password login flow (QR → password mode switch)
- Slider CAPTCHA handling (attempt bypass with human-like motion)
- Cookie persistence for session reuse across runs
- Login state detection
"""

import os
import json
import time
import random
import logging
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger("login")

# Cookie storage
COOKIE_DIR = Path(__file__).resolve().parent.parent / "assets" / "cookies"
COOKIE_FILE = COOKIE_DIR / "taobao_cookies.json"

TAOBAO_LOGIN_URL = "https://login.taobao.com/"
TAOBAO_HOME_URL = "https://www.taobao.com/"

TYPING_DELAY_RANGE = (50, 180)  # ms between keystrokes


class TaobaoLogin:
    """Handle Taobao login with password mode and cookie persistence.

    Usage::

        login = TaobaoLogin(page)
        if login.is_logged_in():
            print("Already logged in via cookies")
        else:
            login.login(username="your_phone", password="your_pass")
    """

    def __init__(self, page: Page):
        self.page = page
        self.logged_in = False

    # ── Public API ───────────────────────────────────────────────

    def is_logged_in(self) -> bool:
        """Check if already logged in via persisted cookies.

        Loads saved cookies, navigates to Taobao home, and checks for
        user-indicator elements.  Returns True only if cookies are still
        valid.
        """
        if not COOKIE_FILE.exists():
            logger.info("No saved cookies found.")
            return False

        try:
            with open(COOKIE_FILE, "r") as f:
                cookies = json.load(f)
            self.page.context.add_cookies(cookies)

            self.page.goto(TAOBAO_HOME_URL, wait_until="networkidle")
            self._random_sleep(1, 2)

            if self._detect_logged_in():
                logger.info("Logged in via saved cookies.")
                self.logged_in = True
                return True

            logger.info("Saved cookies expired or invalid.")
            return False
        except Exception as e:
            logger.warning("Cookie check failed: %s", e)
            return False

    def login(self, username: str, password: str) -> bool:
        """Execute full Taobao password login flow.

        Steps:
        1. Navigate to login page
        2. Switch from QR to password mode
        3. Enter credentials with human-like typing
        4. Submit and handle slider if present
        5. Verify login success and persist cookies

        Returns:
            True if login succeeded.
        """
        logger.info("Starting Taobao password login...")

        try:
            self._go_to_login_page()
            self._switch_to_password_tab()
            self._enter_credentials(username, password)
            self._submit_login()
            self._handle_slider_if_present()
            self._wait_login_complete()
            self._save_cookies()
            self.logged_in = True
            logger.info("Taobao login successful!")
            return True
        except Exception as e:
            logger.error("Login failed: %s", e)
            self._screenshot("login_failed")
            return False

    def clear_cookies(self):
        """Clear both persisted cookies and browser context cookies."""
        if COOKIE_FILE.exists():
            os.remove(COOKIE_FILE)
            logger.info("Saved cookies cleared.")
        self.page.context.clear_cookies()

    # ── Login steps (private) ────────────────────────────────────

    def _go_to_login_page(self):
        logger.info("Navigating to login page...")
        self.page.goto(TAOBAO_LOGIN_URL, wait_until="networkidle")
        self._random_sleep(1, 2)

    def _switch_to_password_tab(self):
        """Switch from QR code login to password/account login."""
        logger.info("Switching to password login mode...")

        selectors = [
            "text=密码登录",
            "//span[contains(text(), '密码登录')]",
            ".password-login",
            "#J_Quick2Static",
        ]
        for sel in selectors:
            try:
                tab = self.page.wait_for_selector(sel, timeout=5000)
                if tab and tab.is_visible():
                    tab.click()
                    self._random_sleep(0.5, 1.5)
                    logger.info("Clicked password tab: %s", sel)
                    return
            except PlaywrightTimeout:
                continue

        logger.info("Password tab not found — possibly already in password mode.")

    def _enter_credentials(self, username: str, password: str):
        """Type credentials with human-like delays between keystrokes."""
        logger.info("Entering credentials...")

        uname_input = self._find_input([
            "#fm-login-id",
            "input[name='fm-login-id']",
            "input[placeholder*='手机号']",
            "input[placeholder*='账号']",
            "input[name='username']",
        ])
        if not uname_input:
            raise RuntimeError("Username input field not found")
        uname_input.click()
        self._random_sleep(0.3, 0.8)
        uname_input.fill("")
        self._human_type(uname_input, username)

        self._random_sleep(0.5, 1.0)

        pwd_input = self._find_input([
            "#fm-login-password",
            "input[name='fm-login-password']",
            "input[placeholder*='密码']",
            "input[type='password']",
        ])
        if not pwd_input:
            raise RuntimeError("Password input field not found")
        pwd_input.click()
        self._random_sleep(0.3, 0.8)
        pwd_input.fill("")
        self._human_type(pwd_input, password)

        self._random_sleep(0.5, 1.0)

    def _submit_login(self):
        """Click the login / submit button."""
        logger.info("Submitting login...")

        selectors = [
            "button[type='submit']",
            ".fm-button",
            "#login-form button",
            "//button[contains(text(), '登录')]",
        ]
        for sel in selectors:
            try:
                btn = self.page.wait_for_selector(sel, timeout=5000)
                if btn and btn.is_visible():
                    btn.click()
                    self._random_sleep(1, 2)
                    logger.info("Login button clicked.")
                    return
            except PlaywrightTimeout:
                continue

        raise RuntimeError("Login submit button not found")

    def _handle_slider_if_present(self):
        """Detect and attempt to solve slider CAPTCHA with human-like motion."""
        slider_selectors = [
            "#nc_1_wrapper",
            ".nc-container",
            "[id*='nc_']",
        ]

        container = None
        for sel in slider_selectors:
            try:
                el = self.page.wait_for_selector(sel, timeout=5000)
                if el and el.is_visible():
                    container = el
                    break
            except PlaywrightTimeout:
                continue

        if not container:
            logger.info("No slider CAPTCHA detected.")
            return

        logger.info("Slider CAPTCHA detected, attempting to solve...")
        self._screenshot("slider_detected")

        try:
            # Locate the draggable handle within the slider
            handle = self._find_handle()
            if not handle:
                logger.warning("Slider handle element not found.")
                return

            box = handle.bounding_box()
            if not box:
                return

            start_x = box["x"] + box["width"] / 2
            start_y = box["y"] + box["height"] / 2
            end_x = start_x + random.randint(280, 340)
            end_y = start_y + random.randint(-4, 4)

            self._drag_humanized(start_x, start_y, end_x, end_y)
            self._random_sleep(1.5, 2.5)

            if self._is_slider_gone():
                logger.info("Slider CAPTCHA solved (or dismissed).")
            else:
                logger.warning("Slider CAPTCHA still present — may need manual intervention.")
        except Exception as e:
            logger.warning("Slider handling error: %s", e)

    def _wait_login_complete(self):
        """Wait for post-login redirect and verify authenticated state."""
        logger.info("Waiting for login redirect...")

        # Wait for URL to leave login subdomain
        self.page.wait_for_load_state("networkidle", timeout=20000)
        self._random_sleep(2, 3)

        if not self._detect_logged_in():
            # Navigate to home explicitly and retry check
            self.page.goto(TAOBAO_HOME_URL, wait_until="networkidle")
            self._random_sleep(1, 2)
            if not self._detect_logged_in():
                raise RuntimeError(
                    "Login verification failed — not in logged-in state after login flow"
                )

    # ── Slider internals ─────────────────────────────────────────

    def _find_handle(self) -> Optional[object]:
        """Find the draggable handle inside the slider widget."""
        handle_selectors = [
            ".nc_iconfont",
            ".slider-handle",
            ".btn_slide",
            "[class*='btn']",
            "#nc_1__scale_text",
        ]
        for sel in handle_selectors:
            try:
                h = self.page.wait_for_selector(sel, timeout=3000)
                if h and h.is_visible():
                    return h
            except PlaywrightTimeout:
                continue
        return None

    def _drag_humanized(self, sx: float, sy: float, ex: float, ey: float):
        """Drag from (sx, sy) to (ex, ey) with smoothstep easing."""
        steps = random.randint(25, 40)

        self.page.mouse.move(sx, sy)
        self.page.mouse.down()
        time.sleep(random.uniform(0.1, 0.3))

        for i in range(1, steps + 1):
            t = i / steps
            # smoothstep: slow start / fast middle / slow end
            eased = t * t * (3 - 2 * t)
            x = sx + (ex - sx) * eased
            y = sy + (ey - sy) * eased
            self.page.mouse.move(x, y)
            time.sleep(random.uniform(0.008, 0.025))

        self.page.mouse.up()

    def _is_slider_gone(self) -> bool:
        """Check whether the slider container is no longer visible."""
        for sel in ["#nc_1_wrapper", ".nc-container"]:
            try:
                el = self.page.wait_for_selector(sel, timeout=3000)
                if el and el.is_visible():
                    return False
            except PlaywrightTimeout:
                pass
        return True

    # ── Login state detection ────────────────────────────────────

    def _detect_logged_in(self) -> bool:
        """Detect logged-in state by checking for user-indicator elements."""
        indicators = [
            ".member-nick",
            "#J_SiteNavMytaobao",
            ".site-nav-user",
            ".site-nav-bd",
            "//a[contains(text(), '我的淘宝')]",
        ]
        for sel in indicators:
            try:
                el = self.page.wait_for_selector(sel, timeout=3000)
                if el and el.is_visible():
                    return True
            except PlaywrightTimeout:
                continue
        return False

    # ── Cookie persistence ───────────────────────────────────────

    def _save_cookies(self):
        """Persist all browser cookies to a JSON file."""
        os.makedirs(COOKIE_DIR, exist_ok=True)
        try:
            cookies = self.page.context.cookies()
            with open(COOKIE_FILE, "w") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            logger.info("Cookies saved (%d entries).", len(cookies))
        except Exception as e:
            logger.warning("Failed to save cookies: %s", e)

    # ── Utilities ────────────────────────────────────────────────

    def _human_type(self, element, text: str):
        """Type text one keystroke at a time with variable delay."""
        for ch in text:
            element.type(ch, delay=random.randint(*TYPING_DELAY_RANGE))

    def _random_sleep(self, lo: float, hi: float):
        time.sleep(random.uniform(lo, hi))

    def _find_input(self, selectors) -> Optional[object]:
        """Return the first visible input matching any given selector."""
        for sel in selectors:
            try:
                el = self.page.wait_for_selector(sel, timeout=3000)
                if el and el.is_visible():
                    return el
            except PlaywrightTimeout:
                continue
        # Last-ditch: any visible <input> on the page
        try:
            inputs = self.page.query_selector_all("input:visible")
            if inputs:
                return inputs[0]
        except Exception:
            pass
        return None

    def _screenshot(self, name: str):
        """Save a debug screenshot."""
        ts = time.strftime("%Y%m%d_%H%M%S")
        d = Path(__file__).resolve().parent.parent / "assets" / "screenshots"
        os.makedirs(d, exist_ok=True)
        path = str(d / f"{name}_{ts}.png")
        self.page.screenshot(path=path)
        logger.info("Screenshot saved: %s", path)
