"""Real-HA recovery when a native service target disappears mid-turn."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from typing import Any

import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses.built_in_functions import (
    BUILT_IN_FUNCTION_PRESETS,
)
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOL_ERROR_RECOVERY,
    CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.functions import (
    native as native_module,
)
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
)
from tests_real_ha.test_provider_wire_e2e import _install_wire, _speech

_ENTITY_ID = "light.native_disappearing_target"
_DOMAIN = "light"
_SERVICE = "native_disappearance_test"
_TOOL_NAME = "execute_service"
_WAIT_TIMEOUT = 10


def _native_execute_service_tool() -> dict[str, Any]:
    """Return the current built-in execute-service Function Tool definition."""
    preset = next(
        item
        for item in BUILT_IN_FUNCTION_PRESETS
        if item["implementation"] == _TOOL_NAME
    )
    tool = deepcopy(preset["tool"])
    tool["enabled"] = True
    return tool


def _arguments() -> dict[str, Any]:
    return {
        "list": [
            {
                "domain": _DOMAIN,
                "service": _SERVICE,
                "service_data": {ATTR_ENTITY_ID: [_ENTITY_ID]},
            }
        ]
    }


async def _say(
    hass: HomeAssistant,
    entry_id: str,
    text: str,
) -> conversation.ConversationResult:
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry_id,
    )


def _tool_result_from_chat_request(
    request: dict[str, Any], expected_call_id: str
) -> dict[str, Any]:
    """Decode one Chat Completions tool result for this test's call id."""
    tool_message = next(
        item for item in request["messages"] if item.get("role") == "tool"
    )
    assert tool_message["tool_call_id"] == expected_call_id
    return json.loads(tool_message["content"])


@pytest.mark.asyncio
async def test_native_service_target_disappears_before_dispatch_and_next_turn_recovers(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target removed after tool selection fails cleanly without poisoning later use."""
    service_calls: list[ServiceCall] = []

    def entity_still_exists(value: Any) -> Any:
        entity_ids = value if isinstance(value, list) else [value]
        missing = [
            entity_id
            for entity_id in entity_ids
            if hass.states.get(entity_id) is None
        ]
        if missing:
            raise HomeAssistantError(
                f"Target entity no longer exists: {', '.join(missing)}"
            )
        return value

    async def service_handler(call: ServiceCall) -> None:
        service_calls.append(call)

    hass.services.async_register(
        _DOMAIN,
        _SERVICE,
        service_handler,
        schema=vol.Schema(
            {
                vol.Required(ATTR_ENTITY_ID): vol.All(
                    vol.Any(str, [str]),
                    entity_still_exists,
                )
            },
            extra=vol.ALLOW_EXTRA,
        ),
    )
    hass.states.async_set(_ENTITY_ID, "on", {"friendly_name": "Vanishing target"})
    async_expose_entity(hass, conversation.DOMAIN, _ENTITY_ID, True)
    await hass.async_block_till_done()

    entry = _make_entry(
        "Native Target Disappearance",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOL_ERROR_RECOVERY: True,
            CONF_FUNCTION_TOOLS: [_native_execute_service_tool()],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    dispatch_entered = asyncio.Event()
    allow_dispatch = asyncio.Event()
    real_call = native_module.async_call_ha_action

    async def gated_call(*args: Any, **kwargs: Any) -> Any:
        dispatch_entered.set()
        await allow_dispatch.wait()
        return await real_call(*args, **kwargs)

    monkeypatch.setattr(native_module, "async_call_ha_action", gated_call)

    failing_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "call-native-target-disappears",
                _TOOL_NAME,
                _arguments(),
            ),
            _chat_sse_text("The target disappeared before I could control it."),
        ],
    )

    failing_task = asyncio.create_task(
        _say(hass, entry.entry_id, "Turn off the vanishing target")
    )
    await asyncio.wait_for(dispatch_entered.wait(), timeout=_WAIT_TIMEOUT)

    # Reaching the action seam proves the provider call was parsed and the native
    # tool's initial entity/exposure validation already succeeded. Remove the target
    # only now, immediately before the genuine Home Assistant service dispatch.
    hass.states.async_remove(_ENTITY_ID)
    await hass.async_block_till_done()
    assert hass.states.get(_ENTITY_ID) is None
    allow_dispatch.set()

    failed = await asyncio.wait_for(failing_task, timeout=_WAIT_TIMEOUT)
    assert _speech(failed) == "The target disappeared before I could control it."
    assert service_calls == []
    assert len(failing_wire.requests) == 2

    failed_tool_result = _tool_result_from_chat_request(
        failing_wire.requests[1]["body"], "call-native-target-disappears"
    )
    assert "result" in failed_tool_result
    assert len(failed_tool_result["result"]) == 1
    error = failed_tool_result["result"][0]
    assert "error" in error
    assert "no longer exists" in error["error"]
    assert _ENTITY_ID in error["error"]

    # Restore the same real target and prove a completely separate public Assist
    # turn can execute the same native tool successfully after the runtime failure.
    hass.states.async_set(_ENTITY_ID, "on", {"friendly_name": "Vanishing target"})
    await hass.async_block_till_done()
    assert hass.states.get(_ENTITY_ID) is not None

    recovered_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "call-native-target-recovers",
                _TOOL_NAME,
                _arguments(),
            ),
            _chat_sse_text("The restored target was controlled successfully."),
        ],
    )
    recovered = await _say(
        hass,
        entry.entry_id,
        "Try the restored target again",
    )

    assert _speech(recovered) == "The restored target was controlled successfully."
    assert len(service_calls) == 1
    assert service_calls[0].data[ATTR_ENTITY_ID] == [_ENTITY_ID]
    assert len(recovered_wire.requests) == 2
    recovered_tool_result = _tool_result_from_chat_request(
        recovered_wire.requests[1]["body"], "call-native-target-recovers"
    )
    assert recovered_tool_result["result"][0]["success"] is True
    assert "error" not in recovered_tool_result["result"][0]

    # The native Function Tool remains configured and usable; recovery must not
    # silently disable or mutate it after one transient Home Assistant target race.
    configured = next(
        tool
        for tool in agent.subentry.data[CONF_FUNCTION_TOOLS]
        if tool["spec"]["name"] == _TOOL_NAME
    )
    assert configured["enabled"] is True
