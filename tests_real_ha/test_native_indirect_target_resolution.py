"""Real-HA acceptance for native Function Tool indirect target resolution."""

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
)
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
)
from tests_real_ha.test_provider_wire_e2e import _install_wire, _speech

_TOOL_NAME = "execute_service"
_SERVICE_DOMAIN = "indirect_target_test"
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


def _arguments(target: dict[str, str], marker: str) -> dict[str, Any]:
    return {
        "list": [
            {
                "domain": _SERVICE_DOMAIN,
                "service": _SERVICE_NAME,
                "service_data": {**target, "marker": marker},
            }
        ]
    }


async def _say(
    hass: HomeAssistant,
    entry_id: str,
    text: str,
) -> conversation.ConversationResult:
    """Enter through Home Assistant's public Assist conversation API."""
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry_id,
    )


def _tool_result(body: dict[str, Any]) -> dict[str, Any]:
    """Decode the real Chat Completions tool-result message."""
    message = next(item for item in body["messages"] if item.get("role") == "tool")
    return json.loads(message["content"])


@pytest.mark.asyncio
async def test_native_area_and_device_targets_follow_live_ha_registries(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Area/device selectors must resolve current HA registry state on every call."""
    entry = _make_entry(
        "Native Indirect Targets",
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

    area_a = area_registry.async_create("Indirect Area A")
    area_b = area_registry.async_create("Indirect Area B")
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("indirect_target_test", "device-a")},
        name="Indirect target device",
    )
    device = device_registry.async_update_device(device.id, area_id=area_a.id)
    entity = entity_registry.async_get_or_create(
        domain="light",
        platform="indirect_target_test",
        unique_id="native-indirect-target-light",
        suggested_object_id="native_indirect_target",
        device_id=device.id,
    )
    entity_id = entity.entity_id
    hass.states.async_set(entity_id, "off", {"friendly_name": "Indirect target light"})
    async_expose_entity(hass, conversation.DOMAIN, entity_id, True)
    await hass.async_block_till_done()

    service_resolutions: list[tuple[str, list[str]]] = []

    async def service_handler(call: ServiceCall) -> None:
        marker = str(call.data["marker"])
        service_resolutions.append((marker, [entity_id]))
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

    async def run_success(
        call_id: str,
        target: dict[str, str],
        marker: str,
        final_text: str,
    ) -> None:
        wire = _install_wire(
            monkeypatch,
            agent,
            [
                _chat_sse_tool_call(
                    call_id,
                    _TOOL_NAME,
                    _arguments(target, marker),
                ),
                _chat_sse_text(final_text),
            ],
        )
        result = await _say(hass, entry.entry_id, f"Run {marker}")
        await hass.async_block_till_done()
        assert _speech(result) == final_text
        assert len(wire.requests) == 2
        tool_result = _tool_result(wire.requests[1]["body"])
        assert tool_result["result"][0]["success"] is True
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == marker

    async def run_resolution_failure(
        call_id: str,
        target: dict[str, str],
        marker: str,
    ) -> None:
        before = list(service_resolutions)
        wire = _install_wire(
            monkeypatch,
            agent,
            [
                _chat_sse_tool_call(
                    call_id,
                    _TOOL_NAME,
                    _arguments(target, marker),
                )
            ],
        )
        result = await _say(hass, entry.entry_id, f"Run {marker}")
        await hass.async_block_till_done()
        assert result.response.error_code is not None
        assert len(wire.requests) == 1
        assert service_resolutions == before

    await run_success(
        "call-area-a-initial",
        {ATTR_AREA_ID: area_a.id},
        "area-a-initial",
        "Area A resolved correctly.",
    )
    assert service_resolutions[-1] == ("area-a-initial", [entity_id])

    device_registry.async_update_device(device.id, area_id=area_b.id)
    await hass.async_block_till_done()
    await run_resolution_failure(
        "call-area-a-stale",
        {ATTR_AREA_ID: area_a.id},
        "area-a-stale",
    )

    await run_success(
        "call-area-b-current",
        {ATTR_AREA_ID: area_b.id},
        "area-b-current",
        "Area B now resolves correctly.",
    )
    assert service_resolutions[-1] == ("area-b-current", [entity_id])

    await run_success(
        "call-device-current",
        {ATTR_DEVICE_ID: device.id},
        "device-current",
        "The device target resolved correctly.",
    )
    assert service_resolutions[-1] == ("device-current", [entity_id])

    entity_registry.async_update_entity(entity_id, device_id=None)
    await hass.async_block_till_done()
    await run_resolution_failure(
        "call-device-detached",
        {ATTR_DEVICE_ID: device.id},
        "device-detached",
    )

    entity_registry.async_update_entity(entity_id, device_id=device.id)
    await hass.async_block_till_done()
    await run_success(
        "call-device-reattached",
        {ATTR_DEVICE_ID: device.id},
        "device-reattached",
        "The reattached device resolves again.",
    )
    assert service_resolutions[-1] == ("device-reattached", [entity_id])

    configured = next(
        tool
        for tool in agent.subentry.data[CONF_FUNCTION_TOOLS]
        if tool["spec"]["name"] == _TOOL_NAME
    )
    assert configured["enabled"] is True
