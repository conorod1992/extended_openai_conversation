"""Native HA error envelopes and actual default values retain recovery safety."""

from contextlib import contextmanager
from itertools import count
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from voluptuous_openapi import UNSUPPORTED

from custom_components.extended_openai_conversation_responses import (
    conversation as owner,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    async_discover,
    new_reference_tool,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from tests.test_conversation_provider_loop_contracts import _provider_fixture
from tests.test_ha_llm_tools import TestAPI, context, register
from tests.test_tool_exchange_protocol import _final_stream, _function_call_stream


async def _native_conversation(
    hass, monkeypatch, tool, *, recovery=False, serializer=None
):
    class SourceAPI(TestAPI):
        async def async_get_api_instance(self, context):
            instance = await super().async_get_api_instance(context)
            if serializer is not None:
                instance.custom_serializer = serializer
            return instance

    register(hass, SourceAPI(hass, api_id="effect-recovery", tools=[tool]))
    discovered = await async_discover(hass, context())
    configured = new_reference_tool(
        next(iter(discovered.tools.values())).reference, set()
    )
    entity, client, _, logs, invoke = _provider_fixture(hass, monkeypatch, [])
    for attr in (
        "_execute_function_tool",
        "_configured_function_tools_from_data",
        "_get_function_tools",
    ):
        delattr(entity, attr)
    entity.subentry.data = normalize_agent_config(
        {
            "chat_model": "gpt-4o",
            "api_mode": "responses",
            "functions": [configured],
            "function_groups": [],
            "memory_mode": "off",
            "function_tool_error_recovery": recovery,
        }
    )
    entity.entry.subentries = {"agent": entity.subentry}
    hass.config_entries.async_get_entry.return_value = entity.entry
    hass.auth.async_get_user = AsyncMock(
        return_value=SimpleNamespace(is_active=True, is_admin=True)
    )
    retained = []

    @contextmanager
    def get_log(_hass, _session, request):
        log = conversation.ChatLog(hass, "conversation-1")
        if retained:
            log.content[:] = retained
        log.async_add_user_content(conversation.UserContent(content=request.text))
        logs.append(log)
        try:
            yield log
        finally:
            retained[:] = log.content

    monkeypatch.setattr(owner, "async_get_chat_log", get_log)
    return SimpleNamespace(
        entity=entity,
        client=client,
        retained=retained,
        invoke=invoke,
        name=configured["spec"]["name"],
    )


@pytest.mark.parametrize("failure", ["raised", "flagged", "nested"])
@pytest.mark.parametrize("recovery", [False, True])
async def test_partial_ha_failure_blocks_changed_argument_retry(
    hass, monkeypatch, failure, recovery
):
    if not hasattr(llm, "ToolResult"):
        pytest.skip("HA's native ToolResult envelope is required")
    effects = []

    class Partial(llm.Tool):
        name = "partial"
        integration = "extended_openai_conversation_responses"
        description = "Apply targets sequentially"
        parameters = vol.Schema({vol.Required("targets"): [str]})

        async def async_call(self, hass, tool_input, llm_context):
            for target in self.parameters(tool_input.tool_args)["targets"]:
                if target == "failed":
                    if failure == "raised":
                        raise HomeAssistantError("target failed")
                    result = llm.ToolResult(data={"failed": target}, error=True)
                    return (
                        llm.ToolResult(data=result) if failure == "nested" else result
                    )
                effects.append(target)
            return llm.ToolResult(data={"done": True})

    run = await _native_conversation(hass, monkeypatch, Partial(), recovery=recovery)
    run.client.responses.create.side_effect = [
        _function_call_stream(
            [("first", run.name, {"targets": ["successful", "failed"]})]
        ),
        *([] if recovery else [TimeoutError("continuation lost")]),
    ]
    first = await run.invoke()
    assert first.response.error_code is not None
    assert effects == ["successful"]
    assert "uncertain" in run.entity._execution_tool_outcomes.values()
    run.client.responses.create.side_effect = [
        _function_call_stream([("new-id", run.name, {"targets": ["successful"]})]),
        _final_stream(),
    ]
    second = await run.invoke()
    assert first.conversation_id == second.conversation_id == "conversation-1"
    assert second.response.error_code is not None
    assert effects == ["successful"]


@pytest.mark.parametrize("value_type", [int, str])
@pytest.mark.parametrize("changing", [False, True])
@pytest.mark.parametrize("acknowledged", [False, True])
async def test_actual_ha_default_returned_to_model_cannot_replay(
    hass, monkeypatch, value_type, changing, acknowledged
):
    counter = count(1)
    effects = []

    class Default(llm.Tool):
        name = "actual_default"
        integration = "extended_openai_conversation_responses"
        description = "Return the actual invocation"
        parameters = vol.Schema(
            {
                vol.Optional(
                    "operation_id",
                    default=lambda: value_type(next(counter) if changing else 1),
                ): value_type
            }
        )

        async def async_call(self, hass, tool_input, llm_context):
            actual = self.parameters(dict(tool_input.tool_args))
            effects.append(actual)
            result_type = getattr(llm, "ToolResult", None)
            return result_type(data=actual) if result_type is not None else actual

    run = await _native_conversation(hass, monkeypatch, Default())
    run.client.responses.create.side_effect = [
        _function_call_stream([("first", run.name, {})]),
        _final_stream() if acknowledged else TimeoutError("continuation lost"),
    ]
    first = await run.invoke()
    assert len(effects) == 1
    actual_result = next(
        item
        for item in run.retained
        if isinstance(item, conversation.ToolResultContent)
    )
    actual = tool_result_data(actual_result)["result"]
    assert actual == effects[0]
    run.client.responses.create.side_effect = [
        _function_call_stream([("new-id", run.name, actual)]),
        _final_stream(),
    ]
    second = await run.invoke()
    assert first.conversation_id == second.conversation_id == "conversation-1"
    # The retry provider really sees the value returned by native execution.
    retry_request = run.client.responses.create.call_args_list[2].kwargs["input"]
    assert any(
        item.get("type") == "function_call_output"
        and json.loads(item["output"])["result"] == actual
        for item in retry_request
    )
    assert len(effects) == (2 if acknowledged else 1)
    assert (second.response.error_code is None) == acknowledged


async def test_explicit_identity_preserving_ha_calls_remain_distinct(hass, monkeypatch):
    effects = []

    class Explicit(llm.Tool):
        name = "explicit"
        integration = "extended_openai_conversation_responses"
        description = "Record a supplied operation"
        parameters = vol.Schema({vol.Required("operation_id"): int})

        async def async_call(self, hass, tool_input, llm_context):
            effects.append(self.parameters(tool_input.tool_args))
            result_type = getattr(llm, "ToolResult", None)
            return (
                result_type(data={"done": True})
                if result_type is not None
                else {"done": True}
            )

    run = await _native_conversation(hass, monkeypatch, Explicit())
    run.client.responses.create.side_effect = [
        _function_call_stream([("first", run.name, {"operation_id": 1})]),
        TimeoutError("continuation lost"),
        _function_call_stream([("next", run.name, {"operation_id": 2})]),
        _final_stream(),
    ]
    await run.invoke()
    second = await run.invoke()
    assert second.response.error_code is None
    assert effects == [{"operation_id": 1}, {"operation_id": 2}]


async def test_unchanged_preview_does_not_certify_stateful_native_normalization(
    hass, monkeypatch
):
    validations = []
    effects = []

    def normalize(value):
        validations.append(value)
        return value if len(validations) == 1 else 11

    class Stateful(llm.Tool):
        name = "stateful"
        integration = "extended_openai_conversation_responses"
        description = "Return the effective invocation"
        parameters = vol.Schema({vol.Required("operation_id"): vol.All(int, normalize)})

        async def async_call(self, hass, tool_input, llm_context):
            actual = self.parameters(dict(tool_input.tool_args))
            effects.append(actual)
            result_type = getattr(llm, "ToolResult", None)
            return result_type(data=actual) if result_type is not None else actual

    run = await _native_conversation(
        hass,
        monkeypatch,
        Stateful(),
        serializer=lambda schema: (
            {"type": "integer"} if schema is normalize else UNSUPPORTED
        ),
    )
    run.client.responses.create.side_effect = [
        _function_call_stream([("first", run.name, {"operation_id": 10})]),
        TimeoutError("continuation lost"),
    ]
    await run.invoke()
    assert validations == [10, 10]
    assert effects == [{"operation_id": 11}]
    result = next(
        item
        for item in run.retained
        if isinstance(item, conversation.ToolResultContent)
    )
    actual = tool_result_data(result)["result"]
    run.client.responses.create.side_effect = [
        _function_call_stream([("new-id", run.name, actual)]),
        _final_stream(),
    ]
    second = await run.invoke()
    assert second.response.error_code is not None
    assert effects == [{"operation_id": 11}]
