"""
Agent system prompt templates for the UI automation orchestrator.

Defines the agent's role, constraints, decision rules, and the
context template used at each step of the decision loop.
"""

SYSTEM_PROMPT = """你是一个专业的 UI 自动化测试 Agent，负责在淘宝网页端执行操作。

## 你的目标
根据用户指令，在淘宝上完成商品搜索、筛选和加购操作。

## 工作方式
你通过调用工具来操作浏览器。每次你会收到：
1. 当前页面 URL 与页面模式（首页/登录页/搜索结果/商品详情/购物车）
2. 页面状态摘要（可见的按钮、链接、输入框等交互元素）
3. 页面的可见文本内容
4. 上一步操作和结果
5. 你的当前进度（第几步/总共几步）

你需要分析当前状态，决定下一步该做什么，并调用相应的工具。

## 典型流程
1. 导航到淘宝首页 → 2. 登录（如果需要） → 3. 搜索商品 → 4. 应用筛选条件 → 5. 将商品加入购物车 → 6. 调用 finish 报告结果

## 决策规则
1. 每一步完成后检查结果是否正常。如果操作返回失败信息，分析原因后重试或调整策略
2. 如果某个工具调用失败，最多重试 2 次，尝试不同的方法
3. 如果重试后仍失败，调用 finish 报告失败原因
4. 如果页面上有弹窗/遮挡，使用 scroll_page 或寻找关闭按钮
5. 在点击元素前，先用 scroll_page 确保元素可见
6. 仔细阅读页面文本内容来定位正确的交互元素，不要假设固定的 CSS 选择器
7. 使用 get_page_state 了解当前页面状态，然后再决定下一步

## 输出格式
你必须通过函数调用（tool_calls）返回决策。每次只调用一个工具。
在 function arguments 中的 tool/args/reasoning 字段内写明思考过程。

调用完成后，将工具返回的结果作为下一步决策的依据。

## 约束
- 只使用提供的工具，不要生成未定义的操作
- 最大决策步数限制为 {max_steps} 步
- 用户凭证已通过环境变量配置，不需要询问
- 只执行合法的测试任务"""

CONTEXT_TEMPLATE = """## 当前页面状态
- URL: {url}
- 页面模式: {mode}
- 页面标题: {title}

## 页面交互元素
{interactive}

## 页面关键文本内容
{text_preview}

## 上一步操作
- 工具: {last_tool}
- 参数: {last_args}
- 结果: {last_result}

## 用户目标
{user_goal}

## 进度
{current_step}/{max_steps}"""


def build_context(
    url: str,
    mode: str,
    title: str,
    interactive: list,
    text_preview: str,
    last_tool: str,
    last_args: str,
    last_result: str,
    user_goal: str,
    current_step: int,
    max_steps: int,
) -> str:
    """Build the per-turn context string for the LLM.

    Args:
        url: Current page URL.
        mode: Inferred page type (login/search_results/product_detail/cart/home/unknown).
        title: Page <title> text.
        interactive: List of interactable elements on the page.
        text_preview: Condensed visible text from the page body.
        last_tool: Name of the tool called in the previous step.
        last_args: Arguments passed to the previous tool.
        last_result: Result string from the previous tool execution.
        user_goal: The user's original instruction.
        current_step: Current step number (1-indexed).
        max_steps: Maximum allowed steps.

    Returns:
        Formatted context string.
    """
    # Format interactive elements (limit to 20 for token efficiency)
    interactive_lines = []
    for el in interactive[:20]:
        text = el.get("text", "")
        hint = el.get("selector_hint", "")
        tag = el.get("tag", "")
        interactive_lines.append(f"  - [{tag}] {text}  ({hint})")

    if len(interactive) > 20:
        interactive_lines.append(f"  ... 另有 {len(interactive) - 20} 个交互元素")

    interactive_str = "\n".join(interactive_lines) if interactive_lines else "  (空)"

    # Truncate text preview
    preview = (text_preview or "")[:1500]

    return CONTEXT_TEMPLATE.format(
        url=url,
        mode=mode,
        title=title,
        interactive=interactive_str,
        text_preview=preview or "  (空)",
        last_tool=last_tool,
        last_args=str(last_args),
        last_result=last_result,
        user_goal=user_goal,
        current_step=current_step,
        max_steps=max_steps,
    )


def build_system_prompt(max_steps: int = 30) -> str:
    """Build the system prompt with configurable max steps."""
    return SYSTEM_PROMPT.format(max_steps=max_steps)
