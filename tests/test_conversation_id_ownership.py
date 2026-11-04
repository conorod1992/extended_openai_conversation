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
    assert hass.data[ownership._UNCLAIMED_CHAT_LOGS]["reused"].source() is source
    assert not ownership.conversation_id_is_owned(None, "reused")
    assert not ownership.conversation_id_is_owned(SimpleNamespace(data=None), "reused")

def test_parent_binding_is_scoped_and_does_not_outlive_its_owned_target():
    from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS

    hass = SimpleNamespace(data={DATA_CHAT_LOGS: {}})
    source = _UnownedLog(hass=hass, conversation_id="parent")
    hass.data[DATA_CHAT_LOGS]["parent"] = source
    ownership.retain_unclaimed_chat_log(source)
    targets = []
    for agent, scope, guest in [
        ("agent", _scope("a"), False),
        ("agent", _scope("b"), False),
        ("other", _scope("a"), False),
        ("agent", _scope("a"), True),
    ]:
        target = ownership.claim_conversation_id(
            hass, agent, scope, "parent", guest_active=guest
        )
        targets.append(target)
        assert target != "parent"
        assert (
            ownership.resolve_parent_conversation_id(
                hass, agent, scope, "parent", guest_active=guest
            )
            == target
        )
        ownership.retain_unclaimed_chat_log(source)
        assert (
            ownership.claim_conversation_id(
                hass, agent, scope, "parent", guest_active=guest
            )
            == target
        )
    assert len(set(targets)) == 4
    assert not ownership.conversation_id_is_owned(hass, "parent")
    ownership.release_conversation_id_claim(hass, targets[0], ("agent", "user:a"))
    assert (
        ownership.resolve_parent_conversation_id(
            hass, "agent", _scope("a"), "parent", guest_active=False
        )
        == "parent"
    )
    assert (
        ownership.claim_conversation_id(
            hass, "agent", _scope("a"), "parent", guest_active=False
        )
        not in targets
    )


def test_replacement_parent_and_retired_weak_callback_cannot_reuse_or_remove_new_binding():
    from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS

    hass = SimpleNamespace(data={DATA_CHAT_LOGS: {}})
    source = _UnownedLog(hass=hass, conversation_id="parent")
    hass.data[DATA_CHAT_LOGS]["parent"] = source
    ownership.retain_unclaimed_chat_log(source)
    old_record = hass.data[ownership._UNCLAIMED_CHAT_LOGS]["parent"]
    old_target = ownership.claim_conversation_id(
        hass, "agent", _scope(), "parent", guest_active=False
    )
    replacement = _UnownedLog(hass=hass, conversation_id="parent")
    hass.data[DATA_CHAT_LOGS]["parent"] = replacement
    assert (
        ownership.resolve_parent_conversation_id(
            hass, "agent", _scope(), "parent", guest_active=False
        )
        == "parent"
    )
    ownership.retain_unclaimed_chat_log(replacement)
    new_target = ownership.claim_conversation_id(
        hass, "agent", _scope(), "parent", guest_active=False
    )
    assert new_target != old_target
    del source
    assert old_record.source() is None
    assert (
        ownership.resolve_parent_conversation_id(
            hass, "agent", _scope(), "parent", guest_active=False
        )
        == new_target
    )


def test_actual_guest_target_can_bind_to_parent_after_namespace_resolution():
    from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS

    hass = SimpleNamespace(data={DATA_CHAT_LOGS: {}})
    source = _UnownedLog(hass=hass, conversation_id="parent")
    hass.data[DATA_CHAT_LOGS]["parent"] = source
    ownership.retain_unclaimed_chat_log(source)
    assert (
        ownership.claim_conversation_id(
            hass, "agent", _scope(), "actual-guest", guest_active=True
        )
        == "actual-guest"
    )
    ownership.bind_parent_conversation_id(
        hass, "parent", "actual-guest", ("agent", "guest:user:user-a")
    )
    assert (
        ownership.resolve_parent_conversation_id(
            hass, "agent", _scope(), "parent", guest_active=True
        )
        == "actual-guest"
    )
    assert (
        ownership.resolve_parent_conversation_id(
            hass, "agent", _scope(), "parent", guest_active=False
        )
        == "parent"
    )
    assert (
        ownership.resolve_parent_conversation_id(
            None, "agent", _scope(), "parent", guest_active=True
        )
        == "parent"
    )
    assert (
        ownership.resolve_parent_conversation_id(
            SimpleNamespace(data=None), "agent", _scope(), "parent", guest_active=True
        )
        == "parent"
    )
    ownership.bind_parent_conversation_id(None, None, "unused", ("agent", "user:user-a"))


