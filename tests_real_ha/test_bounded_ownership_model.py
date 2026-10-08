"""Bounded model coverage and genuine HA cross-owner replay."""
import pytest
from homeassistant.components import conversation
from homeassistant.core import Context

from custom_components.extended_openai_conversation_responses.const import (
    CONF_CONVERSATION_CONTINUITY,
    CONVERSATION_CONTINUITY_HA_DEFAULT,
    CONVERSATION_CONTINUITY_USER,
    CONVERSATION_CONTINUITY_DEVICE,
)
from tests.ownership_state_model import MODES, explore, replay_witnesses
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider

MODE_OPTIONS = {
    "ha": CONVERSATION_CONTINUITY_HA_DEFAULT,
    "user": CONVERSATION_CONTINUITY_USER,
    "device": CONVERSATION_CONTINUITY_DEVICE,
}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("guest", [False, True])
def test_bounded_explorer_is_complete_to_declared_depth(mode, guest):
    traces, states = explore(mode, guest, depth=3)
    assert traces and states
    assert any(len(trace) == 3 for trace, _ in traces)
    assert all(len(trace) <= 3 for trace, _ in traces)
    assert len(traces) == len(explore(mode, guest, 3)[0])
    assert any(
        len(trace) == 3
        and tuple(action[0] for action in trace) == ("start", "switch", "continue")
        for trace, _ in traces
    )


async def _converse(hass, agent, owner, message, conversation_id=None, device_id=None):
    return await conversation.async_converse(
        hass=hass,
        text=message,
        conversation_id=conversation_id,
        context=Context(user_id=owner),
        language="en",
        agent_id=agent.entry.entry_id,
        device_id=device_id,
    )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("guest", [False, True])
async def test_exhaustively_generated_cross_owner_traces_replayed_in_real_ha(
    hass, monkeypatch, mode, guest
):
    """Replay every depth-three start/switch/continue-with-ID witness."""
    traces = [case for case in replay_witnesses(3)
              if case[0] == mode and case[1] == guest]
    assert traces
    alice = await hass.auth.async_create_user("Model Alice")
    bob = await hass.auth.async_create_user("Model Bob")
    owner_ids = {"alice": alice.id, "bob": bob.id}
    agent = await _agent(hass, **{CONF_CONVERSATION_CONTINUITY: MODE_OPTIONS[mode]})
    if guest:
        await agent._guest_mode.async_update_trusted(indefinite=True)
    # The script provider captures all requests, but contains no external API use.
    sent = _provider(monkeypatch, agent, ["Acknowledged."] * (len(traces) * 2))
    for index, (_mode, _guest, trace, _) in enumerate(traces):
        assert trace[0][0] == "start" and trace[1][0] == "switch"
        device = trace[0][1]
        initial = await _converse(
            hass, agent, owner_ids["alice"], f"PRIVATE_ALICE_TRACE_{index}",
            device_id=device,
        )
        before = len(sent)
        next_turn = await _converse(
            hass, agent, owner_ids["bob"], "What is the status?",
            conversation_id=initial.conversation_id,
            device_id=device,
        )
        assert next_turn.response.error_code is None
        assert len(sent) == before + 1
        wire = str(sent[-1].get("messages", sent[-1]))
        assert f"PRIVATE_ALICE_TRACE_{index}" not in wire, (
            mode, guest, trace, wire,
        )
        assert next_turn.conversation_id != initial.conversation_id, (
            mode, guest, trace,
        )
