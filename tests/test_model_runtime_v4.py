"""Focused contracts for conditional capabilities and completed responses."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import model_catalog
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.model_capabilities import (
    ModelCapabilityError,
    select_api_path,
    validate_api_path,
)
from custom_components.extended_openai_conversation_responses.non_streaming import (
    completed_chat_chunks,
    completed_responses_events,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from custom_components.extended_openai_conversation_responses.usage import RequestUsage
from homeassistant.exceptions import HomeAssistantError


@pytest.fixture
def conditional_catalog():
    catalog = deepcopy(model_catalog.BUNDLED_CATALOG)
    catalog["catalog_version"] += 1
    model = next(item for item in catalog["models"] if item["id"] == "gpt-5.4")
    model["function_calling"]["chat_completions"] = {
        "support": "conditional",
        "allowed_reasoning_efforts": ["none"],
    }
    model["tools"]["function"]["chat_completions"] = {
        "support": "conditional", "requires": {"reasoning_effort": ["none"]},
    }
    model["function_calling"]["preferred_api"] = "chat_completions"
    model["auto_api"] = "chat_completions"
    model_catalog.activate_catalog(catalog)
    yield catalog
    model_catalog.activate_catalog(None)


def test_v3_migration_retains_boolean_function_semantics():
    old = deepcopy(model_catalog.BUNDLED_CATALOG)
    old["models"] = [
        deepcopy(item) for item in model_catalog.BUNDLED_CATALOG.resolved.values()
    ]
    old["schema_version"] = 3
    old["catalog_version"] = 3
    for item in [old["defaults"], *old["models"]]:
        item.pop("structured_outputs")
        item.pop("responses_web_search")
    migrated, changed = model_catalog.validate_or_migrate_catalog(old)
    assert changed is True
    assert migrated["schema_version"] == 5
    assert migrated["defaults"]["function_calling"]["responses"] is False
    assert migrated["models"][0]["function_calling"]["responses"] is True


def test_conditional_function_support_and_transition_safety(conditional_catalog):
    assert select_api_path("gpt-5.4", "auto", True, "none") == "chat_completions"
    assert select_api_path("gpt-5.4", "auto", True, "high") == "responses"
    assert (
        validate_api_path("gpt-5.4", "chat_completions", True, "none")
        == "chat_completions"
    )
    with pytest.raises(ModelCapabilityError, match="function/tool calling"):
        validate_api_path("gpt-5.4", "chat_completions", True, "high")
    # Provider-discovered conditions may narrow one API path while preserving
    # model-wide capability choices.
    model_catalog.validate_catalog_transition(None, conditional_catalog)


def test_runtime_and_preview_resolve_effort_before_api(conditional_catalog):
    options = {
        "chat_model": "gpt-5.4",
        "api_mode": "auto",
        "reasoning_effort": "high",
        "max_tokens": 1000,
    }
    preview = build_provider_request_snapshot(options, {}, tools_required=True)
    runtime = build_provider_request_snapshot(options, {}, tools_required=True)
    assert preview == runtime
    assert runtime.api_mode == "responses"
    assert runtime.api_kwargs["reasoning"] == {"effort": "high"}
    options["api_mode"] = "chat_completions"
    with pytest.raises(HomeAssistantError, match="function/tool calling"):
        build_provider_request_snapshot(options, {}, tools_required=True)


def test_web_search_checks_selected_model_without_another_lookup(
    conditional_catalog, monkeypatch
):
    model = next(
        item for item in conditional_catalog["models"] if item["id"] == "gpt-5.4"
    )
    model["responses_web_search"] = False
    model["tools"]["web_search"]["responses"] = {"support": "never"}
    model_catalog.activate_catalog(conditional_catalog)
    from custom_components.extended_openai_conversation_responses import request

    calls = 0
    original = request.get_model_capabilities

    def counted(model_id):
        nonlocal calls
        calls += 1
        return original(model_id)

    monkeypatch.setattr(request, "get_model_capabilities", counted)
    with pytest.raises(
        HomeAssistantError, match="does not support Web Search"
    ):
        build_provider_request_snapshot(
            {"chat_model": "gpt-5.4", "web_search": True, "api_mode": "responses"},
            {"api_provider": "openai"},
        )
    assert calls == 1


class _ChatLog:
    def async_trace(self, _value):
        pass


@pytest.mark.asyncio
async def test_completed_responses_reuse_tool_loop_and_usage_transformer():
    output = SimpleNamespace(
        type="message",
        content=[SimpleNamespace(type="output_text", text="Done", annotations=[])],
    )
    tool = SimpleNamespace(
        type="function_call", call_id="call-1", name="check", arguments='{"value":1}'
    )
    response = SimpleNamespace(status="completed", output=[output, tool], usage=None)
    entity = SimpleNamespace(subentry=SimpleNamespace(data={}))
    deltas = [
        delta
        async for delta in ExtendedOpenAIBaseLLMEntity._transform_responses_stream(
            entity, _ChatLog(), completed_responses_events(response), RequestUsage()
        )
    ]
    assert {"content": "Done"} in deltas
    assert any(
        delta.get("tool_calls", [None])[0].tool_name == "check"
        for delta in deltas
        if delta.get("tool_calls")
    )


@pytest.mark.asyncio
async def test_completed_chat_response_preserves_tool_calls():
    tool = SimpleNamespace(
        id="call-1", function=SimpleNamespace(name="check", arguments="{}")
    )
    message = SimpleNamespace(content=None, refusal=None, tool_calls=[tool])
    choice = SimpleNamespace(message=message, finish_reason="tool_calls")
    response = SimpleNamespace(choices=[choice], usage=None)
    entity = SimpleNamespace(subentry=SimpleNamespace(data={}))
    deltas = [
        delta
        async for delta in ExtendedOpenAIBaseLLMEntity._transform_chat_stream(
            entity, _ChatLog(), completed_chat_chunks(response), RequestUsage()
        )
    ]
    assert any(delta.get("tool_calls") for delta in deltas)