def test_repeated_claim_cleanup_registration_is_bounded_per_native_session():
    owner = ("agent", "user:a")
    hass = SimpleNamespace(data={ownership._CONVERSATION_ID_OWNERS: {"session": owner}})
    session = SimpleNamespace(async_on_cleanup=Mock())
    for _ in range(100):
        ownership.register_conversation_id_cleanup(session, hass, "session", owner)
    session.async_on_cleanup.assert_called_once()
    session.async_on_cleanup.call_args.args[0]()
    assert hass.data[ownership._CLEANUP_REGISTRATIONS] == {}
    assert hass.data[ownership._CONVERSATION_ID_OWNERS] == {}


def test_retired_session_cleanup_cannot_release_a_replacement_claim():
    owner = ("agent", "user:a")
    hass = SimpleNamespace(data={ownership._CONVERSATION_ID_OWNERS: {"session": owner}})
    old = SimpleNamespace(async_on_cleanup=Mock())
    replacement = SimpleNamespace(async_on_cleanup=Mock())
    ownership.register_conversation_id_cleanup(old, hass, "session", owner)
    ownership.register_conversation_id_cleanup(replacement, hass, "session", owner)
    old.async_on_cleanup.call_args.args[0]()
    assert hass.data[ownership._CONVERSATION_ID_OWNERS]["session"] == owner
    assert hass.data[ownership._CLEANUP_REGISTRATIONS]["session"].session is replacement
    replacement.async_on_cleanup.call_args.args[0]()
    assert hass.data[ownership._CONVERSATION_ID_OWNERS] == {}
    assert hass.data[ownership._CLEANUP_REGISTRATIONS] == {}


def test_cleanup_from_a_retired_registry_cannot_release_current_claim():
    owner = ("agent", "user:a")
    hass = SimpleNamespace(data={ownership._CONVERSATION_ID_OWNERS: {"session": owner}})
    old = SimpleNamespace(async_on_cleanup=Mock())
    current = SimpleNamespace(async_on_cleanup=Mock())
    ownership.register_conversation_id_cleanup(old, hass, "session", owner)
    hass.data[ownership._CLEANUP_REGISTRATIONS] = {}
    ownership.register_conversation_id_cleanup(current, hass, "session", owner)
    old.async_on_cleanup.call_args.args[0]()
    assert hass.data[ownership._CONVERSATION_ID_OWNERS]["session"] == owner
    current.async_on_cleanup.call_args.args[0]()
    assert hass.data[ownership._CONVERSATION_ID_OWNERS] == {}

def test_parent_keepalive_preserves_native_authenticated_owner():
    from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS
    from homeassistant.core import Context
    from homeassistant.helpers.chat_session import DATA_CHAT_SESSION
    from homeassistant.helpers.typing import UNDEFINED

    native = SimpleNamespace(user_id="a", async_updated=Mock())
    hass = SimpleNamespace(
        data={DATA_CHAT_LOGS: {}, DATA_CHAT_SESSION: {"parent": native}}
    )
    source = _UnownedLog(hass=hass, conversation_id="parent")
    hass.data[DATA_CHAT_LOGS]["parent"] = source
    ownership.retain_unclaimed_chat_log(source)
    for user in ("a", "b"):
        target = ownership.claim_conversation_id(
            hass, "agent", _scope(user), "parent", guest_active=False
        )
        assert (
            ownership.resolve_parent_conversation_id(
                hass,
                "agent",
                _scope(user),
                "parent",
                guest_active=False,
                context=Context(user_id=user),
            )
            == target
        )
    native.async_updated.assert_called_once()
    assert native.user_id == "a"
    native.user_id = UNDEFINED
    ownership.resolve_parent_conversation_id(
        hass,
        "agent",
        _scope("a"),
        "parent",
        guest_active=False,
        context=Context(user_id="a"),
    )
    assert native.async_updated.call_count == 2
    assert native.user_id is UNDEFINED
