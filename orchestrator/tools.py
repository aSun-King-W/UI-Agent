"""
Tool definitions and handlers for the LLM Agent orchestrator.

Each tool has:
- name / description / parameters (JSON Schema) — for LLM function calling
- handler — the actual implementation that operates on the Playwright Page
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from playwright.sync_api import Page

from core.login import TaobaoLogin
from core.search import TaobaoSearch, Product
from core.filter import TaobaoFilter
from core.cart import TaobaoCart
from core.page_utils import PageUtils

logger = logging.getLogger("orchestrator.tools")


# ── Data structures ──────────────────────────────────────────────


@dataclass
class ToolDef:
    """Definition of a tool available to the LLM agent."""

    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable  # (page: Page, args: dict) -> str


# ── JSON Schemas ─────────────────────────────────────────────────

NAVIGATE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "目标完整 URL，如 https://www.taobao.com",
        }
    },
    "required": ["url"],
}

LOGIN_TAOBAO_SCHEMA: dict = {
    "type": "object",
    "properties": {},
    "required": [],
}

SEARCH_PRODUCT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "keyword": {
            "type": "string",
            "description": "搜索关键词，如 '索尼耳机'",
        }
    },
    "required": ["keyword"],
}

APPLY_FILTER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "min_rating": {
            "type": "number",
            "description": "最低好评率（0-1），如 0.99 表示 99%",
        },
        "max_pages": {
            "type": "integer",
            "description": "最多扫描的搜索结果页数，默认 3",
        },
        "check_detail": {
            "type": "boolean",
            "description": "是否进入详情页获取好评率（更准确但更慢），默认 false",
        },
    },
    "required": ["min_rating"],
}

ADD_TO_CART_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "product_index": {
            "type": "integer",
            "description": "商品在列表中的序号（从 0 开始）",
        }
    },
    "required": ["product_index"],
}

SCROLL_PAGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "direction": {
            "type": "string",
            "enum": ["down", "up", "top", "bottom"],
            "description": "down=向下300px  up=向上300px  top=回到顶部  bottom=滚到底部",
        }
    },
    "required": ["direction"],
}

GET_PAGE_STATE_SCHEMA: dict = {
    "type": "object",
    "properties": {},
}

EXTRACT_PRODUCTS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "max_items": {
            "type": "integer",
            "description": "最大提取商品数量，默认 20",
        }
    },
}

FINISH_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["success", "partial", "failed"],
            "description": "success=完全成功  partial=部分完成  failed=失败",
        },
        "summary": {
            "type": "string",
            "description": "执行结果总结",
        },
    },
    "required": ["status", "summary"],
}


# ── Handlers ─────────────────────────────────────────────────────


def _handle_navigate(page: Page, args: dict) -> str:
    """导航到指定 URL。

    如果遇到淘宝风控拦截页（deny_h5.html），自动重试导航。
    """
    import time

    url = args["url"]
    logger.info("Tool: navigate(%s)", url)

    for attempt in range(3):
        try:
            page.goto(url, wait_until="networkidle")
        except Exception as exc:
            logger.warning("导航 attempt %d/3 失败: %s", attempt + 1, exc)
            time.sleep(2)
            continue

        # 检测是否被拦截
        current_url = page.url
        if any(p in current_url for p in ["deny_h5.html", "punish", "rgv587_flag"]):
            logger.warning("风控拦截页 detected, retry %d/3", attempt + 1)
            time.sleep(3)
            continue

        return f"已导航到 {url}"

    return f"导航可能被风控拦截，当前页面: {page.url[:100]}"


def _handle_login_taobao(page: Page, args: dict) -> str:
    """执行淘宝登录，优先使用已保存的 Cookie。"""
    logger.info("Tool: login_taobao()")
    login_mgr = TaobaoLogin(page)

    # 尝试 Cookie 登录
    if login_mgr.is_logged_in():
        return "Cookie 登录成功"

    # 密码登录
    username = os.getenv("TAOBAO_USERNAME", "")
    password = os.getenv("TAOBAO_PASSWORD", "")
    if not username or not password:
        return "错误: 未配置 TAOBAO_USERNAME / TAOBAO_PASSWORD"

    if login_mgr.login(username, password):
        return "帐密登录成功"
    else:
        return "登录失败 — 可能是滑块验证码拦截或凭证错误"


def _handle_search_product(page: Page, args: dict) -> str:
    """在淘宝搜索商品。"""
    keyword = args["keyword"]
    logger.info("Tool: search_product(%s)", keyword)
    search = TaobaoSearch(page)

    if not search.search(keyword):
        return f"搜索 '{keyword}' 失败"

    summary = search.get_page_state_summary()
    products = search.extract_products(max_items=5)
    return (
        f"搜索 '{keyword}' 完成 | "
        f"结果数: {summary.get('total_results', '未知')} | "
        f"提取到 {len(products)} 个商品"
    )


def _handle_apply_filter(page: Page, args: dict) -> str:
    """对搜索结果应用好评率筛选。"""
    min_rating = args.get("min_rating", 0.99)
    max_pages = args.get("max_pages", 3)
    check_detail = args.get("check_detail", False)

    logger.info("Tool: apply_filter(min_rating=%s, max_pages=%s)", min_rating, max_pages)

    search = TaobaoSearch(page)
    flt = TaobaoFilter(page, search)
    qualified = flt.filter_by_rating(
        min_rating=min_rating,
        max_pages=max_pages,
        check_detail=check_detail,
    )

    if not qualified:
        return f"未找到好评率 ≥ {min_rating*100:.0f}% 的商品"

    lines = [f"筛选完成，共 {len(qualified)} 个商品好评率 ≥ {min_rating*100:.0f}%："]
    for i, p in enumerate(qualified[:5], 1):
        rating_str = f"好评率 {p.rating*100:.0f}%" if p.rating else "好评率未知"
        lines.append(f"  {i}. {p.title[:40]} | ¥{p.price:.2f} | {rating_str}")
    if len(qualified) > 5:
        lines.append(f"  ... 还有 {len(qualified) - 5} 个")

    return "\n".join(lines)


def _handle_add_to_cart(page: Page, args: dict) -> str:
    """将指定商品加入购物车。"""
    product_index = args["product_index"]
    logger.info("Tool: add_to_cart(product_index=%s)", product_index)

    search = TaobaoSearch(page)
    products = search.extract_products(max_items=40)
    if not products:
        return "错误: 当前页面没有商品数据，请先搜索"

    # 优先使用筛选结果
    flt = TaobaoFilter(page, search)
    qualified = flt.filter_by_rating(min_rating=0.0, max_pages=1)
    target = qualified if qualified else products

    if product_index >= len(target):
        return f"错误: 序号 {product_index} 超出范围，共 {len(target)} 个商品"

    product = target[product_index]
    cart = TaobaoCart(page)
    if cart.add_to_cart(product):
        return f"✓ 已加入购物车: {product.title[:50]} | ¥{product.price:.2f}"
    else:
        return f"✗ 加购失败: {product.title[:50]}"


def _handle_scroll_page(page: Page, args: dict) -> str:
    """滚动页面。"""
    direction = args["direction"]
    logger.info("Tool: scroll_page(%s)", direction)
    utils = PageUtils(page)

    {
        "top": utils.scroll_to_top,
        "bottom": utils.scroll_to_bottom,
        "down": lambda: utils.scroll_by(dy=300),
        "up": lambda: utils.scroll_by(dy=-300),
    }[direction]()

    return f"已滚动: {direction}"


def _handle_get_page_state(page: Page, args: dict) -> str:
    """获取当前页面状态摘要。"""
    logger.info("Tool: get_page_state()")
    utils = PageUtils(page)
    state = utils.get_page_state()

    lines = [
        f"URL: {state['url']}",
        f"标题: {state['title']}",
        f"页面模式: {state['mode']}",
    ]
    if state["status_messages"]:
        lines.append(f"状态消息: {'; '.join(state['status_messages'][:3])}")

    return "\n".join(lines)


def _handle_extract_products(page: Page, args: dict) -> str:
    """提取当前页面的商品列表。"""
    max_items = args.get("max_items", 20)
    logger.info("Tool: extract_products(max_items=%s)", max_items)
    search = TaobaoSearch(page)
    products = search.extract_products(max_items=max_items)

    if not products:
        return "未找到商品"

    lines = [f"共 {len(products)} 个商品："]
    for i, p in enumerate(products[:10], 1):
        rating_str = f"好评率 {p.rating*100:.0f}%" if p.rating else "好评率未知"
        lines.append(f"  {i}. {p.title[:50]}")
        lines.append(f"     ¥{p.price:.2f} | {rating_str} | {p.reviews} 条评价")
    if len(products) > 10:
        lines.append(f"  ... 另有 {len(products) - 10} 个")

    return "\n".join(lines)


def _handle_finish(page: Page, args: dict) -> str:
    """完成任务并报告结果。"""
    status = args["status"]
    summary = args["summary"]
    logger.info("Tool: finish(status=%s)", status)
    return f"__FINISH__:{status}:{summary}"


# ── Registry ─────────────────────────────────────────────────────


_TOOL_REGISTRY: List[ToolDef] = [
    ToolDef(
        name="navigate",
        description="导航到指定的 URL",
        parameters=NAVIGATE_SCHEMA,
        handler=_handle_navigate,
    ),
    ToolDef(
        name="login_taobao",
        description="执行淘宝登录（优先使用已保存的 Cookie，否则用环境变量中的凭证）",
        parameters=LOGIN_TAOBAO_SCHEMA,
        handler=_handle_login_taobao,
    ),
    ToolDef(
        name="search_product",
        description="在淘宝搜索商品并提取搜索结果",
        parameters=SEARCH_PRODUCT_SCHEMA,
        handler=_handle_search_product,
    ),
    ToolDef(
        name="apply_filter",
        description="对搜索结果应用好评率筛选，自动翻页直到找到符合条件的商品",
        parameters=APPLY_FILTER_SCHEMA,
        handler=_handle_apply_filter,
    ),
    ToolDef(
        name="add_to_cart",
        description="将指定序号的商品加入购物车（先搜索和筛选后再调用）",
        parameters=ADD_TO_CART_SCHEMA,
        handler=_handle_add_to_cart,
    ),
    ToolDef(
        name="scroll_page",
        description="滚动页面以加载更多内容或使隐藏元素可见",
        parameters=SCROLL_PAGE_SCHEMA,
        handler=_handle_scroll_page,
    ),
    ToolDef(
        name="get_page_state",
        description="获取当前页面的状态摘要（URL、标题、模式、状态消息）",
        parameters=GET_PAGE_STATE_SCHEMA,
        handler=_handle_get_page_state,
    ),
    ToolDef(
        name="extract_products",
        description="从搜索结果页提取商品列表（标题、价格、评价数、好评率）",
        parameters=EXTRACT_PRODUCTS_SCHEMA,
        handler=_handle_extract_products,
    ),
    ToolDef(
        name="finish",
        description="完成任务并报告结果。调用此工具后 Agent 将停止",
        parameters=FINISH_SCHEMA,
        handler=_handle_finish,
    ),
]


# ── Public helpers ───────────────────────────────────────────────


def get_all_tools() -> List[ToolDef]:
    """Return the full list of tool definitions."""
    return _TOOL_REGISTRY


def get_openai_tools() -> List[Dict[str, Any]]:
    """Return tools formatted for OpenAI-compatible function calling API."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in _TOOL_REGISTRY
    ]


def get_handler(name: str) -> Optional[Callable]:
    """Look up a tool handler by name. Returns None if not found."""
    for t in _TOOL_REGISTRY:
        if t.name == name:
            return t.handler
    return None


def get_tool_names() -> List[str]:
    """Return all registered tool names."""
    return [t.name for t in _TOOL_REGISTRY]
