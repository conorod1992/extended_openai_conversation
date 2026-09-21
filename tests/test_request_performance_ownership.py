"""Behavior and work-count contracts for explicit request performance ownership."""

from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as agent_module,
    debug,
    entity as entity_module,
    input_footprint,
    request as request_module,
    request_static_cache as cache,
    skill_runtime_availability,
)
from custom_components.extended_openai_conversation_responses.context_usage_hardening import (
    estimate_prepared_request,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.request import (
    format_function_tools,
)
from custom_components.extended_openai_conversation_responses.usage import RequestUsage
from tests.test_conversation_provider_loop_contracts import (
    _final_stream,
    _provider_fixture,
    _tool,
)


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_fresh_schemas_reuse_formatting_and_isolate_results(mode):
    source = [_tool("lookup")]
    formatter = Mock(wraps=format_function_tools)
    expected = format_function_tools(source, mode)
    with cache.formatted_tool_cache():
        for _ in range(10):
            result = cache.cached_format_tools(deepcopy(source), mode, formatter)
            assert result == expected
            result[0]["mutated"] = {"nested": True}
        assert formatter.call_count == 1
        changed = deepcopy(source)
        changed[0]["spec"]["description"] = "Changed live schema"
        assert cache.cached_format_tools(changed, mode, formatter) != expected
        assert formatter.call_count == 2
    with cache.formatted_tool_cache():
        assert cache.cached_format_tools(source, mode, formatter) == expected
    assert formatter.call_count == 3
    assert source == [_tool("lookup")]


async def test_concurrent_requests_have_independent_caches():
    formatter = Mock(wraps=format_function_tools)

    async def request():
        with cache.formatted_tool_cache():
            first = cache.cached_format_tools([_tool("lookup")], "responses", formatter)
            await asyncio.sleep(0)
            second = cache.cached_format_tools(
                [_tool("lookup")], "responses", formatter
            )
            assert first == second
            assert first is not second

    await asyncio.gather(request(), request())
    assert formatter.call_count == 2
    assert cache._FORMATTED_TOOLS.get() is None


def test_provider_request_resolves_model_capabilities_once(monkeypatch):
    """One request-preparation pass reuses one isolated capability snapshot."""
    calls = 0
    original = request_module.get_model_capabilities

    def resolve(model):
        nonlocal calls
        calls += 1
        return original(model)

    monkeypatch.setattr(request_module, "get_model_capabilities", resolve)
    snapshot = request_module.build_provider_request_snapshot(
        {"chat_model": "gpt-4.1-mini"},
        {},
        tools_required=False,
    )

    assert snapshot.api_kwargs["model"] == "gpt-4.1-mini"
    assert calls == 1


def test_tool_format_cache_miss_keeps_cache_isolated_without_second_copy():
    """The first formatted result is fresh while the cached baseline stays private."""
    source = [_tool("lookup")]
    formatted = format_function_tools(source, "responses")
    formatter = Mock(return_value=formatted)

    with cache.formatted_tool_cache():
        first = cache.cached_format_tools(source, "responses", formatter)
        assert first is formatted
        first[0]["mutated"] = True
        second = cache.cached_format_tools(source, "responses", formatter)

    assert "mutated" not in second[0]
    assert formatter.call_count == 1


def test_request_llm_context_reuses_active_instance_without_rebuilding():
    calls = 0
    fallback = object()

    def as_llm_context(_domain):
        nonlocal calls
        calls += 1
        return fallback

    user_input = SimpleNamespace(as_llm_context=as_llm_context)
    assert agent_module._request_llm_context(user_input) is fallback
    assert calls == 1

    active = object()
    token = agent_module._ACTIVE_LLM_CONTEXT.set(active)
    try:
        assert agent_module._request_llm_context(user_input) is active
        assert agent_module._request_llm_context(user_input) is active
        assert calls == 1
    finally:
        agent_module._ACTIVE_LLM_CONTEXT.reset(token)


async def test_request_reuses_function_config_across_provider_rounds(monkeypatch):
    agent = object.__new__(ExtendedOpenAIAgentEntity)
    agent.hass = SimpleNamespace()
    agent.subentry = SimpleNamespace(
        data={"functions": [], "skills": [], "function_groups": []},
        subentry_id="agent",
    )
    agent._function_groups_runtime = None
    agent._temporary_memory = None
    agent._knowledge = None
    agent._archive = None
    agent._effective_guest_policy = lambda: GuestCapabilityPolicy.unrestricted()
    agent._current_memory_scope_id = lambda: None
    configured = Mock(return_value=[_tool("lookup")])
    agent._configured_function_tools_from_data = configured
    validate = Mock(wraps=agent_module.validate_function_groups)
    monkeypatch.setattr(agent_module, "validate_function_groups", validate)

    async def handle(*_args, **_kwargs):
        first = agent._get_function_tools()
        second = agent._get_function_tools()
        assert [tool["spec"]["name"] for tool in first] == ["lookup"]
        assert [tool["spec"]["name"] for tool in second] == ["lookup"]
        return "done"

    agent._async_handle_message = AsyncMock(side_effect=handle)
    user_input = SimpleNamespace(as_llm_context=lambda _domain: SimpleNamespace())

    result = await agent._async_handle_message_with_ha_tools(
        user_input, SimpleNamespace(), {}
    )

    assert result == "done"
    assert configured.call_count == 1
    assert validate.call_count == 1
    assert agent_module._ACTIVE_FUNCTION_CONFIG.get() is None


def test_provider_tool_call_partition_is_single_pass_and_stable():
    pending = [
        SimpleNamespace(tool_name="first"),
        SimpleNamespace(tool_name=entity_module.FUNCTION_GROUP_LOADER_TOOL_NAME),
        SimpleNamespace(tool_name=entity_module.CONTINUE_CONVERSATION_TOOL_NAME),
        SimpleNamespace(tool_name="second"),
        SimpleNamespace(tool_name=entity_module.CONTINUE_CONVERSATION_TOOL_NAME),
    ]

    ordinary, loader, control = entity_module._partition_provider_tool_calls(
        pending, integration_loader_seen=True
    )

    assert [call.tool_name for call in ordinary] == ["first", "second"]
    assert [call.tool_name for call in loader] == [
        entity_module.FUNCTION_GROUP_LOADER_TOOL_NAME
    ]
    assert [call.tool_name for call in control] == [
        entity_module.CONTINUE_CONVERSATION_TOOL_NAME,
        entity_module.CONTINUE_CONVERSATION_TOOL_NAME,
    ]
    assert pending[0].tool_name == "first"


def test_effective_assembly_retrieves_and_validates_once(monkeypatch):
    agent = object.__new__(ExtendedOpenAIAgentEntity)
    agent.subentry = SimpleNamespace(
        data={"functions": [], "skills": [], "function_groups": []}
    )
    agent._function_groups_runtime = None
    agent._temporary_memory = None
    agent._knowledge = None
    agent._archive = None
    agent._get_configured_function_tools = Mock(return_value=[_tool("lookup")])
    agent._effective_guest_policy = lambda: GuestCapabilityPolicy.unrestricted()
    agent._current_memory_scope_id = lambda: None
    validate = Mock(wraps=agent_module.validate_function_groups)
    monkeypatch.setattr(agent_module, "validate_function_groups", validate)
    redundant = Mock(side_effect=AssertionError("must use already validated groups"))
    monkeypatch.setattr(
        skill_runtime_availability, "validate_function_groups", redundant
    )
    result = agent._get_function_tools()
    assert [tool["spec"]["name"] for tool in result] == ["lookup"]
    assert agent._get_configured_function_tools.call_count == 1
    assert validate.call_count == 1
    redundant.assert_not_called()


async def test_provider_round_assembles_once_after_attachment_await(hass, monkeypatch):
    agent, client, _, _, invoke = _provider_fixture(
        hass, monkeypatch, [_final_stream("done")], tools=[_tool("old")]
    )

    async def attachments(*args):
        await asyncio.sleep(0)
        agent._get_function_tools.return_value = [_tool("current")]

    agent._async_add_attachments = attachments
    result = await invoke()
    assert result.response.speech["plain"]["speech"] == "done"
    agent._get_function_tools.assert_called_once()
    assert client.responses.create.call_args.kwargs["tools"][0]["name"] == "current"


@pytest.mark.parametrize(
    "text_type,image_type", [("text", "image_url"), ("input_text", "input_image")]
)
def test_prepared_measurement_reuses_wire_artifacts_without_mutation(
    text_type, image_type
):
    agent = SimpleNamespace(
        hass=SimpleNamespace(data={}),
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent"),
    )
    payload = [
        {"role": "system", "content": "system"},
        {
            "role": "user",
            "content": [
                {"type": text_type, "text": "hello"},
                {"type": image_type, "image_url": "data:private-image"},
            ],
        },
    ]
    tools = format_function_tools([_tool("lookup")], "responses")
    original = deepcopy((payload, tools))
    usage = RequestUsage()
    estimate_prepared_request(agent, usage, payload, tools)
    measured = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
    ]
    expected = input_footprint.input_footprint_metrics(measured, tools)
    assert usage.input_tokens == expected["context_safety_estimate_tokens"]
    stored = agent.hass.data[input_footprint._LATEST_FOOTPRINTS][("entry", "agent")]
    assert stored["characters"] == expected["characters"]
    assert stored["attachments_excluded"] is True
    assert "private-image" not in str(stored)
    assert (payload, tools) == original


