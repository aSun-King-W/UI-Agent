"""
UI自动化测试 Agent Skill - 主入口

飞书长连接模式启动，接收消息后：
  1. 立即回复确认
  2. 后台启动浏览器 + Agent 编排引擎
  3. 实时进度回传飞书
  4. 执行完毕回传结构化结果
"""

import json
import logging
import os
import threading
from dotenv import load_dotenv

from feishu.bot import FeishuBot
from feishu.message import build_text_message, build_result_message
from core.browser import BrowserEngine

logger = logging.getLogger("skill_entry")

load_dotenv()

# ── Bot 引用（main 中赋值，供 handler 和后台线程使用） ──────────
_current_bot: FeishuBot = None

# ── Agent 工具名称 → 中文进度标签映射 ──────────────────────────
_STEP_LABELS = {
    "navigate": "导航",
    "login_taobao": "登录",
    "search_product": "搜索",
    "apply_filter": "筛选",
    "add_to_cart": "加购",
    "scroll_page": "滚动",
    "get_page_state": "观察",
    "extract_products": "提取",
    "finish": "完成",
}


def handle_feishu_message(text: str, sender_id: dict, chat_id: str) -> str:
    """飞书消息入口。

    消息示例:
    - "执行测试"                        → 默认参数
    - "搜索华为耳机，好评率>=98%"        → 自定义参数
    """
    params = parse_task_params(text)
    logger.info("收到指令 | text=%s | params=%s", text, params)

    # 立即返回确认消息
    reply = (
        "[确认] 已收到测试任务！\n"
        f"关键词: {params['keyword']}\n"
        f"好评率: >= {params['min_rating'] * 100:.0f}%\n"
        "正在启动浏览器执行，进度将在此回传..."
    )

    # 后台异步执行 Agent 编排
    t = threading.Thread(
        target=_run_agent_task,
        args=(params, chat_id),
        daemon=True,
    )
    t.start()

    return reply


# ── Agent 后台执行 ──────────────────────────────────────────────


def _run_agent_task(params: dict, chat_id: str):
    """在后台线程中启动浏览器 + Agent 编排，并通过飞书回传进度。"""
    engine: BrowserEngine = None
    try:
        _send_progress(chat_id, 1, "启动", "正在启动浏览器...")

        # 1. 启动浏览器
        engine = BrowserEngine(
            headless=False,        # 开发阶段有头模式，部署可改为 True
            slow_mo=100,
            screenshot_dir=os.getenv(
                "SCREENSHOT_DIR",
                os.path.join(os.path.dirname(__file__), "assets", "screenshots"),
            ),
        )
        page = engine.start()

        # 2. 构造用户目标
        user_goal = _build_user_goal(params)

        # 3. 初始化 Agent
        from orchestrator.agent import Agent

        agent = Agent(
            page=page,
            max_steps=30,
            temperature=0.1,
        )

        # 4. 构造进度回调
        def progress_callback(step: int, message: str):
            label = _infer_step_label(message, step)
            _send_progress(chat_id, step, label, message)

        # 5. 执行
        _send_progress(chat_id, 2, "启动", f"目标: {user_goal}")
        result = agent.run(user_goal, progress_callback=progress_callback)

        # 6. 回传最终结果
        _send_final_result(chat_id, result, params)

    except Exception as exc:
        logger.error("Agent 执行异常: %s", exc, exc_info=True)
        _send_progress(chat_id, 0, "失败", f"执行异常: {exc}")
    finally:
        if engine:
            try:
                engine.close()
            except Exception:
                pass


# ── 飞书消息发送 ────────────────────────────────────────────────


def _send_progress(chat_id: str, step: int, label: str, detail: str):
    """向飞书发送进度消息。"""
    if not _current_bot:
        logger.debug("no bot reference, skip progress: %s", detail)
        return
    try:
        text = f"[{label}] (Step {step})\n{detail[:200]}"
        _current_bot.send_text(chat_id, text)
    except Exception as e:
        logger.warning("发送进度消息失败: %s", e)


def _send_final_result(chat_id: str, result: dict, params: dict):
    """向飞书发送最终执行结果。"""
    if not _current_bot:
        logger.debug("no bot reference, skip final result")
        return

    status = result.get("status", "failed")
    summary = result.get("summary", "无结果")
    steps = result.get("steps", 0)

    # 状态 emoji 用文字替代以保持一致性
    status_text = {"success": "成功", "partial": "部分完成", "failed": "失败"}.get(
        status, "未知"
    )

    lines = [
        f"[结果] UI自动化测试 - {status_text}",
        f"关键词: {params['keyword']}",
        f"筛选条件: 好评率 >= {params['min_rating'] * 100:.0f}%",
        f"执行步数: {steps}",
        "",
        summary,
    ]

    _current_bot.send_text(chat_id, "\n".join(lines))


# ── 辅助函数 ────────────────────────────────────────────────────


def _build_user_goal(params: dict) -> str:
    """将结构化参数转为 Agent 可理解的自然语言目标。"""
    rating_pct = params["min_rating"] * 100
    return (
        f"搜索{params['keyword']}，"
        f"筛选好评率≥{rating_pct:.0f}%的商品，"
        f"将符合条件的商品加入购物车"
    )


def _infer_step_label(message: str, step: int) -> str:
    """从 Agent 的消息中推断当前步骤标签。"""
    msg_lower = message.lower()
    for keyword, label in [
        ("login", "登录"),
        ("登录", "登录"),
        ("搜索", "搜索"),
        ("筛选", "筛选"),
        ("filter", "筛选"),
        ("加购", "加购"),
        ("购物车", "加购"),
        ("cart", "加购"),
        ("滚动", "滚动"),
        ("scroll", "滚动"),
        ("导航", "导航"),
        ("navigate", "导航"),
        ("提取", "提取"),
        ("完成", "完成"),
        ("finish", "完成"),
    ]:
        if keyword in msg_lower:
            return label
    return "执行"


def parse_task_params(text: str) -> dict:
    """从消息文本中解析任务参数。"""
    params = {
        "keyword": "索尼耳机",
        "min_rating": 0.99,
        "platform": "taobao",
    }

    text = text.strip()

    import re

    kw_match = re.search(r"(?:搜索|关键词|搜)[：:\s]*([^，。,\.]+)", text)
    if kw_match:
        kw = kw_match.group(1).strip()
        if kw:
            params["keyword"] = kw

    rating_match = re.search(r"好评率[≥>=]+\s*(\d+\.?\d*)%?", text)
    if rating_match:
        rating = float(rating_match.group(1))
        params["min_rating"] = rating / 100.0 if rating > 1 else rating

    return params


# ── 启动入口 ────────────────────────────────────────────────────


def main():
    """启动飞书机器人长连接。"""
    global _current_bot

    bot = FeishuBot()
    _current_bot = bot
    bot.on_message(handle_feishu_message)

    logger.info("=" * 50)
    logger.info("UI自动化测试 Agent Skill 启动中...")
    logger.info("飞书长连接模式 | LLM Agent 驱动编排")
    logger.info("=" * 50)

    bot.start()


if __name__ == "__main__":
    main()
