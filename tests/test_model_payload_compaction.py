"""Regression tests for lossless static model-payload compaction."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    archive_tools,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    build_loader_tool,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    guest_mode_restrict_tool,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    knowledge_tools,
)
from custom_components.extended_openai_conversation_responses.memory import memory_tools
from custom_components.extended_openai_conversation_responses.model_payload import (
    KNOWLEDGE_GUIDANCE,
    PERSISTENT_MEMORY_GUIDANCE,
    prepare_model_function_tools,
)
from custom_components.extended_openai_conversation_responses.request import (
    CONTINUE_CONVERSATION_TOOL,
    START_FRESH_CONVERSATION_TOOL,
    format_function_tools,
    request_function_tools,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    temporary_memory_tools,
)


def _compact_json(value: Any) -> str:
    # Preserve key order too, including parameter declaration order.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _baseline_tools() -> list[dict[str, Any]]:
    """Exercise every built-in, dynamic catalogue entries and custom strict modes."""
    tools = [
        *memory_tools(),
        *temporary_memory_tools(),
        *knowledge_tools(),
        *archive_tools(),
        guest_mode_restrict_tool(),
        START_FRESH_CONVERSATION_TOOL,
        build_loader_tool(
            [
                {
                    "id": "travel",
                    "name": "Travel",
                    "description": "Routes and destinations.",
                },
                {
                    "id": "media",
                    "name": "Media",
                    "description": "TV, music — and films.\nKeep this catalogue detail.",
                },
            ]
        ),
    ]
    for name, strict in (
        ("custom_omitted", None),
        ("custom_true", True),
        ("custom_false", False),
    ):
        spec = {
            "name": name,
            "description": "User-authored description, verbatim.",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {
                        "type": "string",
                        "description": "User guidance.",
                        "enum": ["first", "second"],
                        "default": "second",
                    },
                    "nested": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"child": {"type": "integer", "minimum": 0}},
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["value"],
                "additionalProperties": False,
            },
        }
        if strict is not None:
            spec["strict"] = strict
        tools.append(
            {
                "spec": spec,
                "function": {"type": "native", "name": "get_user_from_user_id"},
            }
        )
    return request_function_tools(tools, conditional_continue=True)


@pytest.mark.parametrize("api_mode", [API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS])
@pytest.mark.parametrize("conditional_continue", [False, True])
def test_complete_provider_payload_matches_develop_baseline(
    api_mode, conditional_continue
) -> None:
    """Compare complete serialized payloads captured before consolidation at 624d9f07."""
    baseline = json.loads(
        (Path(__file__).parent / "fixtures" / "model_tool_payloads.json").read_text(
            encoding="utf-8"
        )
    )
    tools = _baseline_tools()
    expected = baseline[api_mode]
    if not conditional_continue:
        tools = tools[:-1]
        expected = expected[:-1]
    original = deepcopy(tools)
    assert _compact_json(format_function_tools(tools, api_mode)) == _compact_json(
        expected
    )
    assert tools == original


def _normalized_text(value: str) -> str:
    """Collapse formatting whitespace so semantic assertions ignore wrapping."""
    return " ".join(value.split())


def test_integration_definitions_are_authoritative() -> None:
    """Preparation retains every description and executable field at its source."""
    original = [
        *memory_tools(),
        *temporary_memory_tools(),
        *knowledge_tools(),
        *archive_tools(),
        guest_mode_restrict_tool(),
        CONTINUE_CONVERSATION_TOOL,
    ]
    compacted = prepare_model_function_tools(original)
    original_by_name = {tool["spec"]["name"]: tool for tool in original}
    compacted_by_name = {tool["spec"]["name"]: tool for tool in compacted}

    assert "memory_add" in original_by_name
    assert "memory_add" not in compacted_by_name
    assert "memory_upsert" in compacted_by_name
    assert set(compacted_by_name) == set(original_by_name) - {"memory_add"}

    for name, tool in compacted_by_name.items():
        original_tool = original_by_name[name]
        assert tool == original_tool


@pytest.mark.parametrize("api_mode", [API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS])
def test_colliding_custom_tools_are_not_rewritten_or_hidden(api_mode) -> None:
    """Names and properties cannot grant internal ownership to configured tools."""
    for internal in _baseline_tools():
        tool = deepcopy(internal)
        spec = tool["spec"]
        spec["description"] = "Custom tool instructions, unchanged."
        for prop in spec["parameters"]["properties"].values():
            prop["description"] = "Custom parameter guidance, unchanged."
        tool["function"] = {"type": "native", "name": "get_user_from_user_id"}
        before = deepcopy(tool)
        assert prepare_model_function_tools([tool]) == [before]
        expected = (
            {"type": "function", **spec}
            if api_mode == API_MODE_RESPONSES
            else {"type": "function", "function": spec}
        )
        assert _compact_json(format_function_tools([tool], api_mode)) == _compact_json(
            [expected]
        )
        assert tool == before


@pytest.mark.parametrize("api_mode", [API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS])
@pytest.mark.parametrize("strict", [True, False])
def test_all_explicit_strict_contracts_match_baseline(api_mode, strict) -> None:
    """Explicit values survive unchanged for every built-in and custom tool."""
    baseline = json.loads(
        (Path(__file__).parent / "fixtures" / "model_tool_payloads.json").read_text(
            encoding="utf-8"
        )
    )[api_mode]
    tools = deepcopy(_baseline_tools())
    for tool in tools:
        tool["spec"]["strict"] = strict
    for entry in baseline:
        spec = entry if api_mode == API_MODE_RESPONSES else entry["function"]
        spec["strict"] = strict
    assert _compact_json(format_function_tools(tools, api_mode)) == _compact_json(
        baseline
    )


def test_prepared_tools_isolate_nested_provider_schemas() -> None:
    """Provider consumers can mutate schemas without changing source definitions."""
    tools = deepcopy(_baseline_tools())
    tools.append({"spec": None, "function": {"type": "native", "steps": []}})
    before = deepcopy(tools)
    prepared = prepare_model_function_tools(tools)

    def assert_independent(source, copy):
        if isinstance(source, dict):
            assert copy is not source
            for key, child in source.items():
                assert_independent(child, copy[key])
        elif isinstance(source, list):
            assert copy is not source
            for child, copied_child in zip(source, copy, strict=True):
                assert_independent(child, copied_child)

    for source, copy in zip(tools[1:], prepared, strict=True):
        assert copy is not source
        assert_independent(source["spec"], copy["spec"])
        if isinstance(source["spec"], dict):
            # Execution-only metadata retains its existing reference semantics.
            assert copy.get("function") is source.get("function")
        else:
            assert_independent(source, copy)
    prepared[0]["spec"]["parameters"]["properties"]["source"]["enum"].append("new")
    assert tools == before


@pytest.mark.parametrize("api_mode", [API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS])
async def test_preview_validation_and_live_formatter_match_baseline(
    hass, monkeypatch, api_mode
) -> None:
    """Real preview assembly and the cached live boundary retain the same contract."""
    from custom_components.extended_openai_conversation_responses import (
        management_request_preview as preview,
    )
    from custom_components.extended_openai_conversation_responses.agent_config import (
        agent_config_defaults,
        validate_function_tools,
    )
    from custom_components.extended_openai_conversation_responses.entity import (
        _format_tools,
    )
    from custom_components.extended_openai_conversation_responses.request import (
        canonical_json,
        validate_runtime_readiness,
    )

    # The raw strict=True formatter probe deliberately has optional properties;
    # configured validation correctly rejects that combination.
    tools = [
        tool for tool in _baseline_tools() if tool["spec"]["name"] != "custom_true"
    ]
    custom = [
        tool for tool in tools if tool.get("function", {}).get("type") == "native"
    ]
    validated = validate_function_tools(custom)
    assert validated == custom
    options = {
        **agent_config_defaults(),
        "chat_model": "gpt-4.1",
        "api_mode": api_mode,
        "functions": validated,
        "memory_mode": "automatic",
        "temporary_memory": "balanced",
        "knowledge_enabled": True,
        "archive_enabled": True,
        "archive_model_search_enabled": True,
        "guest_mode_enabled": True,
        "continue_conversation": "conditional",
        "web_search": False,
    }
    assert validate_runtime_readiness(options, {}).api_mode == api_mode
    monkeypatch.setattr(
        preview,
        "async_get_guest_mode",
        AsyncMock(
            return_value=SimpleNamespace(
                is_active=lambda: False, status=lambda: {"currently_active": False}
            )
        ),
    )
    monkeypatch.setattr(
        preview,
        "get_loaded_temporary_memory",
        lambda *_: SimpleNamespace(async_active_snapshot=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(
        preview,
        "get_loaded_knowledge",
        lambda *_: SimpleNamespace(initialized=True, source_count=1),
    )
    monkeypatch.setattr(preview.SkillManager, "get_loaded_instance", lambda: None)
    monkeypatch.setattr(preview, "get_exposed_entities", lambda *_: [])
    monkeypatch.setattr(
        preview,
        "render_effective_prompt",
        lambda *_a, **_k: SimpleNamespace(text="Instructions", sections=[]),
    )
    result = await preview.async_preview_effective_request(
        hass,
        SimpleNamespace(entry_id="entry", data={}),
        SimpleNamespace(subentry_id="agent"),
        options,
        "admin",
    )
    wire = [
        tool
        for section in result["sections"]
        if section["key"] in {"function_tools", "integration_tools"}
        for tool in json.loads(section["content"])
    ]
    live = _format_tools(tools, api_mode)
    # The catalogue loader is tested separately; this preview has no configured groups.
    live = [
        tool
        for tool in live
        if (tool if api_mode == API_MODE_RESPONSES else tool["function"])["name"]
        != "load_function_groups"
    ]

    def key(tool):
        return (tool if api_mode == API_MODE_RESPONSES else tool["function"])["name"]

    # Preview deliberately sorts JSON keys for display; compare full structures.
    assert canonical_json(sorted(wire, key=key)) == canonical_json(
        sorted(live, key=key)
    )


def test_generic_user_tool_is_not_rewritten() -> None:
    """User-authored/configured tools are outside the compaction policy."""
    tool = {
        "spec": {
            "name": "my_custom_tool",
            "description": "Every word here belongs to the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {
                        "type": "string",
                        "description": "Keep this custom explanation exactly.",
                    }
                },
            },
        },
        "function": {"type": "native", "name": "get_user_from_user_id"},
    }

    assert prepare_model_function_tools([tool]) == [tool]


def test_function_group_loader_keeps_full_catalogue_with_less_boilerplate() -> None:
    """Every group remains described while the repeated loader prose is shorter."""
    loader = build_loader_tool(
        [
            {
                "id": "travel",
                "name": "Travel",
                "description": "Routes, destinations, and travel-time helpers.",
            },
            {
                "id": "media",
                "name": "Media",
                "description": "TV and local media helpers.",
            },
        ]
    )
    compacted = prepare_model_function_tools([loader])[0]
    description = compacted["spec"]["description"]

    assert (
        "travel: Travel — Routes, destinations, and travel-time helpers." in description
    )
    assert "media: Media — TV and local media helpers." in description
    assert "performs no action" in description
    assert len(description) < len(loader["spec"]["description"])


def test_provider_formatter_hides_legacy_memory_add() -> None:
    """The backend-compatible add operation is absent from provider tool schemas."""
    formatted = format_function_tools(memory_tools(), API_MODE_RESPONSES)
    names = {tool["name"] for tool in formatted}

    assert "memory_add" not in names
    assert "memory_upsert" in names


def test_provider_optional_integration_tools_declare_non_strict_on_both_apis() -> None:
    """Omitted strict must not coerce optional fields to required nullable values."""
    loader = build_loader_tool(
        [{"id": "testing", "name": "Testing", "description": "Test tools."}]
    )
    original = [
        *memory_tools(),
        *temporary_memory_tools(),
        *knowledge_tools(),
        *archive_tools(),
        guest_mode_restrict_tool(),
        loader,
        CONTINUE_CONVERSATION_TOOL,
    ]
    for api_mode in (API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS):
        wire = format_function_tools(original, api_mode)
        specs = {
            (entry if api_mode == API_MODE_RESPONSES else entry["function"])[
                "name"
            ]: entry if api_mode == API_MODE_RESPONSES else entry["function"]
            for entry in wire
        }
        for name in specs:
            if name == "set_continue_conversation":
                assert specs[name]["strict"] is True
            elif (
                name == "load_function_groups" or api_mode == API_MODE_CHAT_COMPLETIONS
            ):
                # The loader and non-Responses API retain their previous contracts.
                assert "strict" not in specs[name]
            else:
                assert specs[name]["strict"] is False, (api_mode, name)

        search = specs["memory_search"]["parameters"]
        assert search["required"] == ["query"]
        assert {"offset", "category", "scope", "limit"} <= set(search["properties"])
        assert {"offset", "category", "scope", "limit"}.isdisjoint(search["required"])

    # The shared internal schemas must not be mutated by provider formatting.
    assert all(
        "strict" not in item["spec"]
        for item in original
        if item["spec"]["name"] != "set_continue_conversation"
    )


def test_provider_formatter_respects_user_and_explicit_strict_contracts() -> None:
    """Do not rewrite a configured function or override an explicit strict mode."""
    user_tool = {
        "spec": {
            "name": "configured",
            "description": "User-defined function.",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
        },
        "function": {"type": "native", "name": "configured"},
    }
    explicit = memory_tools()[1]
    explicit["spec"]["strict"] = True
    for api_mode in (API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS):
        result = format_function_tools([user_tool, explicit], api_mode)
        specs = [
            item if api_mode == API_MODE_RESPONSES else item["function"]
            for item in result
        ]
        assert "strict" not in specs[0]
        assert specs[1]["strict"] is True


def test_compact_guidance_retains_memory_and_knowledge_safety_semantics() -> None:
    """The largest fixed guidance blocks keep their behavioral requirements."""
    # Historical guidance lengths; unused runtime constants were retired.
    assert len(PERSISTENT_MEMORY_GUIDANCE) < 1971 * 0.7
    assert len(KNOWLEDGE_GUIDANCE) < 1806 * 0.7

    memory_guidance = _normalized_text(PERSISTENT_MEMORY_GUIDANCE)
    knowledge_guidance = _normalized_text(KNOWLEDGE_GUIDANCE)

    for phrase in (
        "memory_upsert",
        "intent=user_request",
        "household scope",
        "Do not reject requested memories solely for credentials",
        "retention and sharing preferences",
        "Current user statements override stored facts",
        "expected_revision",
        "needs_resolution",
        "not proof of equivalence",
        "Confirm before broad deletion",
        "current-user persistence request",
        "Shared Explicit",
    ):
        assert phrase in memory_guidance

    for phrase in (
        "search rather than guess",
        "short, discriminative keywords",
        "Never invent an ID",
        "knowledge_list with no query first",
        "knowledge_get",
        "rather than inventing an answer",
    ):
        assert phrase in knowledge_guidance
