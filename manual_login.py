"""
手动登录淘宝并保存 Cookie 的辅助脚本。

使用 real Chrome（你日常使用的浏览器）来登录，
避免 Playwright Chromium 被淘宝风控拦截。

用法:
    python manual_login.py

登录成功后会自动保存 Cookie 到 assets/cookies/taobao_cookies.json，
之后 skill_entry.py 会自动复用。
"""

import json
import os
import sys
import time
import logging
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from core.login import COOKIE_FILE, COOKIE_DIR
from playwright.sync_api import sync_playwright

logging.basicConfig(
    level=logging.INFO,
    format="[%(name)s] %(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("manual_login")


def main():
    print("=" * 56)
    print("  淘宝手动登录 — Cookie 保存工具")
    print("=" * 56)
    print()
    print("  即将打开你的 Chrome 浏览器")
    print()
    print("  请按以下步骤操作：")
    print("  1. 浏览器会打开淘宝首页")
    print("  2. 在页面上手动登录（扫码/密码/手机验证码）")
    print("     - 滑块验证码在这种模式下是正常的，用手滑即可")
    print("  3. 登录成功后，回到本窗口按 Enter 键")
    print("  4. Cookie 会自动保存，后续可自动复用")
    print()

    # 检测已有 Cookie
    if COOKIE_FILE.exists():
        print(f"  [提示] 已有 Cookie 文件: {COOKIE_FILE}")
        ans = input("  是否覆盖? (y/N): ").strip().lower()
        if ans != "y":
            print("  已跳过，使用现有 Cookie。")
            return

    # 使用 real Chrome + 独立用户数据目录
    user_data_dir = str(Path(__file__).resolve().parent / "assets" / "chrome_profile")
    print(f"  用户数据目录: {user_data_dir}")
    print()

    playwright = sync_playwright().start()
    try:
        # 检测是否有 real Chrome
        try:
            playwright.chromium.launch_persistent_context(
                user_data_dir,
                headless=False,
                channel="chrome",
                args=["--window-size=1920,1080"],
            ).close()
            has_chrome = True
            channel = "chrome"
        except Exception:
            has_chrome = False
            channel = None
            print("  [提示] 未检测到系统 Chrome，将使用 Playwright Chromium。")
            print("         如果滑块依旧异常，请安装 Chrome 后再试。")

        # 创建持久化上下文（更接近真实浏览器）
        context = playwright.chromium.launch_persistent_context(
            user_data_dir,
            headless=False,
            channel=channel,
            args=[
                "--window-size=1920,1080",
                "--disable-blink-features=AutomationControlled",
            ],
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            no_viewport=False,
        )

        page = context.pages[0] if context.pages else context.new_page()

        # 导航到淘宝首页
        print("  → 正在打开淘宝首页...")
        page.goto("https://www.taobao.com/", wait_until="load", timeout=120000)
        print("  → 已打开，请在浏览器中手动登录")
        print()
        print("  ⚠ 注意事项:")
        print("     - 如果遇到滑块验证，用手拖动即可")
        print("     - 推荐使用淘宝 App 扫码登录，最快捷")
        print("     - 登录后请确认能看到'我的淘宝'或用户名")
        print()
        input("  登录完成后，请按 Enter 键继续...")

        # 检测登录状态
        print("  → 正在检测登录状态...")
        time.sleep(2)

        logged_in = _detect_logged_in(page)
        if not logged_in:
            print()
            print("  ⚠ 未检测到登录状态，重试检测一次？")
            retry = input("  按 Enter 重试检测，输入 q 跳过 (Enter/q): ").strip().lower()
            if retry != "q":
                logged_in = _detect_logged_in(page)

        if logged_in:
            # 保存 Cookie
            os.makedirs(COOKIE_DIR, exist_ok=True)
            cookies = context.cookies()
            with open(COOKIE_FILE, "w") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            print()
            print(f"  ✓ Cookie 已保存! ({len(cookies)} 条)")
            print(f"    路径: {COOKIE_FILE}")
            print()
            print("  之后运行 skill_entry.py 就会自动复用这些 Cookie，")
            print("  不再需要手动登录了。")
        else:
            print()
            print("  ✗ 未检测到登录成功。")
            print("    可能的原因：")
            print("    1. 登录还没完成（可重新运行再试）")
            print("    2. 浏览器被风控拦截（推荐用 Chrome 不是 Playwright Chromium）")
            print("    3. 打开 assets/screenshots/ 下的截图看看状态")

    finally:
        playwright.stop()
        print("  浏览器已关闭。")


def _detect_logged_in(page) -> bool:
    """检测是否已登录。"""
    indicators = [
        ".member-nick",
        "#J_SiteNavMytaobao",
        ".site-nav-user",
        ".site-nav-bd",
        "//a[contains(text(), '我的淘宝')]",
    ]
    for sel in indicators:
        try:
            el = page.wait_for_selector(sel, timeout=5000)
            if el and el.is_visible():
                logger.info("已检测到登录状态: %s", sel)
                return True
        except Exception:
            continue
    return False


if __name__ == "__main__":
    main()
