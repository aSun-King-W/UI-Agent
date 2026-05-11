"""Tests for orchestrator.agent — LLM-driven decision loop.

Mocks Agent._llm_decide to test the decision loop without real API calls.
"""

import os
from unittest.mock import MagicMock, Mock, patch

import pytest

from orchestrator.agent import Agent, AgentError


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def mock_page():
    """Create a fake Playwright Page with minimal structure."""
    page = MagicMock()
    page.url = "about:blank"
    page.title.return_value = ""
    page.inner_text.return_value = ""  # required by PageUtils._get_text_preview
    return page


@pytest.fixture
def agent(mock_page):
    """Build an Agent with a mocked page and test API config."""
    return Agent(
        page=mock_page,
        api_key="test-key",
        api_base="https://api.test.com/v1",
        model="test-model",
        max_steps=10,
        temperature=0.0,
    )


# ── Init tests ───────────────────────────────────────────────────


class TestAgentInit:
    def test_agent_init_with_params(self, mock_page):
        a = Agent(
            page=mock_page,
            api_key="custom-key",
            api_base="https://custom.com",
            model="custom-model",
            max_steps=5,
            temperature=0.5,
        )
        assert a.api_key == "custom-key"
        assert a.api_base == "https://custom.com"
        assert a.model == "custom-model"
        assert a.max_steps == 5
        assert a.temperature == 0.5
        assert a.history == []

    def test_agent_init_uses_env_fallback(self, mock_page, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "env-key")
        monkeypatch.setenv("LLM_API_BASE", "https://env.com")
        monkeypatch.setenv("LLM_MODEL", "env-model")

        a = Agent(page=mock_page)
        assert a.api_key == "env-key"
        assert a.api_base == "https://env.com"
        assert a.model == "env-model"

    def test_agent_init_missing_api_key(self, mock_page, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        with pytest.raises(AgentError, match="LLM_API_KEY 未配置"):
            Agent(page=mock_page)

    def test_agent_has_expected_attributes(self, agent):
        assert hasattr(agent, "run")
        assert hasattr(agent, "_llm_decide")
        assert hasattr(agent, "_execute_tool")
        assert hasattr(agent, "history")
        assert hasattr(agent, "max_steps")
        assert hasattr(agent, "utils")


# ── Tool execution tests ─────────────────────────────────────────


class TestToolExecution:
    def test_execute_known_tool(self, agent):
        """Executing get_page_state should return a result string."""
        result = agent._execute_tool("get_page_state", {})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_execute_unknown_tool(self, agent):
        result = agent._execute_tool("nonexistent", {})
        assert "错误" in result
        assert "未知工具" in result

    def test_execute_tool_with_exception(self, agent, monkeypatch):
        """When a tool handler raises, the agent should catch it."""
        # Patch the handler reference inside the agent module
        import orchestrator.agent as agent_mod

        handler = MagicMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(
            agent_mod,
            "get_handler",
            lambda name, h=handler: h if name == "navigate" else None,
        )

        result = agent._execute_tool("navigate", {"url": "https://x.com"})
        assert "RuntimeError" in result


# ── LLM Decision tests ───────────────────────────────────────────


class TestLLMDecide:
    def test_llm_decide_function_call(self, agent):
        """Agent._llm_decide should extract tool/args/reasoning from LLM response."""
        # Monkey-patch _client.chat.completions.create to return a valid pydantic
        # ChatCompletion via the simplest possible fake.
        import openai

        fake_message = MagicMock(spec=openai.types.chat.ChatCompletionMessage)
        func_mock = MagicMock()
        func_mock.name = "get_page_state"
        func_mock.arguments = "{}"
        fake_message.tool_calls = [MagicMock(function=func_mock)]
        fake_message.content = "先查看当前页面状态"
        fake_message.role = "assistant"

        fake_choice = MagicMock(spec=openai.types.chat.chat_completion.Choice)
        fake_choice.finish_reason = "tool_calls"
        fake_choice.message = fake_message
        fake_choice.index = 0

        fake_resp = MagicMock(spec=openai.types.chat.ChatCompletion)
        fake_resp.choices = [fake_choice]
        fake_resp.id = "test"
        fake_resp.created = 0
        fake_resp.model = "test"

        agent._client.chat.completions.create = MagicMock(return_value=fake_resp)

        messages = [{"role": "system", "content": "test"}]
        decision = agent._llm_decide(messages, "some context")
        assert decision is not None
        assert decision["tool"] == "get_page_state"
        assert decision["args"] == {}
        assert "当前页面" in decision["reasoning"]
        assert decision["assistant_msg"]["role"] == "assistant"
        assert decision["tool_call_id"] is not None

    def test_llm_decide_with_args(self, agent):
        """LLM tool call with arguments should be parsed correctly."""
        import openai

        fake_message = MagicMock(spec=openai.types.chat.ChatCompletionMessage)
        func_mock = MagicMock()
        func_mock.name = "search_product"
        func_mock.arguments = '{"keyword": "索尼耳机"}'
        fake_message.tool_calls = [MagicMock(function=func_mock)]
        fake_message.content = "用户要求搜索索尼耳机"
        fake_message.role = "assistant"

        fake_choice = MagicMock(spec=openai.types.chat.chat_completion.Choice)
        fake_choice.finish_reason = "tool_calls"
        fake_choice.message = fake_message
        fake_choice.index = 0

        fake_resp = MagicMock(spec=openai.types.chat.ChatCompletion)
        fake_resp.choices = [fake_choice]

        agent._client.chat.completions.create = MagicMock(return_value=fake_resp)

        decision = agent._llm_decide([{"role": "system", "content": "test"}], "ctx")
        assert decision["tool"] == "search_product"
        assert decision["args"]["keyword"] == "索尼耳机"

    def test_llm_decide_text_fallback(self, agent):
        """When LLM returns plain text (no tool_calls), default to get_page_state."""
        import openai

        fake_message = MagicMock(spec=openai.types.chat.ChatCompletionMessage)
        fake_message.tool_calls = None
        fake_message.content = "当前页面看起来是搜索结果页"
        fake_message.role = "assistant"

        fake_choice = MagicMock(spec=openai.types.chat.chat_completion.Choice)
        fake_choice.finish_reason = "stop"
        fake_choice.message = fake_message

        fake_resp = MagicMock(spec=openai.types.chat.ChatCompletion)
        fake_resp.choices = [fake_choice]

        agent._client.chat.completions.create = MagicMock(return_value=fake_resp)

        decision = agent._llm_decide([{"role": "system", "content": "test"}], "ctx")
        assert decision["tool"] == "get_page_state"

    def test_llm_decide_retry_on_empty(self, agent):
        """Empty LLM response should trigger retry."""
        import openai

        # First two: empty content, no tool_calls
        empty_msg = MagicMock(spec=openai.types.chat.ChatCompletionMessage)
        empty_msg.tool_calls = None
        empty_msg.content = ""
        empty_msg.role = "assistant"

        empty_choice = MagicMock(spec=openai.types.chat.chat_completion.Choice)
        empty_choice.finish_reason = "stop"
        empty_choice.message = empty_msg

        empty_resp = MagicMock(spec=openai.types.chat.ChatCompletion)
        empty_resp.choices = [empty_choice]

        # Third: valid response
        ok_msg = MagicMock(spec=openai.types.chat.ChatCompletionMessage)
        func_mock = MagicMock()
        func_mock.name = "navigate"
        func_mock.arguments = '{"url": "https://tb.cn"}'
        ok_msg.tool_calls = [MagicMock(function=func_mock)]
        ok_msg.content = "导航到淘宝"
        ok_msg.role = "assistant"

        ok_choice = MagicMock(spec=openai.types.chat.chat_completion.Choice)
        ok_choice.finish_reason = "tool_calls"
        ok_choice.message = ok_msg

        ok_resp = MagicMock(spec=openai.types.chat.ChatCompletion)
        ok_resp.choices = [ok_choice]

        agent._client.chat.completions.create = MagicMock(
            side_effect=[empty_resp, empty_resp, ok_resp]
        )

        decision = agent._llm_decide([{"role": "system", "content": "test"}], "ctx")
        assert decision["tool"] == "navigate"

    def test_llm_decide_fails_all_retries(self, agent):
        """All retries exhausted → return None."""
        agent._client.chat.completions.create = MagicMock(
            side_effect=Exception("API error")
        )

        decision = agent._llm_decide([{"role": "system", "content": "test"}], "ctx")
        assert decision is None


# ── Full agent run tests ─────────────────────────────────────────


class TestAgentRun:
    def test_run_normal_flow(self, agent):
        """Normal flow: LLM returns decisions, finishes correctly."""
        decisions = iter([
            {"tool": "navigate", "args": {"url": "https://www.taobao.com"}, "reasoning": "开始"},
            {"tool": "finish", "args": {"status": "success", "summary": "完成"}, "reasoning": "结束"},
        ])

        def fake_decide(messages, context):
            return next(decisions)

        agent._llm_decide = fake_decide

        result = agent.run("测试")
        assert result["status"] == "success"
        assert result["steps"] == 2
        assert len(result["history"]) == 2

    def test_run_with_progress_callback(self, agent):
        """progress_callback should be called at each step."""
        import inspect

        def fake_decide(messages, context):
            return {"tool": "finish", "args": {"status": "success", "summary": "完成"}, "reasoning": "结束"}

        agent._llm_decide = fake_decide

        calls = []
        agent.run("test", progress_callback=lambda step, msg: calls.append((step, msg)))

        assert len(calls) >= 2

    def test_run_max_steps_reached(self, agent):
        """When max_steps is hit without finish, return 'partial'."""
        def fake_decide(messages, context):
            return {"tool": "get_page_state", "args": {}, "reasoning": "继续"}

        agent._llm_decide = fake_decide
        agent.max_steps = 3

        result = agent.run("测试")
        assert result["status"] == "partial"
        assert result["steps"] == 3
        assert "最大步数" in result["summary"]

    def test_run_llm_failure(self, agent):
        """LLM fails on first call → should return failed."""
        agent._llm_decide = MagicMock(return_value=None)

        result = agent.run("test")
        assert result["status"] == "failed"

    def test_run_history_contains_step_details(self, agent):
        """Each step in history should have all required fields."""
        decisions = iter([
            {"tool": "navigate", "args": {"url": "https://tb.cn"}, "reasoning": "go"},
            {"tool": "finish", "args": {"status": "success", "summary": "done"}, "reasoning": "end"},
        ])

        def fake_decide(messages, context):
            return next(decisions)

        agent._llm_decide = fake_decide

        result = agent.run("测试")
        for entry in result["history"]:
            assert "step" in entry
            assert "tool" in entry
            assert "args" in entry
            assert "reasoning" in entry
            assert "result" in entry

    def test_run_with_tool_error(self, agent):
        """Tool execution error should be captured, not crash the loop."""
        from unittest.mock import MagicMock

        original_execute = agent._execute_tool

        def fake_execute(tool_name, args):
            if tool_name == "search_product":
                return "RuntimeError: 搜索页面超时"
            return original_execute(tool_name, args)

        agent._execute_tool = fake_execute

        decisions = iter([
            {"tool": "search_product", "args": {"keyword": "耳机"}, "reasoning": "搜索"},
            {"tool": "finish", "args": {"status": "failed", "summary": "搜索失败"}, "reasoning": "报告"},
        ])

        def fake_decide(messages, context):
            return next(decisions)

        agent._llm_decide = fake_decide

        result = agent.run("搜索耳机")
        assert result["status"] == "failed"
        assert "RuntimeError" in result["history"][0]["result"]
