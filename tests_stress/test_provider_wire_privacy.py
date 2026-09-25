"""Public Assist and SDK-wire privacy checks over populated durable stores."""

from __future__ import annotations

import asyncio
import json

from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_tool_call,
    _chat_tool_result,
    _knowledge_agent,
)
from tests_real_ha.test_memory_provider_wire_e2e import _memory_agent
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire, _speech
from tests_stress.conftest import record
from tests_stress.health import HealthChecks, assert_enhanced_health


async def _say(hass: HomeAssistant, entry_id: str, user_id: str, text: str):
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(user_id=user_id),
        language="en",
        agent_id=entry_id,
    )


async def test_concurrent_users_never_cross_private_provider_wire(
    hass: HomeAssistant,
    monkeypatch,
    stress_seed: int,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    users = [f"wire-owner-{number:02d}" for number in range(10 * stress_scale)]
    for user in users:
        MockUser(id=user, name=user, is_owner=True).add_to_hass(hass)
    entry, agent = await _memory_agent(hass, API_MODE_CHAT_COMPLETIONS)
    markers = {user: f"PRIVATE-MEMORY-{user}-{stress_seed}" for user in users}
    for user, marker in markers.items():
        await agent._memory.async_add(
            user,
            f"The calibration token for {user} is {marker}.",
            "preferences",
            "explicit",
        )

    async def batch(current_agent, turns: int) -> None:
        wire = _install_wire(
            monkeypatch,
            current_agent,
            [
                _chat_sse_text("private lookup complete")
                for _ in range(turns * len(users))
            ],
        )
        for turn in range(turns):
            results = await asyncio.gather(
                *(
                    _say(
                        hass,
                        entry.entry_id,
                        user,
                        f"For {user}, what is my calibration token? Turn {turn}.",
                    )
                    for user in users
                )
            )
            assert all(
                _speech(result) == "private lookup complete" for result in results
            )
        assert len(wire.requests) == turns * len(users)
        for request in wire.requests:
            body = json.dumps(request["body"], ensure_ascii=False)
            matching_users = [user for user in users if f"For {user}," in body]
            assert len(matching_users) == 1
            owner = matching_users[0]
            assert markers[owner] in body, owner
            assert all(
                other_marker not in body
                for other_user, other_marker in markers.items()
                if other_user != owner
            ), owner
        record(
            stress_trace,
            "provider_wire_batch",
            layer="provider-wire",
            public_turns=turns * len(users),
            provider_requests=len(wire.requests),
            concurrent_users=len(users),
        )

    await batch(agent, 2)
    await agent._guest_mode.async_update_trusted(indefinite=True)
    guest_wire = _install_wire(
        monkeypatch, agent, [_chat_sse_text("private context hidden")]
    )
    guest_result = await _say(
        hass,
        entry.entry_id,
        users[0],
        f"For {users[0]}, what is my calibration token while Guest Mode is active?",
    )
    assert _speech(guest_result) == "private context hidden"
    assert len(guest_wire.requests) == 1
    guest_body = json.dumps(guest_wire.requests[0]["body"], ensure_ascii=False)
    assert all(marker not in guest_body for marker in markers.values())
    await agent._guest_mode.async_disable_trusted()
    record(
        stress_trace,
        "guest_provider_wire_probe",
        layer="provider-wire",
        public_turns=1,
        provider_requests=1,
        hidden_private_markers=len(markers),
    )
    await assert_enhanced_health(
        hass,
        entry,
        next(
            item
            for item in entry.subentries.values()
            if item.subentry_type == "conversation"
        ),
        HealthChecks(backup=True, memory_users=tuple(users)),
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    reloaded = conversation.async_get_agent(hass, entry.entry_id)
    assert reloaded is not None
    await batch(reloaded, 1)
    record(
        stress_trace,
        "summary",
        layer="provider-wire",
        users=len(users),
        public_turns=3 * len(users) + 1,
        provider_requests=3 * len(users) + 1,
        reloads=1,
    )


async def test_knowledge_enabled_and_deleted_sources_on_tool_wire(
    hass: HomeAssistant,
    monkeypatch,
    stress_seed: int,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    agent = await _knowledge_agent(hass, API_MODE_CHAT_COMPLETIONS)
    sources = 20 * stress_scale
    selected_marker = f"KNOWLEDGE-LIVE-{stress_seed}"
    disabled_marker = f"KNOWLEDGE-DISABLED-{stress_seed}"
    deleted_marker = f"KNOWLEDGE-DELETED-{stress_seed}"
    for number in range(sources):
        await agent._knowledge.async_create(
            f"Reference {number}",
            f"Unrelated reference {number}",
            f"Reference content number {number}",
        )
    selected = await agent._knowledge.async_create(
        "Quasar calibration", "Quasar calibration code", selected_marker
    )
    await agent._knowledge.async_create(
        "Disabled quasar calibration",
        "Quasar calibration code",
        disabled_marker,
        enabled=False,
    )
    deleted = await agent._knowledge.async_create(
        "Deleted quasar calibration", "Quasar calibration code", deleted_marker
    )
    assert await agent._knowledge.async_delete(deleted.source_id)
    call_id = "call-enhanced-knowledge"
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                call_id,
                "knowledge_search",
                {"query": "Quasar calibration code", "limit": 5},
            ),
            _chat_sse_text("knowledge lookup complete"),
        ],
    )
    result = await _say(
        hass, agent.entry.entry_id, "", "Find the Quasar calibration code"
    )
    assert _speech(result) == "knowledge lookup complete"
    assert len(wire.requests) == 2
    first = json.dumps(wire.requests[0]["body"], ensure_ascii=False)
    assert selected_marker not in first  # Knowledge is retrieved on demand.
    response = _chat_tool_result(wire.requests[1]["body"], call_id)
    serialized = json.dumps(response, ensure_ascii=False)
    assert selected.source_id in serialized
    assert selected_marker in serialized
    assert disabled_marker not in serialized
    assert deleted_marker not in serialized
    record(
        stress_trace,
        "summary",
        layer="provider-wire",
        knowledge_sources=sources + 1,
        public_turns=1,
        provider_requests=2,
        actual_tool_executions=1,
    )
