"""Nightly safety for caller-supplied conversation-ID collisions."""

from __future__ import annotations

import asyncio
import json

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_MODE,
    CONF_CONVERSATION_CONTINUITY,
    CONF_VOICE_SCOPE_POLICY,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.live_subentry_updates import (
    update_live_subentry,
)
from homeassistant.components import conversation
from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _raw_client,
    _responses_sse_text,
)
from tests_stress.conftest import record


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
@pytest.mark.parametrize(
    "core_expired", [False, True], ids=["warm-core", "expired-core"]
)
async def test_device_continuity_owner_transition_isolated_on_provider_wire(
    hass, monkeypatch, stress_trace, api_mode, core_expired
):
    users = [
        MockUser(id="continuity-alice", name="Alice"),
        MockUser(id="continuity-bob", name="Bob"),
    ]
    for user in users:
        user.add_to_hass(hass)
    entry = _make_entry(
        "Continuity privacy",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: api_mode,
            CONF_CONVERSATION_CONTINUITY: "device",
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    reply = _chat_sse_text if api_mode == "chat_completions" else _responses_sse_text
    wire = _install_wire(monkeypatch, agent, [reply(f"reply-{i}") for i in range(6)])
    markers = [
        "OWNER_ALICE_MARKER",
        "OWNER_BOB_MARKER",
        "SHARED_MARKER",
        "UNRETAINED_MARKER",
    ]
    scopes = [
        (users[0].id, "unretained"),
        (users[1].id, "unretained"),
        (None, "shared"),
        (None, "unretained"),
        (users[0].id, "unretained"),
    ]
    ids = []
    for index, (user_id, policy) in enumerate(scopes):
        current = next(iter(entry.subentries.values()))
        update_live_subentry(
            hass, entry, current, data={**current.data, CONF_VOICE_SCOPE_POLICY: policy}
        )
        if core_expired:
            hass.data.get(DATA_CHAT_LOGS, {}).clear()
        marker = markers[index] if index < 4 else "ALICE_RETURN_MARKER"
        result = await conversation.async_converse(
            hass=hass,
            text=marker,
            conversation_id=ids[-1] if ids else None,
            context=Context(user_id=user_id),
            device_id="same-physical-device",
            language="en",
            agent_id=entry.entry_id,
        )
        assert result.response.error_code is None
        ids.append(result.conversation_id)
        body = json.dumps(wire.requests[-1]["body"])
        for prior_index, prior in enumerate(markers):
            if prior_index != index and not (index == 4 and prior_index == 0):
                assert prior not in body
        if index == 4:
            assert markers[0] in body
    assert len(set(ids[:4])) == 4
    assert ids[4] == ids[0]
    result = await conversation.async_converse(
        hass=hass,
        text="Alice follow-up",
        conversation_id=ids[-1],
        context=Context(user_id=users[0].id),
        device_id="same-physical-device",
        language="en",
        agent_id=entry.entry_id,
    )
    assert result.response.error_code is None
    assert "ALICE_RETURN_MARKER" in json.dumps(wire.requests[-1]["body"])
    record(
        stress_trace,
        "summary",
        continuity_owner_transitions=4,
        continuity_wire_checks=6,
    )


@pytest.mark.parametrize("mode", ["ha_default", "device", "user"])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel-waiter"])
async def test_effective_ha_default_overlap_preserves_completed_turns(
    hass, monkeypatch, stress_trace, mode, outcome
):
    entry = _make_entry(
        "Continuity overlap",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: "chat_completions",
            CONF_CONVERSATION_CONTINUITY: mode,
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    replies = [
        _chat_sse_text("BASE_REPLY"),
        (
            400,
            {
                "error": {
                    "message": "controlled failure",
                    "type": "invalid_request_error",
                }
            },
        )
        if outcome == "error"
        else _chat_sse_text("FIRST_REPLY"),
        _chat_sse_text("NEXT_REPLY"),
        _chat_sse_text("FINAL_REPLY"),
    ]
    wire = _install_wire(monkeypatch, agent, replies)

    async def say(text):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id="same-ha-log",
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )

    assert (await say("BASE_TURN")).response.error_code is None
    send = wire.send
    entered, release = asyncio.Event(), asyncio.Event()
    sends = 0

    async def gated_send(request, *args, **kwargs):
        nonlocal sends
        sends += 1
        if sends == 1:
            entered.set()
            await release.wait()
        return await send(request, *args, **kwargs)

    monkeypatch.setattr(_raw_client(agent)._client, "send", gated_send)
    first = asyncio.create_task(say("FIRST_TURN"))
    await asyncio.wait_for(entered.wait(), 10)
    waiting = asyncio.create_task(say("SECOND_TURN"))
    try:
        await hass.async_block_till_done()
        assert not waiting.done()
        assert sends == 1
        if outcome == "cancel-waiter":
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
        release.set()
        result = await first
        assert (result.response.error_code is None) == (outcome != "error")
        if outcome != "cancel-waiter":
            assert (await waiting).response.error_code is None
            second_body = json.dumps(wire.requests[-1]["body"])
            assert "BASE_REPLY" in second_body
            if outcome == "success":
                assert "FIRST_REPLY" in second_body
        assert (await say("THIRD_TURN")).response.error_code is None
        final = json.dumps(wire.requests[-1]["body"])
        assert "BASE_REPLY" in final
        if outcome != "error":
            assert "FIRST_REPLY" in final
        if outcome != "cancel-waiter":
            assert "SECOND_TURN" in final and "NEXT_REPLY" in final
        assert not agent._continuity._ha_default_locks
    finally:
        release.set()
        for task in (first, waiting):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, waiting, return_exceptions=True)
    record(stress_trace, "summary", continuity_serialized_overlaps=1)


