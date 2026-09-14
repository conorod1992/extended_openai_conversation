"""Real-HA coverage for multiple overlapping indirect native targets."""

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
from homeassistant.const import ATTR_AREA_ID, ATTR_DEVICE_ID
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
_SERVICE_DOMAIN = "multi_indirect_target_test"
_SERVICE_NAME = "mark"


def _native_execute_service_tool() -> dict[str, Any]:
    """Return the current stock native execute-service tool definition."""
    preset = next(
        item
        for item in BUILT_IN_FUNCTION_PRESETS
        if item["implementation"] == _TOOL_NAME
    )
    tool = deepcopy(preset["tool"])
    tool["enabled"] = True
    return tool


def _arguments(area_id: str, device_id: str, marker: str) -> dict[str, Any]:
    """Build one service request carrying two overlapping indirect selectors."""
    return {
        "list": [
            {
                "domain": _SERVICE_DOMAIN,
                "service": _SERVICE_NAME,
                "service_data": {
                    ATTR_AREA_ID: area_id,
                    ATTR_DEVICE_ID: device_id,
                    "marker": marker,
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


def _tool_result(request: dict[str, Any], call_id: str) -> dict[str, Any]:
    """Decode one real Chat Completions tool-result message."""
    message = next(
        item
        for item in request["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == call_id
    )
    return json.loads(message["content"])


@pytest.mark.asyncio
async def test_multiple_indirect_target_kinds_union_dedupe_and_exposure(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Area+device targets must validate and dispatch their deduplicated union."""
    entry = _make_entry(
        "Multiple Native Indirect Targets",
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

    area = area_registry.async_create("Multi-selector Area")
    device_a = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(_SERVICE_DOMAIN, "device-a")},
        name="Multi selector device A",
    )
    device_b = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(_SERVICE_DOMAIN, "device-b")},
        name="Multi selector device B",
    )
    device_registry.async_update_device(device_a.id, area_id=area.id)
    device_registry.async_update_device(device_b.id, area_id=area.id)

    entity_a = entity_registry.async_get_or_create(
        domain="light",
        platform=_SERVICE_DOMAIN,
        unique_id="multi-indirect-a",
        suggested_object_id="multi_indirect_a",
        device_id=device_a.id,
    )
    entity_b = entity_registry.async_get_or_create(
        domain="light",
        platform=_SERVICE_DOMAIN,
        unique_id="multi-indirect-b",
        suggested_object_id="multi_indirect_b",
        device_id=device_b.id,
    )
    entity_a_id = entity_a.entity_id
    entity_b_id = entity_b.entity_id

    hass.states.async_set(entity_a_id, "idle", {"friendly_name": "Multi A"})
    hass.states.async_set(entity_b_id, "idle", {"friendly_name": "Multi B"})
    async_expose_entity(hass, conversation.DOMAIN, entity_a_id, True)
    async_expose_entity(hass, conversation.DOMAIN, entity_b_id, False)
    await hass.async_block_till_done()

    service_calls: list[ServiceCall] = []
    resolved_per_call: list[list[str]] = []

    async def service_handler(call: ServiceCall) -> None:
        service_calls.append(call)
        selection = target_helpers.TargetSelection(
            {
                ATTR_AREA_ID: call.data.get(ATTR_AREA_ID),
                ATTR_DEVICE_ID: call.data.get(ATTR_DEVICE_ID),
            }
        )
        referenced = target_helpers.async_extract_referenced_entity_ids(hass, selection)
        resolved = sorted(referenced.referenced | referenced.indirectly_referenced)
        resolved_per_call.append(resolved)
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

    hass.services.async_register(
        _SERVICE_DOMAIN,
        _SERVICE_NAME,
        service_handler,
    )

    # area_id resolves A+B while device_id resolves A again. Because B is hidden,
    # policy must reject the complete union before the service sees any subset.
    failing_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "call-multi-indirect-hidden",
                _TOOL_NAME,
                _arguments(area.id, device_a.id, "must-not-run"),
            ),
            _chat_sse_text("One resolved target is not exposed."),
        ],
    )
    failed = await _say(
        hass,
        entry.entry_id,
        "Mark the area and device together",
    )
    await hass.async_block_till_done()

    assert _speech(failed) == "One resolved target is not exposed."
    assert service_calls == []
    assert resolved_per_call == []
    assert hass.states[entity_a_id].state == "idle"
    assert hass.states[entity_b_id].state == "idle"
    assert len(failing_wire.requests) == 2

    failed_result = _tool_result(
        failing_wire.requests[1]["body"], "call-multi-indirect-hidden"
    )
    assert "result" in failed_result
    assert len(failed_result["result"]) == 1
    error = failed_result["result"][0]
    assert "error" in error
    assert entity_b_id in error["error"]

    # Expose B and repeat the identical overlapping selector pair. The service must
    # dispatch exactly once and HA's real resolver must yield each entity once.
    async_expose_entity(hass, conversation.DOMAIN, entity_b_id, True)
    await hass.async_block_till_done()

    success_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "call-multi-indirect-success",
                _TOOL_NAME,
                _arguments(area.id, device_a.id, "combined-success"),
            ),
            _chat_sse_text("Both resolved targets were updated."),
        ],
    )
    succeeded = await _say(
        hass,
        entry.entry_id,
        "Try the combined area and device target again",
    )
    await hass.async_block_till_done()

    assert _speech(succeeded) == "Both resolved targets were updated."
    assert len(service_calls) == 1
    assert service_calls[0].data[ATTR_AREA_ID] == area.id
    assert service_calls[0].data[ATTR_DEVICE_ID] == device_a.id
    assert resolved_per_call == [[entity_a_id, entity_b_id]]
    assert len(resolved_per_call[0]) == len(set(resolved_per_call[0])) == 2
    assert hass.states[entity_a_id].state == "combined-success"
    assert hass.states[entity_b_id].state == "combined-success"
    assert len(success_wire.requests) == 2

    success_result = _tool_result(
        success_wire.requests[1]["body"], "call-multi-indirect-success"
    )
    assert success_result["result"][0]["success"] is True
    assert "error" not in success_result["result"][0]

    configured = next(
        tool
        for tool in agent.subentry.data[CONF_FUNCTION_TOOLS]
        if tool["spec"]["name"] == _TOOL_NAME
    )
    assert configured["enabled"] is True
