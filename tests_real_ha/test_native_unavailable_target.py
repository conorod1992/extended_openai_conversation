"""Real-HA recovery when a native service target becomes unavailable mid-turn."""

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
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import Context, HomeAssistant, ServiceCall
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
)
from tests_real_ha.test_provider_wire_e2e import _install_wire, _speech

_ENTITY_ID = "light.native_unavailable_target"
_DOMAIN = "light"
_SERVICE = "native_unavailable_test"
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
async def test_native_target_becomes_unavailable_before_dispatch_then_recovers(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An existing unavailable target fails at HA's service boundary and can recover."""
    service_calls: list[ServiceCall] = []

    def require_available_target(value: Any) -> Any:
        entity_ids = value if isinstance(value, list) else [value]
        for entity_id in entity_ids:
            state = hass.states.get(entity_id)
            if state is None:
                raise vol.Invalid(f"Target entity does not exist: {entity_id}")
            if state.state == STATE_UNAVAILABLE:
                raise vol.Invalid(f"Target entity is unavailable: {entity_id}")
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
                    require_available_target,
                )
            },
            extra=vol.ALLOW_EXTRA,
        ),
    )
    hass.states.async_set(_ENTITY_ID, "on", {"friendly_name": "Unavailable target"})
    async_expose_entity(hass, conversation.DOMAIN, _ENTITY_ID, True)
    await hass.async_block_till_done()

    entry = _make_entry(
        "Native Unavailable Target",
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
                "call-native-target-unavailable",
                _TOOL_NAME,
                _arguments(),
            ),
            _chat_sse_text("The target is currently unavailable."),
        ],
    )

    failing_task = asyncio.create_task(
        _say(hass, entry.entry_id, "Turn off the target that may become unavailable")
    )
    await asyncio.wait_for(dispatch_entered.wait(), timeout=_WAIT_TIMEOUT)

    # The native tool has already validated that the target exists and is exposed.
    # Keep the entity present, but change only its live HA state before dispatch.
    hass.states.async_set(
        _ENTITY_ID,
        STATE_UNAVAILABLE,
        {"friendly_name": "Unavailable target"},
    )
    await hass.async_block_till_done()
    unavailable = hass.states.get(_ENTITY_ID)
    assert unavailable is not None
    assert unavailable.state == STATE_UNAVAILABLE
    allow_dispatch.set()

    failed = await asyncio.wait_for(failing_task, timeout=_WAIT_TIMEOUT)
    assert _speech(failed) == "The target is currently unavailable."
    assert service_calls == []
    assert len(failing_wire.requests) == 2

    failed_tool_result = _tool_result_from_chat_request(
        failing_wire.requests[1]["body"], "call-native-target-unavailable"
    )
    assert "result" in failed_tool_result
    assert len(failed_tool_result["result"]) == 1
    error = failed_tool_result["result"][0]
    assert "error" in error
    assert "unavailable" in error["error"].lower()
    assert _ENTITY_ID in error["error"]

    # This distinguishes the case from target disappearance: HA still owns a live
    # State object and the exposure/configured-tool boundaries remain unchanged.
    still_present = hass.states.get(_ENTITY_ID)
    assert still_present is not None
    assert still_present.state == STATE_UNAVAILABLE

    hass.states.async_set(_ENTITY_ID, "on", {"friendly_name": "Unavailable target"})
    await hass.async_block_till_done()
    restored = hass.states.get(_ENTITY_ID)
    assert restored is not None
    assert restored.state == "on"

    recovered_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "call-native-target-available-again",
                _TOOL_NAME,
                _arguments(),
            ),
            _chat_sse_text("The available target was controlled successfully."),
        ],
    )
    recovered = await _say(
        hass,
        entry.entry_id,
        "Try the now-available target again",
    )

    assert _speech(recovered) == "The available target was controlled successfully."
    assert len(service_calls) == 1
    assert service_calls[0].data[ATTR_ENTITY_ID] == [_ENTITY_ID]
    assert len(recovered_wire.requests) == 2
    recovered_tool_result = _tool_result_from_chat_request(
        recovered_wire.requests[1]["body"],
        "call-native-target-available-again",
    )
    assert recovered_tool_result["result"][0]["success"] is True
    assert "error" not in recovered_tool_result["result"][0]

    configured = next(
        tool
        for tool in agent.subentry.data[CONF_FUNCTION_TOOLS]
        if tool["spec"]["name"] == _TOOL_NAME
    )
    assert configured["enabled"] is True
