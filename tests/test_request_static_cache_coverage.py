"""Focused residual coverage for request-scoped static caching wrappers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation,
    entity,
    prompt,
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


@pytest.fixture(autouse=True)
def restore_static_cache_wrappers():
    agent_type = conversation.ExtendedOpenAIAgentEntity
    originals = {
        "process": agent_type._async_process,
        "format_tools": entity._format_tools,
        "render_template": prompt._render_template,
        "get_function_tools": agent_type._get_function_tools,
        "load_function_groups_method": agent_type._load_function_groups,
        "assemble": conversation.assemble_function_tools,
        "load_groups": conversation.load_function_groups,
        "installed": request_static_cache._INSTALLED,
    }
    request_static_cache._INSTALLED = False
    try:
        yield
    finally:
        agent_type._async_process = originals["process"]
        entity._format_tools = originals["format_tools"]
        prompt._render_template = originals["render_template"]
        agent_type._get_function_tools = originals["get_function_tools"]
        agent_type._load_function_groups = originals["load_function_groups_method"]
        conversation.assemble_function_tools = originals["assemble"]
        conversation.load_function_groups = originals["load_groups"]
        request_static_cache._INSTALLED = originals["installed"]


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


@pytest.mark.asyncio
async def test_process_wrapper_uses_fresh_request_cache_and_restores_outer_context(
    monkeypatch,
) -> None:
    agent_type = conversation.ExtendedOpenAIAgentEntity
    seen: list[object] = []

    async def original_process(_self, user_input):
        seen.append(request_static_cache._FORMATTED_TOOLS.get())
        request_static_cache._FORMATTED_TOOLS.get()["inside"] = user_input
        return "ok"

    monkeypatch.setattr(agent_type, "_async_process", original_process)
    request_static_cache.install_request_static_caching()

    outer = {"outer": "preserved"}
    token = request_static_cache._FORMATTED_TOOLS.set(outer)
    try:
        result = await agent_type._async_process(SimpleNamespace(), "hello")
        assert request_static_cache._FORMATTED_TOOLS.get() is outer
    finally:
        request_static_cache._FORMATTED_TOOLS.reset(token)

    assert result == "ok"
    assert seen == [{"inside": "hello"}]
    assert outer == {"outer": "preserved"}


@pytest.mark.asyncio
async def test_process_wrapper_restores_cache_when_processing_raises(monkeypatch) -> None:
    agent_type = conversation.ExtendedOpenAIAgentEntity

    async def original_process(_self, _user_input):
        assert request_static_cache._FORMATTED_TOOLS.get() == {}
        raise RuntimeError("boom")

    monkeypatch.setattr(agent_type, "_async_process", original_process)
    request_static_cache.install_request_static_caching()

    outer = {"outer": "still-here"}
    token = request_static_cache._FORMATTED_TOOLS.set(outer)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            await agent_type._async_process(SimpleNamespace(), "hello")
        assert request_static_cache._FORMATTED_TOOLS.get() is outer
    finally:
        request_static_cache._FORMATTED_TOOLS.reset(token)


def test_render_template_fast_path_and_fallback_preserve_behavior(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    def original_render(
        _hass,
        raw,
        *,
        exposed_entities,
        current_device_id,
        user_input,
        skills,
    ):
        calls.append((raw, current_device_id))
        return f"original:{raw}:{len(exposed_entities)}:{len(skills)}"

    monkeypatch.setattr(prompt, "_DEFAULT_EXPOSED_ENTITIES_CONTEXT", "MAINTAINED")
    monkeypatch.setattr(prompt, "_render_template", original_render)
    monkeypatch.setattr(
        request_static_cache,
        "render_maintained_entity_context",
        lambda _hass, entities: f"maintained:{len(entities)}",
    )
    request_static_cache.install_request_static_caching()

    maintained = prompt._render_template(
        object(),
        "MAINTAINED",
        exposed_entities=[{"entity_id": "light.one"}],
        current_device_id="device-1",
        user_input="hello",
        skills=["skill"],
    )
    custom = prompt._render_template(
        object(),
        "CUSTOM",
        exposed_entities=[{"entity_id": "light.one"}],
        current_device_id="device-2",
        user_input="hello",
        skills=["skill"],
    )

    assert maintained == "maintained:1"
    assert custom == "original:CUSTOM:1:1"
    assert calls == [("CUSTOM", "device-2")]


def test_skill_availability_context_is_scoped_for_tool_and_group_loading(monkeypatch) -> None:
    agent_type = conversation.ExtendedOpenAIAgentEntity
    seen: list[tuple[str, bool | None]] = []

    def original_tools(_self):
        seen.append(("tools", request_static_cache._SKILLS_AVAILABLE.get()))
        return ["tool"]

    def original_groups(_self, requested):
        seen.append(("groups", request_static_cache._SKILLS_AVAILABLE.get()))
        return {"requested": requested}

    monkeypatch.setattr(agent_type, "_get_function_tools", original_tools)
    monkeypatch.setattr(agent_type, "_load_function_groups", original_groups)
    request_static_cache.install_request_static_caching()

    agent = SimpleNamespace(_get_enabled_skills=lambda: [])
    assert agent_type._get_function_tools(agent) == ["tool"]
    assert request_static_cache._SKILLS_AVAILABLE.get() is None

    agent._get_enabled_skills = lambda: ["one"]
    assert agent_type._load_function_groups(agent, ["group-a"]) == {
        "requested": ["group-a"]
    }
    assert request_static_cache._SKILLS_AVAILABLE.get() is None
    assert seen == [("tools", False), ("groups", True)]


def test_assembly_and_group_loading_project_skill_loader_only_when_unavailable(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def original_assemble(
        configured_tools,
        groups,
        loaded_group_ids,
        *,
        function_tools_supported=True,
        group_loader_supported=True,
        tool_available=None,
    ):
        captured["assembled"] = configured_tools
        return ["assembled"]

    def original_load(
        session,
        requested,
        groups,
        configured_tools=None,
        *,
        function_tools_supported=True,
        group_loader_supported=True,
        tool_available=None,
    ):
        captured["loaded"] = configured_tools
        return {"loaded": True}

    monkeypatch.setattr(conversation, "assemble_function_tools", original_assemble)
    monkeypatch.setattr(conversation, "load_function_groups", original_load)
    request_static_cache.install_request_static_caching()

    canonical = _skill_loader()
    ordinary = {"spec": {"name": "ordinary"}, "function": {"type": "template"}}
    token = request_static_cache._SKILLS_AVAILABLE.set(False)
    try:
        assert conversation.assemble_function_tools(
            [canonical, ordinary], [], set()
        ) == ["assembled"]
        assert conversation.load_function_groups(
            object(), [], [], [canonical, ordinary]
        ) == {"loaded": True}
    finally:
        request_static_cache._SKILLS_AVAILABLE.reset(token)

    assert captured["assembled"] == [ordinary]
    assert captured["loaded"] == [ordinary]


def test_group_loading_preserves_none_configured_tools(monkeypatch) -> None:
    captured: list[object] = []

    def original_load(
        _session,
        _requested,
        _groups,
        configured_tools=None,
        **_kwargs,
    ):
        captured.append(configured_tools)
        return {"ok": True}

    monkeypatch.setattr(conversation, "load_function_groups", original_load)
    request_static_cache.install_request_static_caching()

    assert conversation.load_function_groups(object(), [], [], None) == {"ok": True}
    assert captured == [None]


def test_install_request_static_caching_is_idempotent() -> None:
    request_static_cache.install_request_static_caching()
    first_process = conversation.ExtendedOpenAIAgentEntity._async_process
    first_formatter = entity._format_tools
    first_renderer = prompt._render_template

    request_static_cache.install_request_static_caching()

    assert conversation.ExtendedOpenAIAgentEntity._async_process is first_process
    assert entity._format_tools is first_formatter
    assert prompt._render_template is first_renderer
