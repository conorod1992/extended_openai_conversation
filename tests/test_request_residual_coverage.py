"""Residual branch coverage for provider request assembly."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import request
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_GUEST_MODE_ENABLED,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MAX_TOKENS,
    CONF_MEMORY_ENABLED,
    CONF_REASONING_EFFORT,
    CONF_TEMPERATURE,
    CONF_WEB_SEARCH,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from homeassistant.exceptions import HomeAssistantError


def test_configured_tools_required_handles_persisted_string_forms_and_features() -> None:
    for value in ("", "   ", "[]", "null", "~"):
        assert request._configured_tools_required({CONF_FUNCTION_TOOLS: value}) is False

    assert request._configured_tools_required({CONF_FUNCTION_TOOLS: "[tool]"}) is True
    assert request._configured_tools_required(
        {CONF_FUNCTION_TOOLS: [{"spec": {}}]}
    ) is True
    assert request._configured_tools_required({CONF_FUNCTION_GROUPS: ["group"]}) is True
    assert request._configured_tools_required({CONF_MEMORY_ENABLED: True}) is True
    assert request._configured_tools_required({CONF_KNOWLEDGE_ENABLED: True}) is True
    assert request._configured_tools_required({CONF_WEB_SEARCH: True}) is False


def test_sampling_value_omits_unconfigured_or_disallowed_values(monkeypatch) -> None:
    monkeypatch.setattr(request, "sampling_value_is_configured", lambda *_args: False)
    assert request._sampling_value({}, "model", CONF_TEMPERATURE, None) is None

    monkeypatch.setattr(request, "sampling_value_is_configured", lambda *_args: True)
    monkeypatch.setattr(request, "parameter_is_allowed", lambda *_args: False)
    assert (
        request._sampling_value(
            {CONF_TEMPERATURE: 0.4}, "model", CONF_TEMPERATURE, None
        )
        is None
    )

    monkeypatch.setattr(request, "parameter_is_allowed", lambda *_args: True)
    assert (
        request._sampling_value(
            {CONF_TEMPERATURE: 0.4}, "model", CONF_TEMPERATURE, None
        )
        == 0.4
    )


def test_provider_snapshot_covers_token_migration_reasoning_and_chat_paths(
    monkeypatch,
) -> None:
    request._LEGACY_TOKEN_MIGRATION_LOGGED.clear()
    monkeypatch.setattr(request, "select_api_path", lambda *_args: API_MODE_RESPONSES)
    monkeypatch.setattr(
        request,
        "get_model_capabilities",
        lambda _model: {
            "reasoning": {"supported": True},
            "service_tier": True,
            "streaming": True,
            "structured_outputs": True,
        },
    )
    monkeypatch.setattr(
        request,
        "normalize_output_token_limit",
        lambda *_args: ("max_output_tokens", 123),
    )
    monkeypatch.setattr(
        request, "recommended_reasoning_effort", lambda _model: "high"
    )
    monkeypatch.setattr(request, "validate_reasoning_effort", lambda *_args: "high")
    monkeypatch.setattr(request, "sampling_value_is_configured", lambda *_args: False)
    monkeypatch.setattr(request, "build_web_search_tool", lambda *_args: None)

    snapshot = request.build_provider_request_snapshot(
        {CONF_MAX_TOKENS: 123}, {}, tools_required=False
    )
    assert snapshot.api_mode == API_MODE_RESPONSES
    assert snapshot.api_kwargs["max_output_tokens"] == 123
    assert snapshot.api_kwargs["reasoning"] == {"effort": "high"}
    assert snapshot.api_kwargs["include"] == ["reasoning.encrypted_content"]
    assert snapshot.api_kwargs["store"] is False
    assert request.DEFAULT_CHAT_MODEL in request._LEGACY_TOKEN_MIGRATION_LOGGED

    monkeypatch.setattr(
        request, "select_api_path", lambda *_args: API_MODE_CHAT_COMPLETIONS
    )
    snapshot = request.build_provider_request_snapshot(
        {CONF_REASONING_EFFORT: "high"}, {}, tools_required=False
    )
    assert snapshot.api_kwargs["reasoning_effort"] == "high"
    assert snapshot.api_kwargs["stream_options"] == {"include_usage": True}


def test_provider_snapshot_ignores_stale_effort_for_non_reasoning_model(
    monkeypatch,
) -> None:
    monkeypatch.setattr(request, "select_api_path", lambda *_args: API_MODE_RESPONSES)
    monkeypatch.setattr(
        request,
        "get_model_capabilities",
        lambda _model: {
            "reasoning": {"supported": False},
            "service_tier": False,
            "streaming": True,
            "structured_outputs": True,
        },
    )
    monkeypatch.setattr(request, "normalize_output_token_limit", lambda *_args: None)
    monkeypatch.setattr(request, "sampling_value_is_configured", lambda *_args: False)
    monkeypatch.setattr(request, "build_web_search_tool", lambda *_args: None)

    snapshot = request.build_provider_request_snapshot(
        {CONF_REASONING_EFFORT: "high"}, {}, tools_required=False
    )
    assert "reasoning" not in snapshot.api_kwargs
    assert "reasoning_effort" not in snapshot.api_kwargs


def test_provider_snapshot_wraps_model_capability_errors(monkeypatch) -> None:
    def fail(*_args):
        raise request.ModelCapabilityError("bad model capability")

    monkeypatch.setattr(request, "select_api_path", fail)
    with pytest.raises(HomeAssistantError, match="bad model capability"):
        request.build_provider_request_snapshot({}, {}, tools_required=False)


def test_assemble_tools_covers_reserved_names_guest_filtering_and_schema_guard(
    monkeypatch,
) -> None:
    monkeypatch.setattr(request, "conversation_lifecycle_active", lambda: True)
    monkeypatch.setattr(
        request,
        "resolve_effective_capabilities",
        lambda *_args, **_kwargs: SimpleNamespace(persistent_memory=True),
    )

    with pytest.raises(HomeAssistantError, match="lifecycle"):
        request.assemble_integration_function_tools(
            {},
            {request.START_FRESH_CONVERSATION_TOOL_NAME},
            memory_scope_available=True,
            temporary_scope_available=False,
            knowledge_available=False,
        )

    with pytest.raises(HomeAssistantError, match="persistent-memory"):
        request.assemble_integration_function_tools(
            {},
            {next(iter(request.MEMORY_TOOL_NAMES))},
            memory_scope_available=True,
            temporary_scope_available=False,
            knowledge_available=False,
        )

    memory_tools = [
        {
            "spec": {
                "name": "memory_search",
                "parameters": {"properties": {"query": {"type": "string", "minLength": 1}}},
            }
        },
        {
            "spec": {
                "name": "memory_add",
                "parameters": {"properties": {"query": "not-a-schema"}},
            }
        },
        {
            "spec": {
                "name": "memory_delete",
                "parameters": {"properties": {}},
            }
        },
    ]
    monkeypatch.setattr(request, "memory_tools", lambda: memory_tools)
    monkeypatch.setattr(request, "temporary_memory_tools", lambda: [])
    monkeypatch.setattr(request, "archive_tools", lambda: [])
    monkeypatch.setattr(request, "knowledge_tools", lambda: [])

    policy = GuestCapabilityPolicy(
        guest_active=True,
        shared_memory_read=True,
        shared_memory_write=False,
    )
    result = request.assemble_integration_function_tools(
        {},
        set(),
        memory_scope_available=True,
        temporary_scope_available=False,
        knowledge_available=False,
        guest_policy=policy,
    )
    names = [tool.get("spec", {}).get("name") for tool in result]
    assert request.START_FRESH_CONVERSATION_TOOL_NAME in names
    assert "memory_search" in names
    assert "memory_add" not in names
    search_tool = next(
        tool for tool in result if tool.get("spec", {}).get("name") == "memory_search"
    )
    query = search_tool["spec"]["parameters"]["properties"]["query"]
    assert query["minLength"] == 1


def test_assemble_tools_covers_temporary_conflict(monkeypatch) -> None:
    monkeypatch.setattr(request, "conversation_lifecycle_active", lambda: False)
    monkeypatch.setattr(
        request,
        "resolve_effective_capabilities",
        lambda *_args, **_kwargs: SimpleNamespace(persistent_memory=False),
    )

    with pytest.raises(HomeAssistantError, match="temporary-memory"):
        request.assemble_integration_function_tools(
            {},
            {next(iter(request.TEMPORARY_MEMORY_TOOL_NAMES))},
            memory_scope_available=False,
            temporary_scope_available=True,
            knowledge_available=False,
        )


def test_assemble_tools_covers_archive_filter_knowledge_and_guest_conflicts(
    monkeypatch,
) -> None:
    monkeypatch.setattr(request, "conversation_lifecycle_active", lambda: False)
    monkeypatch.setattr(
        request,
        "resolve_effective_capabilities",
        lambda *_args, **_kwargs: SimpleNamespace(persistent_memory=False),
    )
    monkeypatch.setattr(request, "temporary_memory_tools", lambda: [])
    monkeypatch.setattr(
        request,
        "archive_tools",
        lambda: [
            {
                "spec": {"name": "conversation_search"},
                "function": {"operation": "search"},
            },
            {
                "spec": {"name": "archive_list"},
                "function": {"operation": "list"},
            },
        ],
    )
    monkeypatch.setattr(
        request,
        "knowledge_tools",
        lambda: [{"spec": {"name": "knowledge_search"}}],
    )

    options = {
        CONF_ARCHIVE_ENABLED: True,
        CONF_ARCHIVE_MODEL_SEARCH_ENABLED: False,
    }
    result = request.assemble_integration_function_tools(
        options,
        set(),
        memory_scope_available=False,
        temporary_scope_available=False,
        knowledge_available=True,
    )
    names = [tool.get("spec", {}).get("name") for tool in result]
    assert "conversation_search" not in names
    assert "archive_list" in names
    assert "knowledge_search" in names

    with pytest.raises(HomeAssistantError, match="Knowledge Library"):
        request.assemble_integration_function_tools(
            {},
            {next(iter(request.KNOWLEDGE_TOOL_NAMES))},
            memory_scope_available=False,
            temporary_scope_available=False,
            knowledge_available=True,
        )

    with pytest.raises(HomeAssistantError, match="Guest Mode"):
        request.assemble_integration_function_tools(
            {CONF_GUEST_MODE_ENABLED: True},
            {"guest_mode_restrict"},
            memory_scope_available=False,
            temporary_scope_available=False,
            knowledge_available=False,
        )
