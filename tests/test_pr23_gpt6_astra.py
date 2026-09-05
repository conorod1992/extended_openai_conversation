"""Regression coverage for GPT-6 Astra and composed structured-output schemas."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_AUTO,
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    DEFAULT_AI_TASK_OPTIONS,
)
from custom_components.extended_openai_conversation_responses.entity import _adjust_schema
from custom_components.extended_openai_conversation_responses.helpers import (
    get_api_mode,
    get_model_config,
    get_token_param_for_model,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)


MODEL = "gpt-6-astra"


def test_gpt6_astra_uses_reasoning_model_parameter_profile() -> None:
    """Astra should omit sampling controls and use completion-token semantics."""
    config = get_model_config(MODEL)

    assert config == {
        "supports_top_p": False,
        "supports_temperature": False,
        "supports_max_tokens": False,
        "supports_max_completion_tokens": True,
        "supports_reasoning_effort": True,
        "supports_service_tier": True,
    }
    assert get_token_param_for_model(MODEL) == "max_completion_tokens"


@pytest.mark.parametrize(
    ("configured_mode", "expected"),
    [
        (API_MODE_AUTO, API_MODE_RESPONSES),
        (API_MODE_CHAT_COMPLETIONS, API_MODE_CHAT_COMPLETIONS),
        (API_MODE_RESPONSES, API_MODE_RESPONSES),
    ],
)
def test_gpt6_astra_api_mode_selection(configured_mode: str, expected: str) -> None:
    """Auto should prefer Responses without overriding an explicit API choice."""
    assert get_api_mode(configured_mode, MODEL) == expected


@pytest.mark.parametrize(
    "base_options",
    [
        {CONF_API_MODE: API_MODE_AUTO},
        DEFAULT_AI_TASK_OPTIONS,
    ],
    ids=["conversation", "ai-task"],
)
def test_gpt6_astra_auto_payloads_use_responses_fields(
    base_options: dict[str, object],
) -> None:
    """Conversation and AI Task options should build the same compatible payload."""
    options = {
        **base_options,
        CONF_CHAT_MODEL: MODEL,
        CONF_API_MODE: API_MODE_AUTO,
        CONF_MAX_TOKENS: 1234,
        CONF_TEMPERATURE: 0.2,
        CONF_TOP_P: 0.7,
    }

    snapshot = build_provider_request_snapshot(options, {})
    kwargs = snapshot.api_kwargs

    assert snapshot.api_mode == API_MODE_RESPONSES
    assert kwargs["model"] == MODEL
    assert kwargs["max_output_tokens"] == 1234
    assert kwargs["store"] is False
    assert "max_completion_tokens" not in kwargs
    assert "max_tokens" not in kwargs
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs


def test_gpt6_astra_chat_completions_uses_max_completion_tokens() -> None:
    """An explicit Chat Completions choice should use Astra's compatible token field."""
    snapshot = build_provider_request_snapshot(
        {
            CONF_CHAT_MODEL: MODEL,
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_MAX_TOKENS: 640,
            CONF_TEMPERATURE: 0.2,
            CONF_TOP_P: 0.7,
        },
        {},
    )
    kwargs = snapshot.api_kwargs

    assert snapshot.api_mode == API_MODE_CHAT_COMPLETIONS
    assert kwargs["max_completion_tokens"] == 640
    assert "max_output_tokens" not in kwargs
    assert "max_tokens" not in kwargs
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs


def test_adjust_schema_handles_compositions_and_nested_array_items() -> None:
    """Composition nodes should be traversed without assuming every node has type."""
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "choice": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "integer"},
                ]
            },
            "entries": {
                "type": "array",
                "items": {
                    "oneOf": [
                        {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                        },
                        {
                            "allOf": [
                                {
                                    "type": "object",
                                    "properties": {"count": {"type": "integer"}},
                                }
                            ]
                        },
                    ]
                },
            },
            "composed": {
                "allOf": [
                    {
                        "type": "object",
                        "properties": {"flag": {"type": "boolean"}},
                    }
                ]
            },
        },
    }

    _adjust_schema(schema)  # type: ignore[arg-type]

    assert schema["required"] == ["choice", "entries", "composed"]
    properties = schema["properties"]
    assert isinstance(properties, dict)

    choice = properties["choice"]
    assert choice["anyOf"][-1] == {"type": "null"}
    assert choice["anyOf"][0]["anyOf"] == [
        {"type": "string"},
        {"type": "integer"},
    ]

    entries = properties["entries"]
    assert entries["type"] == ["array", "null"]
    variants = entries["items"]["oneOf"]
    assert variants[0]["required"] == ["name"]
    assert variants[0]["properties"]["name"]["type"] == ["string", "null"]
    all_of_object = variants[1]["allOf"][0]
    assert all_of_object["required"] == ["count"]
    assert all_of_object["properties"]["count"]["type"] == ["integer", "null"]

    composed = properties["composed"]
    assert composed["anyOf"][-1] == {"type": "null"}
    composed_object = composed["anyOf"][0]["allOf"][0]
    assert composed_object["required"] == ["flag"]
    assert composed_object["properties"]["flag"]["type"] == ["boolean", "null"]

    adjusted_once = deepcopy(schema)
    _adjust_schema(schema)  # type: ignore[arg-type]
    assert schema == adjusted_once
