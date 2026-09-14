"""Real-HA acceptance for mixed exposure in native indirect targets."""

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
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.const import ATTR_AREA_ID
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    target as target_helpers,
)
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
)
from tests_real_ha.test_provider_wire_e2e import _install_wire, _speech

_TOOL_NAME = "execute_service"
_SERVICE_DOMAIN = "mixed_exposure_test"
_SERVICE_NAME = "mark"


def _native_execute_service_tool() -> dict[str, Any]:
    """Return an isolated current built-in execute-service definition."""
    preset = next(
        item
        for item in BUILT_IN_FUNCTION_PRESETS
        if item["implementation"] == _TOOL_NAME
    )
    tool = deepcopy(preset["tool"])
    tool["enabled"] = True
    return tool


def _arguments(area_id: str, marker: str) -> dict[str, Any]:
    return {
        "list": [
            {
                "domain": _SERVICE_DOMAIN,
                "service": _SERVICE_NAME,
                "service_data": {ATTR_AREA_ID: area_id, "marker": marker},
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


def _tool_result(body: dict[str, Any], expected_call_id: str) -> dict[str, Any]:
    message = next(item for item in body["messages"] if item.get("role") == "tool")
    assert message["tool_call_id"] == expected_call_id
    return json.loads(message["content"])


@pytest.mark.asyncio
async def test_indirect_target_with_hidden_entity_fails_closed_without_partial_dispatch(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One hidden entity in an area prevents action on the entire resolved target."""
    entry = _make_entry(
        "Native Mixed Exposure",
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

    area_registry = ar.async_get(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    area = area_registry.async_create("Mixed Exposure Area")
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("mixed_exposure_test", "device")},
        name="Mixed exposure device",
    )
    device_registry.async_update_device(device.id, area_id=area.id)

    exposed = entity_registry.async_get_or_create(
        domain="light",
        platform="mixed_exposure_test",
        unique_id="mixed-exposure-visible",
        suggested_object_id="mixed_exposure_visible",
        device_id=device.id,
    )
    hidden = entity_registry.async_get_or_create(
        domain="light",
        platform="mixed_exposure_test",
        unique_id="mixed-exposure-hidden",
        suggested_object_id="mixed_exposure_hidden",
        device_id=device.id,
    )
    hass.states.async_set(exposed.entity_id, "off", {"friendly_name": "Visible light"})
    hass.states.async_set(hidden.entity_id, "off", {"friendly_name": "Hidden light"})
    async_expose_entity(hass, conversation.DOMAIN, exposed.entity_id, True)
    async_expose_entity(hass, conversation.DOMAIN, hidden.entity_id, False)
    await hass.async_block_till_done()

    service_calls: list[ServiceCall] = []
    service_resolutions: list[set[str]] = []

    async def service_handler(call: ServiceCall) -> None:
        service_calls.append(call)
        selection = {ATTR_AREA_ID: call.data[ATTR_AREA_ID]}
        referenced = target_helpers.async_extract_referenced_entity_ids(
            hass, target_helpers.TargetSelection(selection)
        )
        resolved = set(referenced.referenced | referenced.indirectly_referenced)
        service_resolutions.append(resolved)
        marker = str(call.data["marker"])
        for entity_id in resolved:
            state = hass.states.get(entity_id)
            if state is not None:
                hass.states.async_set(
                    entity_id,
                    marker,
                    dict(state.attributes),
                    context=call.context,
                )

    hass.services.async_register(_SERVICE_DOMAIN, _SERVICE_NAME, service_handler)

    # Exposure validation happens before service dispatch. A mixed indirect target
    # therefore fails the conversation turn closed instead of sending a tool result
    # back to the provider. The key contract is that no partial action occurs and
    # the same live agent can recover on a later valid turn.
    failing_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "call-mixed-exposure-denied",
                _TOOL_NAME,
                _arguments(area.id, "should-not-run"),
            ),
        ],
    )
    denied = await _say(hass, entry.entry_id, "Mark the mixed exposure area")
    await hass.async_block_till_done()

    assert denied.response.error_code is not None
    assert service_calls == []
    assert service_resolutions == []
    assert hass.states.get(exposed.entity_id).state == "off"
    assert hass.states.get(hidden.entity_id).state == "off"
    assert len(failing_wire.requests) == 1

    # Once every entity in the indirect target is exposed, the exact same area
    # selector may be dispatched. The real service independently resolves the area
    # and proves both entities are included in the successful target union.
    async_expose_entity(hass, conversation.DOMAIN, hidden.entity_id, True)
    await hass.async_block_till_done()

    recovered_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "call-mixed-exposure-allowed",
                _TOOL_NAME,
                _arguments(area.id, "allowed"),
            ),
            _chat_sse_text("The whole area was controlled safely."),
        ],
    )
    allowed = await _say(hass, entry.entry_id, "Try the whole area again")
    await hass.async_block_till_done()

    assert _speech(allowed) == "The whole area was controlled safely."
    assert len(service_calls) == 1
    assert service_resolutions == [{exposed.entity_id, hidden.entity_id}]
    assert hass.states.get(exposed.entity_id).state == "allowed"
    assert hass.states.get(hidden.entity_id).state == "allowed"
    assert len(recovered_wire.requests) == 2

    success = _tool_result(
        recovered_wire.requests[1]["body"], "call-mixed-exposure-allowed"
    )
    assert success["result"][0]["success"] is True
    assert "error" not in success["result"][0]

    configured = next(
        tool
        for tool in agent.subentry.data[CONF_FUNCTION_TOOLS]
        if tool["spec"]["name"] == _TOOL_NAME
    )
    assert configured["enabled"] is True
