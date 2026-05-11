"""Tests for orchestrator.tools — tool definitions, schemas, and handlers."""

import json
from unittest.mock import MagicMock

import pytest

from orchestrator.tools import (
    ToolDef,
    get_all_tools,
    get_openai_tools,
    get_handler,
    get_tool_names,
)


class TestToolRegistration:
    def test_all_tools_count(self):
        tools = get_all_tools()
        assert len(tools) == 9

    def test_all_tools_are_tooldef(self):
        for t in get_all_tools():
            assert isinstance(t, ToolDef)

    def test_tool_has_required_attrs(self):
        for t in get_all_tools():
            assert t.name, f"Tool missing name"
            assert t.description, f"Tool '{t.name}' missing description"
            assert t.parameters, f"Tool '{t.name}' missing parameters"
            assert callable(t.handler), f"Tool '{t.name}' handler not callable"

    def test_tool_names(self):
        names = get_tool_names()
        expected = [
            "navigate",
            "login_taobao",
            "search_product",
            "apply_filter",
            "add_to_cart",
            "scroll_page",
            "get_page_state",
            "extract_products",
            "finish",
        ]
        assert names == expected

    def test_get_handler_found(self):
        handler = get_handler("search_product")
        assert callable(handler)

    def test_get_handler_not_found(self):
        assert get_handler("nonexistent_tool") is None


def _get_tool(name: str) -> dict:
    """Helper: find the inner function definition of an OpenAI-format tool by name."""
    for t in get_openai_tools():
        if t["function"]["name"] == name:
            return t["function"]
    raise AssertionError(f"Tool '{name}' not found")


class TestToolSchemas:
    def test_openai_format(self):
        tools = get_openai_tools()
        assert len(tools) == 9
        for t in tools:
            assert t["type"] == "function"
            func = t["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func

    def test_navigate_schema(self):
        t = _get_tool("navigate")
        assert "url" in t["parameters"]["properties"]
        assert t["parameters"]["required"] == ["url"]

    def test_login_taobao_schema(self):
        t = _get_tool("login_taobao")
        # No required params — it reads from env
        assert t["parameters"]["required"] == []

    def test_search_product_schema(self):
        t = _get_tool("search_product")
        assert "keyword" in t["parameters"]["properties"]
        assert t["parameters"]["required"] == ["keyword"]

    def test_apply_filter_schema(self):
        t = _get_tool("apply_filter")
        props = t["parameters"]["properties"]
        assert "min_rating" in props
        assert props["min_rating"]["type"] == "number"
        assert t["parameters"]["required"] == ["min_rating"]

        # Optional params should have defaults
        assert "max_pages" in props
        assert "check_detail" in props

    def test_add_to_cart_schema(self):
        t = _get_tool("add_to_cart")
        assert "product_index" in t["parameters"]["properties"]
        assert t["parameters"]["required"] == ["product_index"]

    def test_scroll_page_schema(self):
        t = _get_tool("scroll_page")
        direction = t["parameters"]["properties"]["direction"]
        assert direction["type"] == "string"
        assert "enum" in direction
        assert set(direction["enum"]) == {"down", "up", "top", "bottom"}
        assert t["parameters"]["required"] == ["direction"]

    def test_finish_schema(self):
        t = _get_tool("finish")
        props = t["parameters"]["properties"]
        assert "status" in props
        assert props["status"]["type"] == "string"
        assert props["status"]["enum"] == ["success", "partial", "failed"]
        assert t["parameters"]["required"] == ["status", "summary"]


class TestToolHandlers:
    def test_navigate_handler(self, monkeypatch):
        page = MagicMock()
        handler = get_handler("navigate")
        result = handler(page, {"url": "https://www.taobao.com"})
        page.goto.assert_called_once_with(
            "https://www.taobao.com", wait_until="networkidle"
        )
        assert "已导航" in result

    def test_scroll_page_handler(self, monkeypatch):
        page = MagicMock()
        handler = get_handler("scroll_page")

        result_top = handler(page, {"direction": "top"})
        page.evaluate.assert_any_call("window.scrollTo(0, 0)")
        assert "top" in result_top

        result_bottom = handler(page, {"direction": "bottom"})
        page.evaluate.assert_any_call(
            "window.scrollTo(0, document.body.scrollHeight)"
        )
        assert "bottom" in result_bottom

    def test_finish_handler(self):
        page = MagicMock()
        handler = get_handler("finish")
        result = handler(
            page, {"status": "success", "summary": "全部完成"}
        )
        assert result.startswith("__FINISH__:")
        assert "success" in result
        assert "全部完成" in result

    def test_get_page_state_handler(self):
        page = MagicMock()
        page.url = "https://www.taobao.com"
        page.title.return_value = "淘宝网"
        # inner_text must return a real string (not MagicMock) for re.sub
        page.inner_text.return_value = "淘宝网 - 淘! 我喜欢"

        handler = get_handler("get_page_state")
        result = handler(page, {})

        assert "https://www.taobao.com" in result
        assert "淘宝网" in result

    def test_unknown_tool_execution(self):
        """Calling an unregistered tool name should be caught by Agent."""
        handler = get_handler("i_do_not_exist")
        assert handler is None
