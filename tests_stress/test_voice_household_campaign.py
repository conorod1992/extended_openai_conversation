"""Seeded public Assist turns across a changing multi-user voice household."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
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
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_MEMORY_MODE,
    CONF_TEMPORARY_MEMORY,
    CONF_VOICE_DEVICE_MAPPINGS,
    CONF_VOICE_SCOPE_POLICY,
    MEMORY_MODE_MANUAL,
    TEMPORARY_MEMORY_BALANCED,
    VOICE_POLICY_DEVICE_MAPPING,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    async_get_archive,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
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
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_MEMORY_AUTO_RETRIEVE_LIMIT: 3,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
            CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
            CONF_VOICE_DEVICE_MAPPINGS: mappings,
        },
    )
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    archive = await async_get_archive(hass, entry.entry_id, subentry.subentry_id)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    assert agent._memory is not None
    assert agent._temporary_memory is not None
    persistent_markers = {
        user.id: f"HOUSEHOLD_PRIVATE_{index}_{stress_seed}"
        for index, user in enumerate(users)
    }
    temporary_markers = {
        user.id: f"HOUSEHOLD_TEMPORARY_{index}_{stress_seed}"
        for index, user in enumerate(users)
    }
    expiry = (dt_util.utcnow() + timedelta(hours=2)).isoformat()
    for user in users:
        await agent._memory.async_add(
            user.id,
            f"My household calibration token is {persistent_markers[user.id]}.",
            "preferences",
            "explicit",
        )
        scope = f"user:{user.id}"
        await agent._temporary_memory.async_add(
            scope,
            f"My temporary household calibration token is {temporary_markers[user.id]}.",
            expiry,
            "household",
            owner_scope_id=scope,
        )

    per_phase = 12 * stress_scale
    wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_text("Household voice answer") for _ in range(per_phase)],
    )
    rng = random.Random(stress_seed ^ 0xA5501CE)
    expected = {user.id: 0 for user in users}
    active_user_ids = set(expected)
    owners_by_marker: dict[str, str | None] = {}
    observed_requests: list[dict[str, Any]] = []

    async def run_turn(
        index: int, origin: int, current_mappings: dict[str, str]
    ) -> None:
        marker = f"VOICE_MARKER_{index:04d}"
        owner = current_mappings.get(devices[origin])
        owners_by_marker[marker] = owner if owner in active_user_ids else None
        result = await conversation.async_converse(
            hass=hass,
            text=f"What is my household calibration token? Reply to {marker}",
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
        if owner in active_user_ids:
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

    # The fourth user's old sessions remain owned by that identity, while new
    # voice turns pointing to the deleted HA user must become unretained.
    await hass.auth.async_remove_user(users[3])
    active_user_ids.remove(users[3].id)
    # Reassign the unmapped source; keep another mapping pointed at the deleted
    # user so both previously valid and newly stale devices are exercised.
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
        body = json.dumps(request["body"], ensure_ascii=False)
        markers = set(_MARKER.findall(body))
        assert len(markers) == 1, f"Voice request mixed household turns: {markers}"
        owner = owners_by_marker[next(iter(markers))]
        for user in users:
            for private_marker in (
                persistent_markers[user.id],
                temporary_markers[user.id],
            ):
                assert (private_marker in body) is (owner == user.id), (
                    owner,
                    user.id,
                    private_marker,
                )

    # A temporary Guest transition must hide every personal marker without
    # destroying the underlying Memory data used by later owner requests.
    await agent._guest_mode.async_update_trusted(indefinite=True)
    guest_wire = _install_wire(
        monkeypatch, agent, [_chat_sse_text("Household guest answer")]
    )
    guest_result = await conversation.async_converse(
        hass=hass,
        text="What is my household calibration token? Guest marker probe.",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
        device_id=devices[0],
        satellite_id=satellites[0],
    )
    assert guest_result.response.error_code is None
    assert len(guest_wire.requests) == 1
    guest_body = json.dumps(guest_wire.requests[0]["body"], ensure_ascii=False)
    assert all(marker not in guest_body for marker in persistent_markers.values())
    assert all(marker not in guest_body for marker in temporary_markers.values())
    await agent._guest_mode.async_disable_trusted()
    owner_wire = _install_wire(
        monkeypatch, agent, [_chat_sse_text("Household owner answer")]
    )
    owner_result = await conversation.async_converse(
        hass=hass,
        text="What is my household calibration token? Restored owner probe.",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
        device_id=devices[0],
        satellite_id=satellites[0],
    )
    assert owner_result.response.error_code is None
    assert len(owner_wire.requests) == 1
    owner_body = json.dumps(owner_wire.requests[0]["body"], ensure_ascii=False)
    assert persistent_markers[users[0].id] in owner_body
    assert temporary_markers[users[0].id] in owner_body
    assert all(
        marker not in owner_body
        for user in users[1:]
        for marker in (persistent_markers[user.id], temporary_markers[user.id])
    )
    expected[users[0].id] += 1

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
        voice_user_deletions=1,
        voice_private_context_probes=len(observed_requests) + 1,
        voice_temporary_context_probes=len(observed_requests) + 1,
        voice_guest_context_probes=1,
        public_turns=per_phase * 2 + 2,
        provider_requests=len(observed_requests) + 2,
    )
