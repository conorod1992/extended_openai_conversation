"""Real-HA recovery when a native Home Assistant service disappears mid-turn."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import pytest

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
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
)
from tests_real_ha.test_provider_wire_e2e import _install_wire, _speech

_ENTITY_ID = "light.native_disappearing_service_target"
_DOMAIN = "light"
_SERVICE = "native_service_disappearance_test"
_TOOL_NAME = "execute_service"


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
async def test_native_service_disappears_before_dispatch_and_next_turn_recovers(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A removed service fails cleanly after validation and is usable again later."""
    service_calls: list[ServiceCall] = []

    async def service_handler(call: ServiceCall) -> None:
        service_calls.append(call)

    def register_service() -> None:
        hass.services.async_register(_DOMAIN, _SERVICE, service_handler)

    register_service()
    hass.states.async_set(
        _ENTITY_ID,
        "on",
        {"friendly_name": "Disappearing service target"},
    )
    async_expose_entity(hass, conversation.DOMAIN, _ENTITY_ID, True)
    await hass.async_block_till_done()

    entry = _make_entry(
        "Native Service Disappearance",
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

    real_call = native_module.async_call_ha_action
    remove_service_once = True

    async def remove_before_dispatch(*args: Any, **kwargs: Any) -> Any:
        nonlocal remove_service_once
        if remove_service_once:
            remove_service_once = False
            # Reaching this seam proves execute_service_single() already saw the
            # service as registered and completed the native target/exposure checks.
            hass.services.async_remove(_DOMAIN, _SERVICE)
            assert not hass.services.has_service(_DOMAIN, _SERVICE)
        return await real_call(*args, **kwargs)

    monkeypatch.setattr(
        native_module,
        "async_call_ha_action",
        remove_before_dispatch,
    )

    failing_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "call-native-service-disappears",
                _TOOL_NAME,
                _arguments(),
            ),
            _chat_sse_text("The Home Assistant service disappeared before dispatch."),
        ],
    )

    failed = await _say(
        hass,
        entry.entry_id,
        "Use the disappearing Home Assistant service",
    )

    assert _speech(failed) == "The Home Assistant service disappeared before dispatch."
    assert service_calls == []
    assert not hass.services.has_service(_DOMAIN, _SERVICE)
    assert len(failing_wire.requests) == 2

    failed_tool_result = _tool_result_from_chat_request(
        failing_wire.requests[1]["body"],
        "call-native-service-disappears",
    )
    assert "result" in failed_tool_result
    assert len(failed_tool_result["result"]) == 1
    failure = failed_tool_result["result"][0]
    assert "error" in failure
    error_text = failure["error"].casefold()
    assert error_text == "service_not_found" or (
        _SERVICE in error_text and "not found" in error_text
    )

    # Restore the exact same real HA service and prove the same loaded agent/tool
    # succeeds on a completely separate public Conversation turn.
    register_service()
    assert hass.services.has_service(_DOMAIN, _SERVICE)

    recovered_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "call-native-service-recovers",
                _TOOL_NAME,
                _arguments(),
            ),
            _chat_sse_text("The restored Home Assistant service ran successfully."),
        ],
    )
    recovered = await _say(
        hass,
        entry.entry_id,
        "Try the restored Home Assistant service again",
    )

    assert _speech(recovered) == "The restored Home Assistant service ran successfully."
    assert len(service_calls) == 1
    assert service_calls[0].domain == _DOMAIN
    assert service_calls[0].service == _SERVICE
    assert service_calls[0].data[ATTR_ENTITY_ID] == [_ENTITY_ID]
    assert len(recovered_wire.requests) == 2

    recovered_tool_result = _tool_result_from_chat_request(
        recovered_wire.requests[1]["body"],
        "call-native-service-recovers",
    )
    assert recovered_tool_result["result"][0]["success"] is True
    assert "error" not in recovered_tool_result["result"][0]

    configured = next(
        tool
        for tool in agent.subentry.data[CONF_FUNCTION_TOOLS]
        if tool["spec"]["name"] == _TOOL_NAME
    )
    assert configured["enabled"] is True
