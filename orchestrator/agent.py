"""
LLM Agent Orchestration Engine.

Implements the core decision loop:
  observe page state → LLM decides next action → execute tool → check done → repeat

Uses OpenAI-compatible API (DeepSeek) with function calling for tool invocation.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from openai import OpenAI

from core.page_utils import PageUtils
from orchestrator.prompt import build_system_prompt, build_context
from orchestrator.tools import get_openai_tools, get_handler

logger = logging.getLogger("orchestrator.agent")

FINISH_SENTINEL = "__FINISH__:"


class AgentError(Exception):
    """Base error for agent failures."""


class Agent:
    """LLM-driven orchestration agent for browser UI automation.

    Usage::

        from core.browser import BrowserEngine
        from orchestrator.agent import Agent

        engine = BrowserEngine(headless=False)
        page = engine.start()

        agent = Agent(page)
        result = agent.run("搜索索尼耳机，好评率≥99%，加入购物车")

        engine.close()
    """

    def __init__(
        self,
        page,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        max_steps: int = 30,
        temperature: float = 0.1,
    ):
        """
        Args:
            page: Playwright Page instance to operate on.
            model: LLM model name (default: from env LLM_MODEL or "deepseek-chat").
            api_key: LLM API key (default: from env LLM_API_KEY).
            api_base: LLM API base URL (default: from env LLM_API_BASE).
            max_steps: Maximum decision steps before the agent stops.
            temperature: LLM temperature (low = more deterministic).
        """
        self.page = page
        self.max_steps = max_steps
        self.temperature = temperature
        self.utils = PageUtils(page)

        self.model = model or os.getenv("LLM_MODEL", "deepseek-chat")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.api_base = api_base or os.getenv(
            "LLM_API_BASE", "https://api.deepseek.com"
        )

        if not self.api_key:
            raise AgentError(
                "LLM_API_KEY 未配置，请在 .env 文件中设置或通过参数传入"
            )

        self._client = OpenAI(api_key=self.api_key, base_url=self.api_base)
        self._tools = get_openai_tools()

        # Execution trace — populated by run()
        self.history: List[Dict[str, Any]] = []

    # ── Public API ───────────────────────────────────────────────

    def run(
        self,
        user_goal: str,
        *,
        progress_callback=None,
    ) -> Dict[str, Any]:
        """Execute the full agent workflow.

        Args:
            user_goal: The user's instruction, e.g. "搜索索尼耳机，好评率≥99%，加入购物车".
            progress_callback: Optional fn(step: int, message: str) for progress reporting.

        Returns:
            Dict with keys: status ("success"|"partial"|"failed"), summary, steps, history.
        """
        logger.info("Agent 启动 | goal: %s | max_steps: %d", user_goal, self.max_steps)
        if progress_callback:
            progress_callback(0, f"Agent 启动 — {user_goal}")

        # System prompt (fixed for the entire run)
        system_prompt = build_system_prompt(max_steps=self.max_steps)
        messages: List[Dict] = [{"role": "system", "content": system_prompt}]

        # State trackers
        last_tool = "none"
        last_args = ""
        last_result = "初始化完成，准备开始"

        for step in range(1, self.max_steps + 1):
            logger.info("─" * 40)
            logger.info("Step %d / %d", step, self.max_steps)

            # ── 1. Observe ────────────────────────────────────────
            page_state = self.utils.get_page_state()

            context = build_context(
                url=page_state["url"],
                mode=page_state["mode"],
                title=page_state["title"],
                interactive=page_state.get("interactive", []),
                text_preview=page_state.get("text_preview", ""),
                last_tool=last_tool,
                last_args=last_args,
                last_result=last_result,
                user_goal=user_goal,
                current_step=step,
                max_steps=self.max_steps,
            )

            if progress_callback:
                progress_callback(
                    step, f"观察: {page_state['mode']} — {page_state['title'][:40]}"
                )

            # ── 2. LLM decides ────────────────────────────────────
            decision = self._llm_decide(messages, context)
            if decision is None:
                return self._finish("failed", "LLM 连续决策失败，终止执行", step)

            tool_name = decision["tool"]
            tool_args = decision["args"]
            reasoning = decision.get("reasoning", "")
            assistant_msg = decision.get("assistant_msg")
            tool_call_id = decision.get("tool_call_id")

            logger.info("决策: %s %s | %s", tool_name, tool_args, reasoning)

            if progress_callback:
                progress_callback(step, f"决策: {tool_name}")

            # Append assistant message to conversation history
            if assistant_msg is not None:
                messages.append(assistant_msg)

            # ── 3. Execute ────────────────────────────────────────
            last_tool = tool_name
            last_args = tool_args
            result = self._execute_tool(tool_name, tool_args)

            # Append tool result to conversation history (required after tool_calls)
            if tool_call_id is not None:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result,
                })

            # Record in history
            self.history.append({
                "step": step,
                "tool": tool_name,
                "args": tool_args,
                "reasoning": reasoning,
                "result": result,
            })

            last_result = result

            # ── 4. Check done ─────────────────────────────────────
            if result.startswith(FINISH_SENTINEL):
                _, status, summary = result.split(":", 2)
                logger.info("Agent 结束: %s — %s", status, summary)
                if progress_callback:
                    progress_callback(step, f"完成: {summary}")
                return {
                    "status": status,
                    "summary": summary,
                    "steps": step,
                    "history": self.history,
                }

        # ── Max steps reached ────────────────────────────────────
        logger.warning("达到最大步数 %d", self.max_steps)
        if progress_callback:
            progress_callback(self.max_steps, "达到最大步数，任务可能未完成")

        return self._finish(
            "partial",
            f"达到最大步数 {self.max_steps}，任务可能未完成",
            self.max_steps,
        )

    # ── LLM Interaction ──────────────────────────────────────────

    def _llm_decide(
        self, messages: List[Dict], context: str
    ) -> Optional[Dict[str, Any]]:
        """Send context to the LLM and get back a tool decision.

        The caller (run) is responsible for:
        - Appending the returned ``assistant_msg`` to ``messages``
        - Appending a ``tool`` role message after tool execution

        Returns dict with keys ``tool``, ``args``, ``reasoning``,
        ``assistant_msg`` (dict), and optionally ``tool_call_id`` (str),
        or None if all retries failed.
        """
        # Append the turn context as a user message
        messages.append({"role": "user", "content": context})

        for attempt in range(3):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=self._tools,
                    tool_choice="auto",
                    temperature=self.temperature,
                    max_tokens=2000,
                )
            except Exception as exc:
                logger.warning(
                    "LLM call attempt %d/3 failed: %s", attempt + 1, exc
                )
                if attempt < 2:
                    continue
                messages.pop()
                return None

            choice = response.choices[0]
            msg = choice.message

            # ── Path A: LLM used function calling ────────────────
            if choice.finish_reason == "tool_calls" and msg.tool_calls:
                tc = msg.tool_calls[0]
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                reasoning = (msg.content or "").strip()

                return {
                    "tool": tool_name,
                    "args": tool_args,
                    "reasoning": reasoning,
                    "assistant_msg": {
                        "role": "assistant",
                        "content": msg.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": tc.type,
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                        ],
                    },
                    "tool_call_id": tc.id,
                }

            # ── Path B: LLM returned plain-text / JSON fallback ──
            content = (msg.content or "").strip()
            if content:
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict) and "tool" in parsed:
                        return {
                            **parsed,
                            "assistant_msg": {
                                "role": "assistant",
                                "content": msg.content,
                            },
                            "tool_call_id": None,
                        }
                except json.JSONDecodeError:
                    pass

                # Treat plain text as a request for get_page_state
                logger.info(
                    "LLM returned text (not function call); default to get_page_state"
                )
                return {
                    "tool": "get_page_state",
                    "args": {},
                    "reasoning": content,
                    "assistant_msg": {
                        "role": "assistant",
                        "content": msg.content,
                    },
                    "tool_call_id": None,
                }

            # Empty response
            logger.warning("LLM returned empty response (attempt %d/3)", attempt + 1)

            if attempt < 2:
                continue

        messages.pop()
        return None

    # ── Tool Execution ──────────────────────────────────────────

    def _execute_tool(self, tool_name: str, args: dict) -> str:
        """Run a tool handler and return its result string.

        Wraps exceptions so the LLM can decide how to recover.
        """
        handler = get_handler(tool_name)
        if handler is None:
            return f"错误: 未知工具 '{tool_name}'，可用工具: {get_openai_tools()}"

        logger.info("执行工具: %s %s", tool_name, args)
        try:
            result = handler(self.page, args)
            logger.info("工具结果: %s", result[:120])
            return result
        except Exception as exc:
            logger.error("工具 %s 异常: %s", tool_name, exc)
            return f"工具 {tool_name} 执行异常: {type(exc).__name__}: {exc}"

    # ── Internal helpers ─────────────────────────────────────────

    def _finish(self, status: str, summary: str, steps: int) -> Dict[str, Any]:
        """Build a standard result dict."""
        return {
            "status": status,
            "summary": summary,
            "steps": steps,
            "history": self.history,
        }
