"""A privacy replacement must remain resumable in its active namespace."""

from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as owner,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    ConversationContinuity,
)
from custom_components.extended_openai_conversation_responses.scope import (
    resolve_data_scope,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from tests.test_conversation_provider_loop_contracts import (
    _final_stream,
    _provider_fixture,
)


@pytest.mark.parametrize(
    ("guest", "tighten"),
    [(False, False), (True, False), (True, True)],
    ids=["owner-control", "guest", "guest-policy-changed"],
)
async def test_owner_replacement_preserves_b_followup_only(hass, monkeypatch, guest, tighten):
    entity, client, _old, _logs, _invoke = _provider_fixture(
        hass,
        monkeypatch,
        [
            _final_stream("A reply"),
            _final_stream("B reply"),
            _final_stream("Followup reply"),
        ],
    )
    entity.subentry.data["conversation_continuity"] = "ha_default"
    entity._continuity = ConversationContinuity("agent")
    policy = replace(entity._resolve_live_guest_policy(), guest_active=guest)
    entity._resolve_live_guest_policy.return_value = policy
    monkeypatch.setattr(owner, "resolve_data_scope", resolve_data_scope)
    histories = {}

    def session(_hass, conversation_id, *, context):
        assert isinstance(context, Context)
        assert context.user_id in {"alice", "bob"}
        return nullcontext(
            SimpleNamespace(
                conversation_id=conversation_id or f"ha-{len(histories)}",
                async_on_cleanup=Mock(),
            )
        )

    def chat(_hass, session, request):
        log = histories.setdefault(
            session.conversation_id, conversation.ChatLog(hass, session.conversation_id)
        )
        log.async_add_user_content(conversation.UserContent(content=request.text))
        return nullcontext(log)

    monkeypatch.setattr(owner, "async_get_chat_session", session)
    monkeypatch.setattr(owner, "async_get_chat_log", chat)

    async def request(user, text, conversation_id=None):
        return await entity.async_process(
            conversation.ConversationInput(
                text=text,
                context=Context(user_id=user),
                conversation_id=conversation_id,
                device_id="kitchen",
                satellite_id=None,
                language="en",
                agent_id=entity.entity_id,
            )
        )

    a = await request("alice", "A private question")
    b = await request("bob", "B first question", a.conversation_id)
    if tighten:
        entity._resolve_live_guest_policy.return_value = replace(
            policy, knowledge_source_ids=frozenset()
        )
    follow = await request("bob", "B followup", b.conversation_id)
    assert a.conversation_id != b.conversation_id
    assert (follow.conversation_id != b.conversation_id) is tighten
    if guest:
        assert b.conversation_id.startswith("extended-openai-guest-")
    third = client.responses.create.await_args_list[2].kwargs["input"]
    text = str(third)
    assert ("B first question" not in text) is tighten
    assert "B followup" in text
    assert "A private question" not in text and "A reply" not in text
    assert "A private question" in str(histories[a.conversation_id].content)
    assert "B first question" not in str(histories[a.conversation_id].content)


async def test_guest_access_tightening_during_preparation_blocks_the_provider_send(hass, monkeypatch):
    from custom_components.extended_openai_conversation_responses import entity as provider

    entity, client, _old, _logs, invoke = _provider_fixture(
        hass, monkeypatch, [_final_stream("unused")]
    )
    entity._continuity = ConversationContinuity("agent")
    original = replace(entity._resolve_live_guest_policy(), guest_active=True)
    entity._resolve_live_guest_policy.return_value = original
    calculate_revision = provider.async_prepared_input_revision

    async def tighten(messages):
        revision = await calculate_revision(messages)
        entity._resolve_live_guest_policy.return_value = replace(
            original, readable_entity_ids=frozenset(),
        )
        return revision

    monkeypatch.setattr(provider, "async_prepared_input_revision", tighten)
    await invoke()
    client.responses.create.assert_not_awaited()
