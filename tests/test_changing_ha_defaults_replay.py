"""Replay after a failed continuation through public conversation entry and HA discovery."""

from itertools import count
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
from homeassistant.components import conversation
from homeassistant.helpers import llm
from tests.test_conversation_provider_loop_contracts import _provider_fixture
from tests.test_ha_llm_tools import TestAPI, context, register
from tests.test_tool_exchange_protocol import _final_stream, _function_call_stream


@pytest.mark.parametrize("value_type", [int, str])
@pytest.mark.parametrize(
    ("normalization", "repeat"),
    [
        (normalization, repeat)
        for normalization in ("fixed", "changing", "coercion")
        for repeat in (
            "failed-identical",
            "failed-distinct",
            "acknowledged",
            "recovered",
            "failed-equivalent",
        )
        if repeat != "failed-equivalent" or normalization == "coercion"
    ],
)
async def test_ha_schema_normalization_preserves_replay_protection(
    hass, monkeypatch, value_type, normalization, repeat
):
    counter = count(1)
    effects = []

    def increment(value):
        return value_type(int(value) + 1)

    class Action(llm.Tool):
        name = "effect"
        integration = "extended_openai_conversation_responses"
        description = "Increment an observable effect"
        parameters = vol.Schema(
            {
                vol.Optional(
                    "operation_id",
                    default=lambda: value_type(
                        next(counter) if normalization == "changing" else 1
                    ),
                ): vol.All(vol.Coerce(value_type), increment)
                if normalization == "coercion"
                else value_type
            }
        )

        async def async_call(self, hass, tool_input, llm_context):
            # Native HA tools own validation of their dispatch input. A second
            # application to already-prepared values would increment twice.
            effects.append(self.parameters(dict(tool_input.tool_args)))
            payload = {"completed": True}
            result_type = getattr(llm, "ToolResult", None)
            return result_type(data=payload) if result_type is not None else payload

    class SourceAPI(TestAPI):
        async def async_get_api_instance(self, context):
            instance = await super().async_get_api_instance(context)
            instance.custom_serializer = lambda schema: (
                {"type": "integer" if value_type is int else "string"}
                if schema is increment
                else UNSUPPORTED
            )
            return instance

    register(hass, SourceAPI(hass, api_id="audit", tools=[Action()]))
    discovered = await async_discover(hass, context())
    configured = new_reference_tool(
        next(iter(discovered.tools.values())).reference, set()
    )
    name = configured["spec"]["name"]
    retry_args = (
        {"operation_id": value_type(900)} if repeat == "failed-distinct" else {}
    )
    if repeat == "failed-equivalent":
        retry_args = {"operation_id": "1"}
    streams = [
        _function_call_stream([("first", name, {})]),
        _final_stream()
        if repeat == "acknowledged"
        else TimeoutError("continuation lost"),
    ]
    if repeat == "recovered":
        # A later successful response acknowledges the retained effect before
        # the user deliberately requests the same action on a third turn.
        streams.append(_final_stream())
    streams.extend(
        [
            _function_call_stream([("new-id", name, retry_args)]),
            _final_stream(),
        ]
    )
    entity, client, _, logs, invoke = _provider_fixture(hass, monkeypatch, streams)
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
        }
    )
    entity.entry.subentries = {"agent": entity.subentry}
    hass.config_entries.async_get_entry.return_value = entity.entry
    hass.auth.async_get_user = AsyncMock(
        return_value=SimpleNamespace(is_active=True, is_admin=True)
    )
    retained = []

    def get_log(_hass, _session, request):
        log = conversation.ChatLog(hass, "conversation-1")
        if retained:
            log.content[:] = retained
        log.async_add_user_content(conversation.UserContent(content=request.text))
        retained.clear()

        # Keep the exact log content after the turn without patching any replay state.
        class Lease:
            def __enter__(self):
                return log

            def __exit__(self, *args):
                retained.extend(log.content)

        logs.append(log)
        return Lease()

    monkeypatch.setattr(owner, "async_get_chat_log", get_log)
    first = await invoke()
    assert len(effects) == 1
    assert client.responses.create.await_count == 2
    assert (first.response.error_code is None) == (repeat == "acknowledged")
    second = await invoke()
    assert first.conversation_id == second.conversation_id == "conversation-1"
    if repeat == "recovered":
        assert len(effects) == 1
        assert second.response.error_code is None
        second = await invoke()
        assert second.conversation_id == "conversation-1"
    # HA owns the second normalization; a changed preview cannot certify the
    # executed identity. Until acknowledgement, distinct calls are conservative
    # too. Acknowledged/recovered repetitions remain independent operations.
    blocked = repeat in {"failed-identical", "failed-equivalent", "failed-distinct"}
    assert len(effects) == (1 if blocked else 2)
    if normalization == "coercion":
        assert effects[0] == {"operation_id": value_type(2)}
    if blocked:
        assert second.response.error_code is not None
    else:
        assert second.response.error_code is None
