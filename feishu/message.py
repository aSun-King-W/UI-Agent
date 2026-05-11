"""
飞书消息构造工具
"""

import json
from typing import List, Optional


def build_text_message(text: str) -> str:
    """构造文本消息内容"""
    return json.dumps({"text": text})


def build_progress_message(step: int, status: str, detail: str = "") -> str:
    """构造进度通知消息

    Args:
        step: 当前步骤序号（0 表示初始化/启动阶段）。
        status: 状态标签，如 "启动" / "登录" / "搜索" / "筛选" / "加购" / "完成"。
        detail: 补充描述信息。
    """
    icon_map = {
        "启动": "[启动]",
        "登录": "[登录]",
        "搜索": "[搜索]",
        "筛选": "[筛选]",
        "加购": "[加购]",
        "完成": "[完成]",
        "失败": "[失败]",
    }
    icon = icon_map.get(status, f"[{status}]")

    lines = [f"{icon} 进度更新 (Step {step})"]
    if detail:
        lines.append(detail)
    return json.dumps({"text": "\n".join(lines)})


def build_result_message(
    status: str,
    keyword: str,
    products_added: int,
    details: list,
    error: Optional[str] = None,
) -> str:
    """构造测试结果消息"""
    lines = [
        "📋 **UI自动化测试结果**",
        f"状态: {'✅ 成功' if status == 'success' else '⚠️ 部分成功' if status == 'partial' else '❌ 失败'}",
        f"搜索关键词: {keyword}",
        f"成功加购: {products_added} 件商品",
        "",
    ]

    if details:
        lines.append("商品详情:")
        for i, item in enumerate(details, 1):
            lines.append(
                f"  {i}. {item.get('title', '未知')} "
                f"- ¥{item.get('price', '?')} "
                f"- 好评率: {item.get('rating', '?')}%"
            )

    if error:
        lines.extend(["", f"异常信息: {error}"])

    lines.append("")
    lines.append("---")
    lines.append("_Agent Skill 自动执行_")

    return json.dumps({"text": "\n".join(lines)})