def test_no_migrated_installer_reassigns_owned_methods():
    root = Path(entity_module.__file__).parent
    names = {
        "install_request_static_caching",
        "install_guest_policy_fast_path",
        "install_deferred_context_summary",
        "install_debug_instrumentation",
        "install_input_footprint",
        "install_hot_path_cleanup",
        "install_configurable_regex_isolation",
        "install_skill_runtime_availability",
        "install_exposed_attribute_runtime",
        "install_context_usage_hardening",
        "install_payload_latency_diagnostics",
    }
    owned = {
        "_async_handle_message",
        "_async_handle_chat_log",
        "_get_function_tools",
        "_load_function_groups",
        "_effective_guest_policy",
        "_format_tools",
        "_truncate_message_history",
        "_transform_chat_stream",
        "_transform_responses_stream",
        "render_effective_prompt",
        "_default_exposed_entities_context",
    }
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name not in names, (path.name, node.name)
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    if isinstance(target, ast.Attribute):
                        assert target.attr not in owned, (path.name, target.attr)


async def test_owned_model_timing_fires_on_error(monkeypatch):
    trace = debug.DebugTrace(
        debug_id="debug",
        entry_id="entry",
        subentry_id="agent",
        started_at="now",
        user_input={},
        incoming_conversation_id=None,
    )

    async def fail(*args, **kwargs):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(ExtendedOpenAIAgentEntity, "_async_generate_message", fail)
    token = debug._ACTIVE_DEBUG_TRACE.set(trace)
    try:
        with pytest.raises(RuntimeError, match="provider failed"):
            await ExtendedOpenAIAgentEntity._async_handle_message(
                SimpleNamespace(_usage=None), None, None
            )
    finally:
        debug._ACTIVE_DEBUG_TRACE.reset(token)
    assert "model_path_total" in trace.phases_ms


