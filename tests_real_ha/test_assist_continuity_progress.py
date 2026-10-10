"""Real Assist streams survive selection of an owned continuity ChatLog."""

from __future__ import annotations

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_CONVERSATION_CONTINUITY,
    CONVERSATION_CONTINUITY_DEVICE,
    CONVERSATION_CONTINUITY_HA_DEFAULT,
    CONVERSATION_CONTINUITY_USER,
)
from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS
from homeassistant.setup import async_setup_component
from tests_real_ha.test_assist_streaming_speech_processing import (
    _chat_sse_deltas,
    _final_speech,
    _progressive_text,
    _run_assist,
    _speech_agent,
)
from tests_real_ha.test_provider_wire_e2e import _install_wire


def _result_id(events):
    """Return the ID that Assist gives the caller for their next turn."""
    return next(event for event in events if event["type"] == "intent-end")["data"][
        "intent_output"
    ]["conversation_id"]


@pytest.mark.parametrize(
    "continuity",
    [
        CONVERSATION_CONTINUITY_HA_DEFAULT,
        CONVERSATION_CONTINUITY_USER,
        CONVERSATION_CONTINUITY_DEVICE,
    ],
)
@pytest.mark.parametrize("initial_id", [None, "assist-initial-id"])
async def test_assist_progress_follows_continuity_across_turns(
    hass, hass_ws_client, monkeypatch, continuity, initial_id
):
    """Fresh IDs resume owned history; returned IDs reuse the current HA log."""
    agent = await _speech_agent(hass, **{CONF_CONVERSATION_CONTINUITY: continuity})
    assert await async_setup_component(hass, "assist_pipeline", {})
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_deltas([f"**Answer {index}** ", "see https://example.com done."])
            for index in range(3)
        ],
    )
    client = await hass_ws_client(hass)
    ids = []
    for index, incoming in enumerate([initial_id, "assist-new-invocation", None]):
        if index == 2:
            incoming = ids[-1]
        events = await _run_assist(
            client,
            pipeline_id=agent.entity_id,
            conversation_id=incoming,
            text=f"Tell me response {index}",
        )
        expected = f"Answer {index} see done."
        assert _progressive_text(events) == expected
        assert _final_speech(events) == expected
        ids.append(_result_id(events))
        assert hass.data[DATA_CHAT_LOGS][ids[-1]].delta_listener is None
    assert len(wire.requests) == 3
    if continuity != CONVERSATION_CONTINUITY_HA_DEFAULT:
        assert len(set(ids)) == 1
        content = hass.data[DATA_CHAT_LOGS][ids[-1]].content
        assert [item.content for item in content if item.role == "user"] == [
            f"Tell me response {index}" for index in range(3)
        ]


async def test_assist_progress_keeps_guest_history_separate(
    hass, hass_ws_client, monkeypatch
):
    """The listener follows each live turn without importing owner/Guest history."""
    agent = await _speech_agent(
        hass, **{CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_USER}
    )
    assert await async_setup_component(hass, "assist_pipeline", {})
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_deltas(["**Owner** ", "private reply."]),
            _chat_sse_deltas(["**Guest** ", "public reply."]),
            _chat_sse_deltas(["**Owner** ", "resumed reply."]),
        ],
    )
    client = await hass_ws_client(hass)
    owner = await _run_assist(
        client,
        pipeline_id=agent.entity_id,
        conversation_id="owner-invocation",
        text="Owner private question",
    )
    assert _progressive_text(owner) == _final_speech(owner) == "Owner private reply."
    owner_id = _result_id(owner)
    await agent._guest_mode.async_update_trusted(indefinite=True)
    guest = await _run_assist(
        client,
        pipeline_id=agent.entity_id,
        conversation_id=owner_id,
        text="Guest public question",
    )
    assert _progressive_text(guest) == _final_speech(guest) == "Guest public reply."
    guest_id = _result_id(guest)
    assert guest_id != owner_id
    assert guest_id.startswith("extended-openai-guest-")
    guest_content = hass.data[DATA_CHAT_LOGS][guest_id].content
    assert [item.content for item in guest_content if item.role == "user"] == [
        "Guest public question"
    ]
    await agent._guest_mode.async_disable_trusted()
    resumed = await _run_assist(
        client,
        pipeline_id=agent.entity_id,
        conversation_id=guest_id,
        text="Owner follow-up question",
    )
    assert (
        _progressive_text(resumed) == _final_speech(resumed) == "Owner resumed reply."
    )
    assert _result_id(resumed) == owner_id
    owner_content = hass.data[DATA_CHAT_LOGS][owner_id].content
    assert [item.content for item in owner_content if item.role == "user"] == [
        "Owner private question",
        "Owner follow-up question",
    ]
    assert len(wire.requests) == 3
    assert all(log.delta_listener is None for log in hass.data[DATA_CHAT_LOGS].values())
