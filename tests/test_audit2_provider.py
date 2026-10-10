"""Provider identity, schema and model-change regressions."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses.ai_task import (
    parse_ai_task_structured_response,
)
from custom_components.extended_openai_conversation_responses.config_flow import (
    ExtendedOpenAIAITaskSubentryFlowHandler as Flow,
)
from custom_components.extended_openai_conversation_responses.const import (
    DEFAULT_AI_TASK_OPTIONS,
)
from custom_components.extended_openai_conversation_responses.entity import (
    _adjust_schema,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from tests.test_provider_tool_protocol import (
    _chat_chunk,
    _chat_log,
    _entity,
    _FakeStream,
)


@pytest.mark.parametrize("cumulative", [False, True])
async def test_chat_stream_accumulates_identity_fragments(hass, cumulative):
    entity = _entity(hass, [])

    def delta(call_id, name, arguments):
        return SimpleNamespace(
            index=0,
            id=call_id,
            function=SimpleNamespace(name=name, arguments=arguments),
        )

    stream = _FakeStream(
        [
            _chat_chunk(tool_calls=[delta("call_", "get_", '{"value":')]),
            _chat_chunk(
                tool_calls=[
                    delta(
                        "call_123" if cumulative else "123",
                        "get_state" if cumulative else "state",
                        "1}",
                    )
                ]
            ),
            _chat_chunk(
                tool_calls=[delta("call_123", "get_state", None)],
                finish_reason="tool_calls",
            ),
        ]
    )
    output = [
        item async for item in entity._transform_chat_stream(_chat_log(hass), stream)
    ]
    call = next(item["tool_calls"][0] for item in output if "tool_calls" in item)
    assert (call.id, call.tool_name, call.tool_args) == (
        "call_123",
        "get_state",
        {"value": 1},
    )


def test_strict_oneof_is_normalized_only_when_exclusive():
    schema = {
        "type": "object",
        "properties": {"value": {"oneOf": [{"type": "string"}, {"type": "integer"}]}},
    }
    _adjust_schema(schema, root=True)
    assert "oneOf" not in str(schema)
    once = deepcopy(schema)
    _adjust_schema(schema, root=True)
    assert schema == once
    for alternatives in (
        [{"type": "number"}, {"type": "integer"}],
        [{"type": "object"}, {"type": "object"}],
        [{"type": "string", "nullable": True}, {"type": "null"}],
        [
            {
                "type": ["object", "null"],
                "properties": {"kind": {"const": kind}},
                "required": ["kind"],
            }
            for kind in ("a", "b")
        ],
    ):
        with pytest.raises(HomeAssistantError, match="overlapping oneOf"):
            _adjust_schema(
                {"type": "object", "properties": {"value": {"oneOf": alternatives}}},
                root=True,
            )


def test_oneof_conversion_does_not_overwrite_existing_anyof():
    schema = {
        "type": "object",
        "properties": {
            "value": {
                "anyOf": [{"enum": ["allowed"]}],
                "oneOf": [{"type": "string"}, {"type": "number"}],
            }
        },
    }
    with pytest.raises(HomeAssistantError, match="combine oneOf and anyOf"):
        _adjust_schema(schema, root=True)


def test_azure_deployment_identity_uses_explicit_underlying_capabilities():
    options = {
        "chat_model": "ha-production",
        "azure_model": "gpt-4.1",
        "api_mode": "chat_completions",
    }
    deployment = build_provider_request_snapshot(
        options, {"api_provider": "azure"}, tools_required=True
    )
    model = build_provider_request_snapshot(
        {**options, "chat_model": "gpt-4.1"},
        {"api_provider": "azure"},
        tools_required=True,
    )
    assert deployment.api_kwargs["model"] == "ha-production"
    assert {**deployment.api_kwargs, "model": "gpt-4.1"} == model.api_kwargs
    with pytest.raises(HomeAssistantError, match="only supported with Azure"):
        build_provider_request_snapshot(
            options, {"api_provider": "openai"}, tools_required=True
        )


def test_unsupported_tier_is_rejected_before_submission():
    with pytest.raises(HomeAssistantError, match="processing tier"):
        build_provider_request_snapshot(
            {"chat_model": "gpt-4.1", "service_tier": "flex"}, {}, tools_required=False
        )


def test_actual_caller_union_overrides_permissive_projection_null_semantics():
    # A caller's custom serializer can describe a broader union than its validator.
    # Strict output then supplies null placeholders which that caller rejects.
    structure = vol.Schema(
        {
            vol.Optional("payload"): vol.Any(
                {vol.Optional("note"): str}, {vol.Required("count"): int}
            )
        }
    )
    projection = {
        "type": "object",
        "properties": {
            "payload": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {"note": {}},
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                        "required": ["count"],
                    },
                ]
            }
        },
    }
    assert parse_ai_task_structured_response(
        '{"payload":{"note":null}}', structure, original_schema=projection
    ) == {"payload": {}}
    with pytest.raises(HomeAssistantError, match="does not match"):
        parse_ai_task_structured_response(
            '{"payload":{"count":null}}', structure, original_schema=projection
        )
    nullable = vol.Schema({vol.Optional("payload"): vol.Any(str, None)})
    assert parse_ai_task_structured_response(
        '{"payload":null}',
        nullable,
        original_schema={"type": "object", "properties": {"payload": {}}},
    ) == {"payload": None}


def flow_handler(options):
    return SimpleNamespace(
        options=options,
        _temp_data=None,
        _is_new=True,
        _get_entry=lambda: SimpleNamespace(state=ConfigEntryState.LOADED, data={}),
        async_show_form=lambda **kwargs: kwargs,
        async_create_entry=lambda **kwargs: kwargs,
        add_suggested_values_to_schema=MagicMock(
            side_effect=lambda schema, values: schema
        ),
        async_step_advanced=AsyncMock(return_value={"step_id": "advanced"}),
    )


async def test_hidden_reasoning_normalization_survives_basic_validation_retry():
    handler = flow_handler(
        {**DEFAULT_AI_TASK_OPTIONS, "chat_model": "gpt-5.6", "reasoning_effort": "none"}
    )
    first = await Flow.async_step_init(
        handler,
        {"chat_model": "gpt-5-mini", "api_mode": "invalid", "advanced_options": False},
    )
    assert first["errors"] == {"base": "invalid_request"}
    assert handler.options["reasoning_effort"] != "none"
    second = await Flow.async_step_init(
        handler,
        {
            "chat_model": "gpt-5-mini",
            "api_mode": "responses",
            "advanced_options": False,
        },
    )
    assert second["data"]["reasoning_effort"] == handler.options["reasoning_effort"]


async def test_advanced_model_change_displays_and_saves_one_effective_configuration():
    handler = flow_handler(
        {
            **DEFAULT_AI_TASK_OPTIONS,
            "chat_model": "gpt-5-mini",
            "reasoning_effort": "high",
            "temperature": 0.4,
            "top_p": 0.8,
        }
    )
    handler._is_new = False
    await Flow.async_step_init(
        handler, {"chat_model": "gpt-5.4", "advanced_options": True}
    )
    form = await Flow.async_step_advanced(handler)
    suggested = handler.add_suggested_values_to_schema.call_args.args[1]
    assert suggested["reasoning_effort"] == "none"
    assert {"temperature", "top_p"} <= {str(key) for key in form["data_schema"].schema}
    handler.async_update_and_abort = lambda *args, **kwargs: kwargs
    handler._get_reconfigure_subentry = lambda: None
    saved = await Flow.async_step_advanced(handler, {"temperature": 0.4, "top_p": 0.8})
    assert saved["data"]["reasoning_effort"] == "none"
    assert saved["data"]["temperature"] == 0.4