def test_owned_preparation_records_payload_latency_diagnostics(monkeypatch):
    from custom_components.extended_openai_conversation_responses.prompt import (
        EffectivePrompt,
        PromptSection,
    )

    trace = debug.DebugTrace(
        debug_id="debug",
        entry_id="entry",
        subentry_id="agent",
        started_at="now",
        user_input={},
        incoming_conversation_id=None,
    )
    agent = object.__new__(ExtendedOpenAIAgentEntity)
    agent.hass = SimpleNamespace(data={})
    agent.entry = SimpleNamespace(entry_id="entry")
    agent.subentry = SimpleNamespace(
        subentry_id="agent", data={"skills": [], "function_groups": []}
    )
    agent._knowledge = None
    agent._temporary_memory = None
    agent._archive = None
    agent._function_groups_runtime = None
    agent._effective_guest_policy = lambda: GuestCapabilityPolicy.unrestricted()
    agent._current_memory_scope_id = lambda: None
    agent._get_configured_function_tools = lambda: [_tool("lookup")]
    prompt = EffectivePrompt(
        text="owned prompt",
        sections=(
            PromptSection(
                key="base", label="Base", text="owned prompt", volatility="stable"
            ),
        ),
    )
    monkeypatch.setattr(
        agent_module, "render_effective_prompt", lambda *args, **kwargs: prompt
    )
    monkeypatch.setattr(
        agent_module, "get_exposed_entities", lambda hass: [{"entity_id": "light.one"}]
    )
    token = debug._ACTIVE_DEBUG_TRACE.set(trace)
    try:
        entities = agent._get_exposed_entities()
        assert (
            agent._build_system_prompt(entities, SimpleNamespace(device_id=None), None)
            == prompt.text
        )
        assert agent._get_function_tools()[0]["spec"]["name"] == "lookup"
        result = trace.as_dict()
    finally:
        debug._ACTIVE_DEBUG_TRACE.reset(token)
    preparation = result["payload_latency_diagnostics"]["preparation"]
    for phase in (
        "exposed_entity_context",
        "prompt_render_core",
        "function_tool_assembly",
    ):
        assert preparation[phase]["calls"] == 1
    assert result["prompt_metrics"]["sections"][0]["key"] == "base"
    assert result["system_prompt"] == "owned prompt"
    assert "system_prompt_render" in result["phases_ms"]
