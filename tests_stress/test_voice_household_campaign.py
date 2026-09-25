"""Seeded public Assist turns across a changing multi-user voice household."""

from __future__ import annotations

import asyncio
import random
import re
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_ARCHIVE_ENABLED,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
    CONF_VOICE_DEVICE_MAPPINGS,
    CONF_VOICE_SCOPE_POLICY,
    VOICE_POLICY_DEVICE_MAPPING,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    async_get_archive,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import (
    _conversation_subentry,
    _make_entry,
    _setup_entry,
)
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire
from tests_stress.conftest import record

_MARKER = re.compile(r"VOICE_MARKER_[0-9]{4}")


@pytest.mark.asyncio
async def test_voice_household_scopes_survive_concurrency_mapping_edits_and_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_seed: int,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    users = [
        MockUser(id=f"household-user-{index}", name=f"Household {index}")
        for index in range(4)
    ]
    for user in users:
        user.add_to_hass(hass)
    devices = [f"voice-household-device-{index}" for index in range(6)]
    satellites = [f"assist_satellite.household_{index}" for index in range(6)]
    mappings = {devices[index]: users[index].id for index in range(4)}
    mappings[devices[5]] = "deleted-household-user"
    entry = _make_entry(
        "Voice household stress",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: [],
            CONF_ARCHIVE_ENABLED: True,
            CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
            CONF_VOICE_DEVICE_MAPPINGS: mappings,
        },
    )
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    archive = await async_get_archive(hass, entry.entry_id, subentry.subentry_id)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    per_phase = 12 * stress_scale
    wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_text("Household voice answer") for _ in range(per_phase)],
    )
    rng = random.Random(stress_seed ^ 0xA5501CE)
    expected = {user.id: 0 for user in users}
    observed_requests: list[dict[str, Any]] = []

    async def run_turn(
        index: int, origin: int, current_mappings: dict[str, str]
    ) -> None:
        marker = f"VOICE_MARKER_{index:04d}"
        result = await conversation.async_converse(
            hass=hass,
            text=f"Reply to {marker}",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
            device_id=devices[origin],
            satellite_id=satellites[origin],
        )
        assert result.response.error_code is None
        assert (
            "Household voice answer"
            in result.response.as_dict()["speech"]["plain"]["speech"]
        )
        owner = current_mappings.get(devices[origin])
        if owner in expected:
            expected[owner] += 1

    origins = [index % 6 for index in range(per_phase)]
    rng.shuffle(origins)
    for offset in range(0, per_phase, 6):
        await asyncio.gather(
            *(
                run_turn(offset + index, origin, mappings)
                for index, origin in enumerate(origins[offset : offset + 6])
            )
        )
    observed_requests.extend(wire.requests)

    # Reassign one previously unmapped source and one deleted-user mapping.
    updated_mappings = {**mappings, devices[4]: users[2].id, devices[5]: users[3].id}
    hass.config_entries.async_update_subentry(
        entry,
        subentry,
        data={**subentry.data, CONF_VOICE_DEVICE_MAPPINGS: updated_mappings},
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    wire_after = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_text("Household voice answer") for _ in range(per_phase)],
    )
    rng.shuffle(origins)
    for offset in range(0, per_phase, 6):
        await asyncio.gather(
            *(
                run_turn(per_phase + offset + index, origin, updated_mappings)
                for index, origin in enumerate(origins[offset : offset + 6])
            )
        )
    observed_requests.extend(wire_after.requests)
    assert len(observed_requests) == per_phase * 2
    for request in observed_requests:
        markers = set(_MARKER.findall(str(request["body"]["messages"])))
        assert len(markers) == 1, f"Voice request mixed household turns: {markers}"

    archive = await async_get_archive(hass, entry.entry_id, subentry.subentry_id)
    for user in users:
        sessions = await archive.async_list_sessions(f"user:{user.id}", limit=50)
        assert len(sessions["sessions"]) == expected[user.id]
        assert all(
            session["scope_id"] == f"user:{user.id}" for session in sessions["sessions"]
        )
    assert (await archive.async_list_sessions("unretained"))["sessions"] == []
    record(
        stress_trace,
        "summary",
        layer="real-ha + provider-wire",
        voice_household_turns=per_phase * 2,
        voice_household_users=4,
        voice_household_satellites=6,
        voice_mapping_changes=2,
        public_turns=per_phase * 2,
        provider_requests=len(observed_requests),
    )
