"""Identity, privacy, and cross-client continuity hardening."""

from __future__ import annotations

import json

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_ARCHIVE_ENABLED,
    CONF_FUNCTION_TOOLS,
    CONF_MEMORY_MODE,
    CONF_TEMPORARY_MEMORY,
    CONF_VOICE_DEVICE_MAPPINGS,
    CONF_VOICE_SCOPE_POLICY,
    MEMORY_MODE_MANUAL,
    TEMPORARY_MEMORY_BALANCED,
    VOICE_POLICY_DEVICE_MAPPING,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import (
    _conversation_subentry,
    _make_entry,
    _setup_entry,
)
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire, _speech
from tests_real_ha.test_user_ownership_privacy import (
    _management_call,
    _normal_user_token,
    _seed_personal_data,
)
from tests_stress.conftest import record


@pytest.mark.asyncio
async def test_recreated_visible_identity_and_live_voice_remap_do_not_inherit_private_state(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    old_user = MockUser(id="continuity-old-user", name="Same Visible Person")
    old_user.add_to_hass(hass)
    device = "continuity-kitchen-device"
    entry = _make_entry(
        "Identity continuity",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_FUNCTION_TOOLS: [],
            CONF_ARCHIVE_ENABLED: True,
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
            CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
            CONF_VOICE_DEVICE_MAPPINGS: {device: old_user.id},
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    await agent._memory.async_add(
        old_user.id,
        "Private identity token is OLD_IDENTITY_SECRET.",
        "identity",
        "explicit",
    )

    first_wire = _install_wire(monkeypatch, agent, [_chat_sse_text("old identity")])
    first = await conversation.async_converse(
        hass=hass,
        text="What is my private identity token?",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
        device_id=device,
        satellite_id="assist_satellite.identity_kitchen",
    )
    assert _speech(first) == "old identity"
    assert "OLD_IDENTITY_SECRET" in json.dumps(first_wire.requests[0]["body"])
    conversation_id = first.conversation_id
    assert conversation_id

    await hass.auth.async_remove_user(old_user)
    replacement = MockUser(id="continuity-new-user", name="Same Visible Person")
    replacement.add_to_hass(hass)
    await agent._memory.async_add(
        replacement.id,
        "Private identity token is NEW_IDENTITY_SECRET.",
        "identity",
        "explicit",
    )

    # Same visible person/device, but the mapping still targets the deleted HA ID.
    stale_wire = _install_wire(monkeypatch, agent, [_chat_sse_text("stale mapping")])
    stale = await conversation.async_converse(
        hass=hass,
        text="What is my private identity token now?",
        conversation_id=conversation_id,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
        device_id=device,
        satellite_id="assist_satellite.identity_kitchen",
    )
    assert _speech(stale) == "stale mapping"
    stale_body = json.dumps(stale_wire.requests[0]["body"])
    assert "OLD_IDENTITY_SECRET" not in stale_body
    assert "NEW_IDENTITY_SECRET" not in stale_body

    subentry = _conversation_subentry(entry)
    hass.config_entries.async_update_subentry(
        entry,
        subentry,
        data={
            **subentry.data,
            CONF_VOICE_DEVICE_MAPPINGS: {device: replacement.id},
        },
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    remapped_wire = _install_wire(monkeypatch, agent, [_chat_sse_text("new identity")])
    remapped = await conversation.async_converse(
        hass=hass,
        text="What is my private identity token after the remap?",
        conversation_id=conversation_id,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
        device_id=device,
        satellite_id="assist_satellite.identity_kitchen",
    )
    assert _speech(remapped) == "new identity"
    body = json.dumps(remapped_wire.requests[0]["body"])
    assert "NEW_IDENTITY_SECRET" in body
    assert "OLD_IDENTITY_SECRET" not in body
    record(
        stress_trace,
        "summary",
        layer="Real HA Assist + provider wire",
        identity_recreations=1,
        live_voice_remaps=1,
        continued_conversation_turns=3,
        private_identity_probes=3,
    )


@pytest.mark.asyncio
async def test_language_change_inside_continuity_keeps_user_boundary(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    alice = MockUser(id="language-alice", name="Alice")
    bob = MockUser(id="language-bob", name="Bob")
    alice.add_to_hass(hass)
    bob.add_to_hass(hass)
    entry = _make_entry(
        "Language continuity",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_FUNCTION_TOOLS: [],
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    await agent._memory.async_add(alice.id, "Language continuity token is ALICE_LANGUAGE_PRIVATE", "identity", "explicit")
    await agent._memory.async_add(bob.id, "Language continuity token is BOB_LANGUAGE_PRIVATE", "identity", "explicit")

    wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_text("English"), _chat_sse_text("Français"), _chat_sse_text("Bob")],
    )
    first = await conversation.async_converse(
        hass=hass,
        text="What is my language continuity token in English?",
        conversation_id=None,
        context=Context(user_id=alice.id),
        language="en",
        agent_id=entry.entry_id,
    )
    assert first.conversation_id
    second = await conversation.async_converse(
        hass=hass,
        text="Language continuity token en français.",
        conversation_id=first.conversation_id,
        context=Context(user_id=alice.id),
        language="fr",
        agent_id=entry.entry_id,
    )
    third = await conversation.async_converse(
        hass=hass,
        text="What is my language continuity token as Bob?",
        conversation_id=first.conversation_id,
        context=Context(user_id=bob.id),
        language="de",
        agent_id=entry.entry_id,
    )
    assert _speech(first) == "English"
    assert _speech(second) == "Français"
    assert _speech(third) == "Bob"
    assert second.conversation_id == first.conversation_id
    assert third.conversation_id != first.conversation_id

    bodies = [json.dumps(request["body"], ensure_ascii=False) for request in wire.requests]
    assert "ALICE_LANGUAGE_PRIVATE" in bodies[0]
    assert "ALICE_LANGUAGE_PRIVATE" in bodies[1]
    assert "BOB_LANGUAGE_PRIVATE" not in bodies[1]
    assert "BOB_LANGUAGE_PRIVATE" in bodies[2]
    assert "ALICE_LANGUAGE_PRIVATE" not in bodies[2]
    record(
        stress_trace,
        "summary",
        layer="Real HA Assist + provider wire",
        request_languages=3,
        continuity_language_changes=1,
        cross_user_collision_probes=1,
    )


@pytest.mark.asyncio
async def test_long_lived_user_clients_remain_private_across_entry_recreation(
    hass: HomeAssistant,
    hass_ws_client,
    stress_trace: list[dict],
) -> None:
    entry = _make_entry(
        "Cross-client lifecycle",
        include_ai_task=False,
        conversation_options={
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_ARCHIVE_ENABLED: True,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
        },
    )
    await _setup_entry(hass, entry)
    alice, alice_token = await _normal_user_token(hass, "lifecycle-alice", "Alice")
    bob, bob_token = await _normal_user_token(hass, "lifecycle-bob", "Bob")
    await _seed_personal_data(
        hass,
        entry,
        user=alice,
        memory_text="ALICE_LIFECYCLE_PRIVATE",
        conversation_text="Alice lifecycle archive",
    )
    await _seed_personal_data(
        hass,
        entry,
        user=bob,
        memory_text="BOB_LIFECYCLE_PRIVATE",
        conversation_text="Bob lifecycle archive",
    )
    alice_client = await hass_ws_client(hass, alice_token)
    bob_client = await hass_ws_client(hass, bob_token)

    before_a = await _management_call(alice_client, entry=entry, section="memories", action="list")
    before_b = await _management_call(bob_client, entry=entry, section="memories", action="list")
    assert [item["content"] for item in before_a["result"]["memories"]] == ["ALICE_LIFECYCLE_PRIVATE"]
    assert [item["content"] for item in before_b["result"]["memories"]] == ["BOB_LIFECYCLE_PRIVATE"]

    # Exercise the same authenticated websocket clients across a full integration
    # unload/setup lifecycle. Their browser-equivalent connection identity must not
    # be rebound to another HA user or expose cached results from the prior load.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    after_a = await _management_call(alice_client, entry=entry, section="memories", action="list")
    after_b = await _management_call(bob_client, entry=entry, section="memories", action="list")
    assert [item["content"] for item in after_a["result"]["memories"]] == ["ALICE_LIFECYCLE_PRIVATE"]
    assert [item["content"] for item in after_b["result"]["memories"]] == ["BOB_LIFECYCLE_PRIVATE"]
    assert after_a["result"]["scope_id"] == f"user:{alice.id}"
    assert after_b["result"]["scope_id"] == f"user:{bob.id}"
    record(
        stress_trace,
        "summary",
        layer="Real HA management websocket",
        authenticated_clients=2,
        entry_recreations=1,
        private_scope_rechecks=4,
    )
