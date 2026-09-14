"""Real-HA coverage for live service-schema rejection at dispatch."""

from __future__ import annotations

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

_ENTITY_ID = "light.native_schema_boundary"
_DOMAIN = "light"
_SERVICE = "schema_boundary_test"
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


def _arguments(mode: str) -> dict[str, Any]:
    """Build syntactically valid native-tool arguments for the live HA service."""
    return {
        "list": [
            {
                "domain": _DOMAIN,
                "service": _SERVICE,
                "service_data": {
                    ATTR_ENTITY_ID: [_ENTITY_ID],
                    "mode": mode,
                },
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
    tool_message = next(
        item for item in request["messages"] if item.get("role") == "tool"
    )
    assert tool_message["tool_call_id"] == expected_call_id
    return json.loads(tool_message["content"])


@pytest.mark.asyncio
async def test_live_service_schema_rejection_is_model_visible_and_next_turn_recovers(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HA schema rejection causes no action and does not poison later tool use."""
    calls: list[ServiceCall] = []

    async def service_handler(call: ServiceCall) -> None:
        calls.append(call)
        hass.states.async_set(
            _ENTITY_ID,
            str(call.data["mode"]),
            {"friendly_name": "Schema boundary target"},
        )

    # The integration's native tool accepts arbitrary service_data mappings. The
    # intentionally stricter contract lives only at Home Assistant's real service
    # registry boundary, which models HA-version/service-schema drift realistically.
    hass.services.async_register(
        _DOMAIN,
        _SERVICE,
        service_handler,
        schema=vol.Schema(
            {
                vol.Required(ATTR_ENTITY_ID): vol.Any(str, [str]),
                vol.Required("mode"): vol.In({"eco", "boost"}),
            },
            extra=vol.PREVENT_EXTRA,
        ),
    )
    hass.states.async_set(
        _ENTITY_ID,
        "idle",
        {"friendly_name": "Schema boundary target"},
    )
    async_expose_entity(hass, conversation.DOMAIN, _ENTITY_ID, True)
    await hass.async_block_till_done()

    entry = _make_entry(
        "Native Service Schema Boundary",
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

    failing_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "call-live-schema-reject",
                _TOOL_NAME,
                _arguments("turbo"),
            ),
            _chat_sse_text("Home Assistant rejected that service value."),
        ],
    )

    failed = await _say(
        hass,
        entry.entry_id,
        "Set the schema boundary target to turbo mode",
    )

    assert _speech(failed) == "Home Assistant rejected that service value."
    assert calls == []
    assert hass.states[_ENTITY_ID].state == "idle"
    assert len(failing_wire.requests) == 2

    failed_tool_result = _tool_result_from_chat_request(
        failing_wire.requests[1]["body"], "call-live-schema-reject"
    )
    assert "result" in failed_tool_result
    assert len(failed_tool_result["result"]) == 1
    failed_service = failed_tool_result["result"][0]
    assert "error" in failed_service
    assert "value must be one of" in failed_service["error"]
    assert "mode" in failed_service["error"]

    # A valid value against the exact same live HA schema must succeed on a new
    # conversation turn through the same loaded agent. This proves the validation
    # failure is a bounded tool error rather than poisoned native-tool/runtime state.
    recovered_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "call-live-schema-recovers",
                _TOOL_NAME,
                _arguments("eco"),
            ),
            _chat_sse_text("Home Assistant accepted eco mode."),
        ],
    )

    recovered = await _say(
        hass,
        entry.entry_id,
        "Set the schema boundary target to eco mode",
    )

    assert _speech(recovered) == "Home Assistant accepted eco mode."
    assert len(calls) == 1
    assert calls[0].data[ATTR_ENTITY_ID] == [_ENTITY_ID]
    assert calls[0].data["mode"] == "eco"
    assert hass.states[_ENTITY_ID].state == "eco"
    assert len(recovered_wire.requests) == 2

    recovered_tool_result = _tool_result_from_chat_request(
        recovered_wire.requests[1]["body"], "call-live-schema-recovers"
    )
    assert recovered_tool_result["result"][0]["success"] is True
    assert "error" not in recovered_tool_result["result"][0]

    configured = next(
        tool
        for tool in agent.subentry.data[CONF_FUNCTION_TOOLS]
        if tool["spec"]["name"] == _TOOL_NAME
    )
    assert configured["enabled"] is True
