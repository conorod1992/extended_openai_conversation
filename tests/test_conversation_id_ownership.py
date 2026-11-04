"""Conversation-ID ownership helper contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.extended_openai_conversation_responses import (
    conversation_id_ownership as ownership,
    conversation_ids,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope


def _scope(user_id: str = "user-a"):
    return user_scope(user_id, source="test")


def test_claim_short_circuits_invalid_inputs_without_mutation() -> None:
    scope = _scope()
    hass = SimpleNamespace(data={})

    assert ownership.claim_conversation_id(
        hass, "agent", scope, None, guest_active=False
    ) is None
    assert ownership.claim_conversation_id(
        None, "agent", scope, "conversation", guest_active=False
    ) == "conversation"
    assert ownership.claim_conversation_id(
        hass, None, scope, "conversation", guest_active=False
    ) == "conversation"
    assert ownership.claim_conversation_id(
        SimpleNamespace(data=None),
        "agent",
        scope,
        "conversation",
        guest_active=False,
    ) == "conversation"
    assert hass.data == {}


def test_claim_reuses_same_owner_but_isolates_scope_agent_and_guest(
    monkeypatch,
) -> None:
    sequence = iter(["generated-one", "generated-two", "generated-three"])
    monkeypatch.setattr(
        conversation_ids,
        "uuid4",
        lambda: SimpleNamespace(hex=next(sequence)),
    )
    hass = SimpleNamespace(data={})
    scope_a = _scope("a")
    scope_b = _scope("b")

    first = ownership.claim_conversation_id(
        hass, "agent-a", scope_a, "shared-id", guest_active=False
    )
    same = ownership.claim_conversation_id(
        hass, "agent-a", scope_a, "shared-id", guest_active=False
    )
    other_scope = ownership.claim_conversation_id(
        hass, "agent-a", scope_b, "shared-id", guest_active=False
    )
    other_agent = ownership.claim_conversation_id(
        hass, "agent-b", scope_a, "shared-id", guest_active=False
    )
    guest = ownership.claim_conversation_id(
        hass, "agent-a", scope_a, "shared-id", guest_active=True
    )

    assert first == same == "shared-id"
    assert other_scope == "extended-openai-agent-a-generated-one"
    assert other_agent == "extended-openai-agent-b-generated-two"
    assert guest == "extended-openai-guest-agent-a-generated-three"

    owners = hass.data[ownership._CONVERSATION_ID_OWNERS]
    assert owners["shared-id"] == ("agent-a", "user:a")
    assert owners[other_scope] == ("agent-a", "user:b")
    assert owners[other_agent] == ("agent-b", "user:a")
    assert owners[guest] == ("agent-a", "guest:user:a")


def test_release_only_removes_matching_owner_and_tolerates_missing_storage() -> None:
    key = ownership._CONVERSATION_ID_OWNERS
    hass = SimpleNamespace(
        data={
            key: {
                "one": ("agent", "user:a"),
                "two": ("agent", "user:b"),
            }
        }
    )

    ownership.release_conversation_id_claim(
        hass, "one", ("agent", "user:wrong")
    )
    assert "one" in hass.data[key]

    ownership.release_conversation_id_claim(
        hass, "one", ("agent", "user:a")
    )
    assert "one" not in hass.data[key]
    assert "two" in hass.data[key]

    ownership.release_conversation_id_claim(None, "two", ("agent", "user:b"))
    ownership.release_conversation_id_claim(
        SimpleNamespace(data=None), "two", ("agent", "user:b")
    )
    ownership.release_conversation_id_claim(
        SimpleNamespace(data={}), "two", ("agent", "user:b")
    )


def test_cleanup_registration_releases_claim_and_ignores_unsupported_session() -> None:
    key = ownership._CONVERSATION_ID_OWNERS
    owner = ("agent", "user:a")
    hass = SimpleNamespace(data={key: {"conversation": owner}})
    callback = Mock()
    session = SimpleNamespace(async_on_cleanup=callback)

    ownership.register_conversation_id_cleanup(
        session, hass, "conversation", owner
    )
    callback.assert_called_once()
    release = callback.call_args.args[0]
    release()
    assert hass.data[key] == {}

    unsupported = SimpleNamespace(async_on_cleanup=None)
    ownership.register_conversation_id_cleanup(
        unsupported, hass, "conversation", owner
    )

class _UnownedLog(SimpleNamespace):
    """Weak-referenceable stand-in with Core's lifetime and identity semantics."""


def test_unclaimed_history_is_excluded_only_while_that_exact_core_log_is_live():
    import weakref
    from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS

    hass = SimpleNamespace(data={DATA_CHAT_LOGS: {}})
    source = _UnownedLog(hass=hass, conversation_id="foreign")
    hass.data[DATA_CHAT_LOGS]["foreign"] = source
    reference = weakref.ref(source)
    assert not ownership.conversation_id_is_owned(hass, "foreign")
    ownership.retain_unclaimed_chat_log(source)
    routed = ownership.claim_conversation_id(
        hass, "agent", _scope(), "foreign", guest_active=False
    )
    assert routed != "foreign"
    assert not ownership.conversation_id_is_owned(hass, "foreign")
    assert ownership.conversation_id_is_owned(hass, routed)
    hass.data[DATA_CHAT_LOGS].pop("foreign")
    del source
    assert reference() is None
    assert not hass.data[ownership._UNCLAIMED_CHAT_LOGS]
    assert (
        ownership.claim_conversation_id(
            hass, "agent", _scope(), "foreign", guest_active=False
        )
        == "foreign"
    )


def test_replacement_log_does_not_inherit_a_retained_foreign_logs_exclusion():
    from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS

    hass = SimpleNamespace(data={DATA_CHAT_LOGS: {}})
    source = _UnownedLog(hass=hass, conversation_id="reused")
    hass.data[DATA_CHAT_LOGS]["reused"] = source
    ownership.retain_unclaimed_chat_log(source)
    replacement = _UnownedLog(hass=hass, conversation_id="reused")
    hass.data[DATA_CHAT_LOGS]["reused"] = replacement
    assert (
        ownership.claim_conversation_id(
            hass, "agent", _scope(), "reused", guest_active=False
        )
        == "reused"
    )
    assert hass.data[ownership._UNCLAIMED_CHAT_LOGS]["reused"] is source
    assert not ownership.conversation_id_is_owned(None, "reused")
    assert not ownership.conversation_id_is_owned(SimpleNamespace(data=None), "reused")
