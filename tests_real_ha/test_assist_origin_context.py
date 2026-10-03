"""Real-HA acceptance for Assist device and satellite origin metadata."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry


@pytest.mark.asyncio
async def test_public_assist_preserves_real_device_and_satellite_origin_context(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assist origin metadata reaches the loaded agent and HA LLM context unchanged."""
    entry = _make_entry(
        "Assist Origin",
        include_ai_task=False,
        local_intents=True,
    )
    await _setup_entry(hass, entry)

    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(agent, ExtendedOpenAIAgentEntity)

    # Build a genuine HA device/entity relationship representing the Assist satellite
    # that originated the request rather than passing an arbitrary device identifier.
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("test_assist_origin", "kitchen-satellite-device")},
        name="Kitchen Assist Satellite",
    )
    entity_registry = er.async_get(hass)
    satellite = entity_registry.async_get_or_create(
        "assist_satellite",
        "test_assist_origin",
        "kitchen-satellite",
        suggested_object_id="kitchen_satellite",
        device_id=device.id,
    )
    assert satellite.device_id == device.id

    provider_path = AsyncMock(
        side_effect=AssertionError("local intent unexpectedly reached the provider")
    )
    monkeypatch.setattr(agent, "_async_handle_message_with_ha_tools", provider_path)

    original_process = agent._async_process
    observed: dict[str, Any] = {}

    async def capture_origin(
        user_input: conversation.ConversationInput,
    ) -> conversation.ConversationResult:
        observed["device_id"] = user_input.device_id
        observed["satellite_id"] = user_input.satellite_id
        observed["context"] = user_input.context
        llm_context = user_input.as_llm_context(conversation.DOMAIN)
        observed["llm_device_id"] = llm_context.device_id
        observed["llm_context"] = llm_context.context
        return await original_process(user_input)

    monkeypatch.setattr(agent, "_async_process", capture_origin)

    request_context = Context(user_id="assist-origin-user")
    result = await conversation.async_converse(
        hass=hass,
        text="what time is it",
        conversation_id=None,
        context=request_context,
        language="en",
        agent_id=entry.entry_id,
        device_id=device.id,
        satellite_id=satellite.entity_id,
    )

    assert result.response.error_code is None
    provider_path.assert_not_awaited()
    assert observed == {
        "device_id": device.id,
        "satellite_id": satellite.entity_id,
        "context": request_context,
        "llm_device_id": device.id,
        "llm_context": request_context,
    }

    # The origin relationship itself must still be the real registry relationship
    # after the request; processing must not rewrite or detach the satellite entity.
    restored_satellite = entity_registry.async_get(satellite.entity_id)
    assert restored_satellite is not None
    assert restored_satellite.device_id == device.id


