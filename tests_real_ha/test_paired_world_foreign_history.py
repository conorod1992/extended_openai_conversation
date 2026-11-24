"""Metamorphic real-HA privacy checks: alter forbidden history, keep caller fixed.

The provider is scripted; no external LLM or embedding service is required.
The two worlds vary only an authenticated foreign user's conversation content.
"""
from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation_id_ownership,
)
from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SERVICE_PROCESS,
)
from custom_components.extended_openai_conversation_responses.ha_chat_session_compat import (
    async_get_chat_session,
)
from homeassistant.components import conversation
from homeassistant.components.conversation.chat_log import (
    DATA_CHAT_LOGS,
    async_get_chat_log,
    current_chat_log,
)
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


async def test_direct_process_routes_cached_foreign_history_to_owned_continuation(
    hass, monkeypatch
):
    caller = await hass.auth.async_create_user("Parent and child session owner")
    context = Context(user_id=caller.id)
    agent = await _agent(hass)
    sent = _provider(monkeypatch, agent, ["First.", "Second.", "Third.", "Fresh."])
    parent_id = "foreign-cached-parent"
    parent_input = conversation.ConversationInput(
        text="FOREIGN_PARENT_INPUT",
        context=context,
        conversation_id=parent_id,
        device_id=None,
        satellite_id=None,
        language="en",
        agent_id="conversation.foreign",
    )
    with async_get_chat_session(hass, parent_id, context=context) as session:
        with async_get_chat_log(hass, session, parent_input) as parent:
            parent.async_add_assistant_content_without_tools(
                conversation.AssistantContent(
                    agent_id=parent_input.agent_id, content="FOREIGN_PARENT_ANSWER"
                )
            )
            original = parent.content.copy()
    assert current_chat_log.get() is None
    assert hass.data[DATA_CHAT_LOGS][parent_id] is parent
    assert not conversation_id_ownership.conversation_id_is_owned(hass, parent_id)

    async def process(text, conversation_id):
        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_PROCESS,
            {
                "agent_id": agent.entity_id,
                "text": text,
                "conversation_id": conversation_id,
            },
            blocking=True,
            return_response=True,
            context=context,
        )
        assert response["successful"]
        return response["conversation_id"]

    child_id = await process("CHILD_FIRST_TURN", parent_id)
    assert child_id != parent_id
    assert await process("CHILD_PARENT_RETRY", parent_id) == child_id
    assert await process("CHILD_BY_RETURNED_ID", child_id) == child_id
    assert await process("Fresh turn", "fresh-external-caller-id") == (
        "fresh-external-caller-id"
    )

    assert "CHILD_FIRST_TURN" in str(sent[1]["messages"])
    assert "CHILD_PARENT_RETRY" in str(sent[2]["messages"])
    assert "CHILD_FIRST_TURN" not in str(sent[3]["messages"])
    assert all("FOREIGN_PARENT_" not in str(item["messages"]) for item in sent)
    assert parent.content == original
    assert not conversation_id_ownership.conversation_id_is_owned(hass, parent_id)
