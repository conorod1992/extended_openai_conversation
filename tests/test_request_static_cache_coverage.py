"""Focused residual coverage for request-scoped static caching wrappers."""

from __future__ import annotations

import pytest

from custom_components.extended_openai_conversation_responses import (
    request_static_cache,
)


def _skill_loader() -> dict:
    return {
        "spec": {"name": "load_skill", "description": "Load a skill file"},
        "function": {
            "type": "read_file",
            "path": "{{extended_openai.skill_dir(name)}}/{{file}}",
        },
    }


def test_non_json_tool_schema_bypasses_request_cache() -> None:
    calls = 0
    tools = [{"spec": {"name": "demo", "invalid": {"set-value"}}}]

    def formatter(_tools, _api_mode):
        nonlocal calls
        calls += 1
        return [{"formatted": calls}]

    token = request_static_cache._FORMATTED_TOOLS.set({})
    try:
        first = request_static_cache.cached_format_tools(tools, "responses", formatter)
        second = request_static_cache.cached_format_tools(tools, "responses", formatter)
    finally:
        request_static_cache._FORMATTED_TOOLS.reset(token)

    assert first == [{"formatted": 1}]
    assert second == [{"formatted": 2}]
    assert calls == 2


async def test_request_owner_uses_fresh_cache_and_restores_outer_context(
    entry_agent,
    entry_input,
) -> None:
    seen = []

    async def process(request):
        seen.append(request_static_cache._FORMATTED_TOOLS.get())
        request_static_cache._FORMATTED_TOOLS.get()["inside"] = request.text
        return "ok"

    entry_agent._async_process_with_continuity = process
    outer = {"outer": "preserved"}
    token = request_static_cache._FORMATTED_TOOLS.set(outer)
    try:
        result = await entry_agent.async_process(entry_input)
        assert request_static_cache._FORMATTED_TOOLS.get() is outer
    finally:
        request_static_cache._FORMATTED_TOOLS.reset(token)
    assert result == "ok"
    assert seen == [{"inside": "hello"}]
    assert outer == {"outer": "preserved"}


async def test_request_owner_restores_cache_when_processing_raises(
    entry_agent,
    entry_input,
) -> None:
    async def process(_request):
        assert request_static_cache._FORMATTED_TOOLS.get() == {}
        raise RuntimeError("boom")

    entry_agent._async_process_with_continuity = process
    outer = {"outer": "still-here"}
    token = request_static_cache._FORMATTED_TOOLS.set(outer)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            await entry_agent.async_process(entry_input)
        assert request_static_cache._FORMATTED_TOOLS.get() is outer
    finally:
        request_static_cache._FORMATTED_TOOLS.reset(token)