@pytest.mark.parametrize(
    "satellite_area", [True, False], ids=["satellite-room", "device-room-fallback"]
)
async def test_voice_user_mapping_keeps_physical_room_for_local_light_command(
    hass, monkeypatch, satellite_area
):
    """Room targets use the satellite origin while retained data uses its device user."""
    from pytest_homeassistant_custom_component.common import MockUser

    from custom_components.extended_openai_conversation_responses.const import (
        API_MODE_CHAT_COMPLETIONS,
        CONF_API_MODE,
        CONF_ARCHIVE_ENABLED,
        CONF_CHAT_MODEL,
        CONF_CONVERSATION_CONTINUITY,
        CONF_FUNCTION_TOOLS,
        CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
        CONF_MEMORY_MODE,
        CONF_VOICE_DEVICE_MAPPINGS,
        CONF_VOICE_SCOPE_POLICY,
        VOICE_POLICY_DEVICE_MAPPING,
    )
    from custom_components.extended_openai_conversation_responses.conversation_archive import (
        async_get_archive,
    )
    from custom_components.extended_openai_conversation_responses.memory import (
        async_get_memory,
    )
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
    )
    from homeassistant.helpers import area_registry as ar
    from tests_real_ha.test_provider_wire_e2e import (
        _chat_sse_text,
        _install_wire,
        _speech,
    )

    device_user = MockUser(
        id="physical-room-device-user", name="Device-associated user"
    )
    device_user.add_to_hass(hass)
    satellite_user = MockUser(
        id="physical-room-satellite-user", name="Other retained user"
    )
    satellite_user.add_to_hass(hass)
    entry = _make_entry(
        "Voice physical room",
        include_ai_task=False,
        local_intents=True,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: [],
            CONF_CONVERSATION_CONTINUITY: "ha_default",
            CONF_ARCHIVE_ENABLED: True,
            CONF_MEMORY_MODE: "automatic",
            CONF_MEMORY_AUTO_RETRIEVE_LIMIT: 3,
            CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
        },
    )
    await _setup_entry(hass, entry)
    areas, devices, entities = (
        ar.async_get(hass),
        dr.async_get(hass),
        er.async_get(hass),
    )
    kitchen, hall = areas.async_create("Kitchen"), areas.async_create("Hall")
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("voice_physical_room", "parent")},
        name="Hall parent device",
    )
    devices.async_update_device(device.id, area_id=hall.id)
    satellite = entities.async_get_or_create(
        "assist_satellite", "voice_physical_room", "satellite", device_id=device.id
    )
    if satellite_area:
        entities.async_update_entity(satellite.entity_id, area_id=kitchen.id)
    hass.states.async_set(satellite.entity_id, "idle")
    lights = {}
    for area in (kitchen, hall):
        light = entities.async_get_or_create(
            "light",
            "voice_physical_room",
            area.id,
            suggested_object_id=f"physical_room_{area.name.lower()}",
        )
        entities.async_update_entity(light.entity_id, area_id=area.id)
        hass.states.async_set(
            light.entity_id, "on", {"friendly_name": f"{area.name} lights"}
        )
        async_expose_entity(hass, conversation.DOMAIN, light.entity_id, True)
        lights[area.id] = light.entity_id
    subentry = next(iter(entry.subentries.values()))
    hass.config_entries.async_update_subentry(
        entry,
        subentry,
        data={
            **subentry.data,
            CONF_VOICE_DEVICE_MAPPINGS: {
                device.id: device_user.id,
                satellite.entity_id: satellite_user.id,
            },
        },
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, entry.entry_id)
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    await memory.async_add(
        device_user.id,
        "The physical room owner marker is cobalt.",
        "general",
        "explicit",
    )
    await memory.async_add(
        satellite_user.id,
        "The physical room owner marker is crimson.",
        "general",
        "explicit",
    )
    service_calls = []

    async def turn_off(call):
        service_calls.append((call.domain, call.service, dict(call.data)))
        for entity_id in call.data["entity_id"]:
            hass.states.async_set(
                entity_id, "off", dict(hass.states.get(entity_id).attributes)
            )

    hass.services.async_register("light", "turn_off", turn_off)
    wire = _install_wire(
        monkeypatch, agent, [_chat_sse_text("Device user retained correctly")]
    )
    local = await conversation.async_converse(
        hass=hass,
        text="turn off the lights",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
        device_id=device.id,
        satellite_id=satellite.entity_id,
    )
    assert local.response.error_code is None
    expected_area = kitchen if satellite_area else hall
    expected_light = lights[expected_area.id]
    assert service_calls == [("light", "turn_off", {"entity_id": [expected_light]})]
    assert hass.states.get(expected_light).state == "off"
    other_light = lights[(hall if satellite_area else kitchen).id]
    assert hass.states.get(other_light).state == "on"
    assert wire.requests == []
    retained = await conversation.async_converse(
        hass=hass,
        text="What is my physical room owner marker?",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
        device_id=device.id,
        satellite_id=satellite.entity_id,
    )
    assert _speech(retained) == "Device user retained correctly"
    import json

    payload = json.dumps(wire.requests[0]["body"])
    assert "The physical room owner marker is cobalt." in payload
    assert "The physical room owner marker is crimson." not in payload
    archive = await async_get_archive(hass, entry.entry_id, subentry.subentry_id)
    assert (await archive.async_list_sessions(f"user:{device_user.id}"))["sessions"]
    assert (await archive.async_list_sessions(f"user:{satellite_user.id}"))[
        "sessions"
    ] == []
