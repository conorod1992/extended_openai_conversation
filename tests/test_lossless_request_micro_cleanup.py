"""Regression tests for final lossless request-path micro-cleanups."""

from __future__ import annotations

from typing import Any

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
)
from custom_components.extended_openai_conversation_responses.model_payload import (
    prepare_model_function_tools,
)
from custom_components.extended_openai_conversation_responses.request import (
    format_function_tools,
)


class _NoDeepcopyExecutionPayload:
    """Execution-only payload that must never be copied for provider formatting."""

    def __deepcopy__(self, memo: dict[int, Any]) -> Any:
        raise AssertionError("execution metadata was deep-copied")


def test_valid_tool_copies_schema_without_deepcopying_execution_metadata() -> None:
    """Provider preparation must isolate schemas without copying execution state."""
    execution_payload = _NoDeepcopyExecutionPayload()
    tool = {
        "spec": {
            "name": "custom_tool",
            "description": "User-authored tool.",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
        },
        "function": {
            "type": "custom",
            "payload": execution_payload,
        },
    }

    prepared = prepare_model_function_tools([tool])[0]

    assert prepared == tool
    assert prepared["function"] is tool["function"]
    assert prepared["spec"] is not tool["spec"]
    assert prepared["spec"]["parameters"] is not tool["spec"]["parameters"]

    prepared["spec"]["parameters"]["properties"]["value"]["type"] = "number"
    assert tool["spec"]["parameters"]["properties"]["value"]["type"] == "string"


def test_malformed_tool_keeps_historical_full_copy_fallback() -> None:
    """Malformed tool handling retains the previous deep-copy isolation semantics."""
    tool = {
        "spec": {"description": "Missing required name."},
        "function": {"type": "custom", "nested": {"value": 1}},
    }

    prepared = prepare_model_function_tools([tool])[0]

    assert prepared == tool
    assert prepared is not tool
    assert prepared["function"] is not tool["function"]
    prepared["function"]["nested"]["value"] = 2
    assert tool["function"]["nested"]["value"] == 1


def test_chat_completions_wrapper_is_exact_and_schema_only() -> None:
    """Chat Completions keeps the exact provider shape without extra wrapper copies."""
    tool = {
        "spec": {
            "name": "custom_tool",
            "description": "User-authored tool.",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "custom", "private_execution_value": "not-provider-data"},
    }

    formatted = format_function_tools([tool], API_MODE_CHAT_COMPLETIONS)

    assert formatted == [
        {
            "type": "function",
            "function": {
                "name": "custom_tool",
                "description": "User-authored tool.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert "private_execution_value" not in repr(formatted)
