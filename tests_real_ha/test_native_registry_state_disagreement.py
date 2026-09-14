"""Real-HA acceptance for native targeting when registry and state disagree."""

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
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er

from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
)
from tests_real_ha.test_provider_wire_e2e import _install_wire, _speech

_TOOL_NAME = "execute_service"
_SERVICE_DOMAIN = "registry_state_target_test"
_SERVICE_NAME = "mark"
_ENTITY_ID = "light.registry_state_target"


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


def _arguments(target: dict[str, Any], marker: str) -> dict[str, Any]:
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
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry_id,
    )


def _tool_result(body: dict[str, Any], call_id: str) -> dict[str, Any]:
    message = next(
        item
        for item in body["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == call_id
    )
    return json.loads(message["content"])


@pytest.mark.asyncio
async def test_native_targets_fail_safely_when_registry_and_state_disagree(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Indirect targets require live registry plus state; direct IDs follow live state."""
    entry = _make_entry(
        "Registry State Disagreement",
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

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("registry_state_target_test", "device")},
        name="Registry/state disagreement device",
    )
    registry_entry = entity_registry.async_get_or_create(
        domain="light",
        platform="registry_state_target_test",
        unique_id="registry-state-target",
        suggested_object_id="registry_state_target",
        device_id=device.id,
    )
    assert registry_entry.entity_id == _ENTITY_ID

    hass.states.async_set(
        _ENTITY_ID,
        "off",
        {"friendly_name": "Registry/state disagreement target"},
    )
    async_expose_entity(hass, conversation.DOMAIN, _ENTITY_ID, True)
    await hass.async_block_till_done()

    service_calls: list[dict[str, Any]] = []

    async def service_handler(call: ServiceCall) -> None:
        service_calls.append(dict(call.data))
        state = hass.states.get(_ENTITY_ID)
        if state is not None:
            hass.states.async_set(
                _ENTITY_ID,
                str(call.data["marker"]),
                dict(state.attributes),
                context=call.context,
            )

    hass.services.async_register(_SERVICE_DOMAIN, _SERVICE_NAME, service_handler)

    async def run_success(
        *,
        call_id: str,
        target: dict[str, Any],
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
        tool_result = _tool_result(wire.requests[1]["body"], call_id)
        assert tool_result["result"][0]["success"] is True
        state = hass.states.get(_ENTITY_ID)
        assert state is not None
        assert state.state == marker

    async def run_failure(
        *,
        call_id: str,
        target: dict[str, Any],
        marker: str,
    ) -> None:
        before = list(service_calls)
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
        assert service_calls == before

    # Establish that the real device-registry route works while both HA views agree.
    await run_success(
        call_id="call-registry-state-baseline",
        target={ATTR_DEVICE_ID: device.id},
        marker="baseline",
        final_text="The registered target worked.",
    )
    assert service_calls[-1][ATTR_DEVICE_ID] == device.id

    # Disagreement A: the registry still says the entity belongs to this device, but
    # the StateMachine no longer has it. Indirect resolution may find the registry
    # row, but native validation must reject the stale target before service dispatch.
    hass.states.async_remove(_ENTITY_ID)
    await hass.async_block_till_done()
    assert entity_registry.async_get(_ENTITY_ID) is not None
    assert hass.states.get(_ENTITY_ID) is None

    await run_failure(
        call_id="call-registry-without-state",
        target={ATTR_DEVICE_ID: device.id},
        marker="registry-without-state",
    )

    # Restore the live state, then create the inverse disagreement: a valid exposed
    # StateMachine entity whose registry row has disappeared. Device targeting must
    # not invent a relationship from stale state alone.
    hass.states.async_set(
        _ENTITY_ID,
        "off",
        {"friendly_name": "Registry/state disagreement target"},
    )
    async_expose_entity(hass, conversation.DOMAIN, _ENTITY_ID, True)
    entity_registry.async_remove(_ENTITY_ID)
    await hass.async_block_till_done()
    assert hass.states.get(_ENTITY_ID) is not None
    assert entity_registry.async_get(_ENTITY_ID) is None

    await run_failure(
        call_id="call-state-without-registry-indirect",
        target={ATTR_DEVICE_ID: device.id},
        marker="state-without-registry-indirect",
    )

    # The same state-only entity remains a valid explicit target because direct IDs
    # are governed by live state plus Assist exposure, not a guessed registry link.
    await run_success(
        call_id="call-state-without-registry-direct",
        target={ATTR_ENTITY_ID: [_ENTITY_ID]},
        marker="state-without-registry-direct",
        final_text="The explicit live target still worked.",
    )
    assert service_calls[-1][ATTR_ENTITY_ID] == [_ENTITY_ID]

    # Neither disagreement is allowed to mutate or disable the configured native tool.
    configured = next(
        tool
        for tool in agent.subentry.data[CONF_FUNCTION_TOOLS]
        if tool["spec"]["name"] == _TOOL_NAME
    )
    assert configured["enabled"] is True
