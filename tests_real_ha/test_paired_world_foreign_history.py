"""Metamorphic real-HA privacy checks: alter forbidden history, keep caller fixed.

The provider is scripted; no external LLM or embedding service is required.
The two worlds vary only an authenticated foreign user's conversation content.
"""
from copy import deepcopy

import pytest

from homeassistant.components import conversation
from homeassistant.core import Context

from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _speech


async def _converse(hass, agent, user_id, text, conversation_id=None):
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=conversation_id,
        context=Context(user_id=user_id),
        language="en",
        agent_id=agent.entry.entry_id,
    )


def _messages(payload):
    return deepcopy(payload["messages"])


@pytest.mark.parametrize(
    "foreign_text",
    [
        "BOB_PRIVATE_RED_7d3c",
        "BOB_PRIVATE_BLUE_81af",
        "BOB_PRIVATE_LONG_" + "x" * 250,
    ],
)
async def test_foreign_history_cannot_change_authenticated_provider_request(
    hass, monkeypatch, foreign_text
):
    """Counterfactual: modifying Bob's private text cannot affect Alice's wire data.

    Reusing Bob's returned conversation ID deliberately tests ownership fencing.
    Positive control proves the provider is actually invoked for Alice.
    """
    alice = await hass.auth.async_create_user("Metamorphic Alice")
    bob = await hass.auth.async_create_user("Metamorphic Bob")
    agent = await _agent(hass)
    sent = _provider(monkeypatch, agent, ["Bob acknowledged.", "Alice answered.", "Alice answered."])

    foreign = await _converse(hass, agent, bob.id, foreign_text)
    assert _speech(foreign) == "Bob acknowledged."
    assert foreign.conversation_id is not None

    attempted_resume = await _converse(
        hass, agent, alice.id, "What is the living room status?", foreign.conversation_id
    )
    assert _speech(attempted_resume) == "Alice answered."
    assert attempted_resume.conversation_id != foreign.conversation_id
    assert len(sent) == 2
    observed = _messages(sent[-1])
    assert not any(foreign_text in str(part) for part in observed)

    fresh = await _converse(hass, agent, alice.id, "What is the living room status?")
    assert _speech(fresh) == "Alice answered."
    baseline = _messages(sent[-1])
    assert observed == baseline, (
        "Foreign conversation history or session settings influenced Alice's request",
        observed, baseline,
    )
