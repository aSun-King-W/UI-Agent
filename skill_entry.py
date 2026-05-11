"""
UI自动化测试 Agent Skill - 主入口
飞书长连接模式启动，接收消息后触发 LLM Agent 编排执行
"""

import os
import json
import logging
from dotenv import load_dotenv

from feishu.bot import FeishuBot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("skill_entry")

# 加载 .env 配置
load_dotenv()


def handle_feishu_message(text: str, sender_id: dict, chat_id: str) -> str:
    """处理飞书消息

    消息格式示例:
    - "执行测试"                          # 使用默认参数
    - "搜索华为耳机，好评率≥98%"            # 自定义参数
    """
    logger.info(f"收到指令: {text} | chat: {chat_id}")

    # 解析任务参数
    params = parse_task_params(text)

    # TODO (Phase 3): 调用 LLM Agent 编排引擎执行完整流程
    # 目前返回占位响应
    reply = (
        f"✅ 已收到测试任务！\n"
        f"参数: {json.dumps(params, ensure_ascii=False)}\n\n"
        f"即将执行: 登录淘宝 → 搜索商品 → 筛选好评率 → 加入购物车\n"
        f"请稍候，结果将通过飞书回传..."
    )

    # TODO: 异步执行 Agent 编排，并将进度和结果回传
    return reply


def parse_task_params(text: str) -> dict:
    """从消息文本中解析任务参数"""
    params = {
        "keyword": "索尼耳机",
        "min_rating": 0.99,
        "platform": "taobao",
    }

    text = text.strip()

    # 尝试从文本中提取关键词
    import re
    # 匹配 "搜索XXX" 或 "关键词XXX"，在遇到逗号/好评率/句号等时停止
    kw_match = re.search(r"(?:搜索|关键词|搜)[：:\s]*([^，。,\.]+)", text)
    if kw_match:
        kw = kw_match.group(1).strip()
        # 如果有关键词 且 包含"耳机"等商品词
        params["keyword"] = kw

    # 匹配好评率条件
    rating_match = re.search(r"好评率[≥>=]+\s*(\d+\.?\d*)%?", text)
    if rating_match:
        rating = float(rating_match.group(1))
        params["min_rating"] = rating / 100.0 if rating > 1 else rating

    return params


def main():
    """启动飞书机器人长连接"""
    bot = FeishuBot()
    bot.on_message(handle_feishu_message)

    logger.info("=" * 50)
    logger.info("UI自动化测试 Agent Skill 启动中...")
    logger.info("飞书长连接模式 | DeepSeek LLM 驱动编排")
    logger.info("=" * 50)

    bot.start()


if __name__ == "__main__":
    main()
