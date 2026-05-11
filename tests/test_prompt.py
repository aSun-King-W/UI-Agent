"""Tests for orchestrator.prompt — prompt templates and context building."""

from orchestrator.prompt import build_system_prompt, build_context


class TestSystemPrompt:
    def test_build_system_prompt_default(self):
        prompt = build_system_prompt()
        assert prompt.startswith("你是一个专业的 UI 自动化测试 Agent")
        assert "{max_steps}" not in prompt
        assert "30" in prompt

    def test_build_system_prompt_custom_steps(self):
        prompt = build_system_prompt(max_steps=50)
        assert "50" in prompt
        assert "30" not in prompt

    def test_system_prompt_contains_key_sections(self):
        prompt = build_system_prompt(max_steps=30)
        assert "你的目标" in prompt
        assert "工作方式" in prompt
        assert "决策规则" in prompt
        assert "输出格式" in prompt
        assert "约束" in prompt


class TestBuildContext:
    def test_build_context_minimal(self):
        ctx = build_context(
            url="https://www.taobao.com",
            mode="home",
            title="淘宝网",
            interactive=[],
            text_preview="淘宝网首页",
            last_tool="none",
            last_args="",
            last_result="初始化",
            user_goal="test",
            current_step=1,
            max_steps=30,
        )
        assert "https://www.taobao.com" in ctx
        assert "home" in ctx
        assert "淘宝网" in ctx
        assert "1/30" in ctx

    def test_build_context_with_interactive_elements(self):
        interactive = [
            {"tag": "button", "text": "搜索", "selector_hint": "button: 搜索"},
            {"tag": "link", "text": "我的淘宝", "selector_hint": "link: 我的淘宝"},
        ]
        ctx = build_context(
            url="https://www.taobao.com",
            mode="home",
            title="淘宝",
            interactive=interactive,
            text_preview="",
            last_tool="navigate",
            last_args='{"url": "https://www.taobao.com"}',
            last_result="已导航",
            user_goal="搜索索尼耳机",
            current_step=2,
            max_steps=30,
        )
        assert "[button]" in ctx
        assert "[link]" in ctx
        assert "navigate" in ctx
        assert "2/30" in ctx

    def test_build_context_truncates_large_interactive_list(self):
        interactive = [
            {"tag": "button", "text": f"btn{i}", "selector_hint": f"button: btn{i}"}
            for i in range(30)
        ]
        ctx = build_context(
            url="https://www.taobao.com",
            mode="home",
            title="淘宝",
            interactive=interactive,
            text_preview="",
            last_tool="none",
            last_args="",
            last_result="init",
            user_goal="test",
            current_step=1,
            max_steps=30,
        )
        # Should only include first 20 + a note about remaining
        assert "另有 10 个" in ctx

    def test_build_context_with_empty_interactive(self):
        ctx = build_context(
            url="about:blank",
            mode="unknown",
            title="",
            interactive=[],
            text_preview="",
            last_tool="none",
            last_args="",
            last_result="init",
            user_goal="test",
            current_step=1,
            max_steps=30,
        )
        assert "about:blank" in ctx
        assert "(空)" in ctx

    def test_build_context_includes_last_step_info(self):
        ctx = build_context(
            url="https://s.taobao.com",
            mode="search_results",
            title="搜索结果",
            interactive=[],
            text_preview="索尼耳机 商品列表",
            last_tool="search_product",
            last_args='{"keyword": "索尼耳机"}',
            last_result="搜索完成，找到商品",
            user_goal="搜索索尼耳机",
            current_step=3,
            max_steps=30,
        )
        assert "search_product" in ctx
        assert "搜索完成" in ctx
        assert "3/30" in ctx