@pytest.mark.asyncio
async def test_same_conversation_id_cannot_cross_user_or_agent_boundaries(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    """A caller-selected HA conversation ID is owned by one agent/scope boundary."""
    users = [
        MockUser(id="collision-user-a", name="Collision A"),
        MockUser(id="collision-user-b", name="Collision B"),
    ]
    for user in users:
        user.add_to_hass(hass)

    entries = [
        _make_entry("Collision agent A", include_ai_task=False),
        _make_entry("Collision agent B", include_ai_task=False),
    ]
    for entry in entries:
        await _setup_entry(hass, entry)

    agents = [conversation.async_get_agent(hass, entry.entry_id) for entry in entries]
    assert all(isinstance(agent, ExtendedOpenAIAgentEntity) for agent in agents)

    private_markers = {
        (0, users[0].id): "PRIVATE_COLLISION_AGENT_A_USER_A",
        (0, users[1].id): "PRIVATE_COLLISION_AGENT_A_USER_B",
        (1, users[0].id): "PRIVATE_COLLISION_AGENT_B_USER_A",
    }
    observed: list[tuple[int, str, str]] = []

    def install_model(agent_index: int) -> None:
        agent = agents[agent_index]
        assert isinstance(agent, ExtendedOpenAIAgentEntity)

        async def model(log: conversation.ChatLog, **kwargs) -> None:
            del kwargs
            text = "\n".join(
                item.content
                for item in log.content
                if isinstance(getattr(item, "content", None), str)
            )
            current = next(
                marker for marker in private_markers.values() if marker in text
            )
            observed.append((agent_index, current, text))
            assert all(
                marker == current or marker not in text
                for marker in private_markers.values()
            ), text
            log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(
                    agent_id=agent.entity_id,
                    content=f"safe:{current}",
                )
            )

        monkeypatch.setattr(agent, "_async_handle_chat_log", model)

    install_model(0)
    install_model(1)

    collision_id = "caller-selected-cross-scope-collision"
    results = []
    for agent_index, user in ((0, users[0]), (0, users[1]), (1, users[0])):
        marker = private_markers[(agent_index, user.id)]
        result = await conversation.async_converse(
            hass=hass,
            text=f"Keep this turn private: {marker}",
            conversation_id=collision_id,
            context=Context(user_id=user.id),
            language="en",
            agent_id=entries[agent_index].entry_id,
        )
        assert result.response.error_code is None
        assert (
            result.response.as_dict()["speech"]["plain"]["speech"] == f"safe:{marker}"
        )
        assert result.conversation_id
        results.append(result.conversation_id)

    assert len(set(results)) == 3
    assert results[0] == collision_id
    assert results[1] != collision_id
    assert results[2] != collision_id
    assert len(observed) == 3
    record(
        stress_trace,
        "summary",
        layer="Real HA Assist",
        conversation_id_collision_probes=3,
        users=2,
        agents=2,
    )
