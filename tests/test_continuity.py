"""Focused conversation-continuity tests."""

from datetime import timedelta

from custom_components.extended_openai_conversation_responses.const import (
    CONVERSATION_CONTINUITY_DEVICE,
    CONVERSATION_CONTINUITY_HA_DEFAULT,
    CONVERSATION_CONTINUITY_USER,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    GUEST_CONTINUITY_NAMESPACE,
    ConversationContinuity,
)
from custom_components.extended_openai_conversation_responses.scope import (
    shared_scope,
    unretained_scope,
    user_scope,
)
from homeassistant.components import conversation
from homeassistant.util import dt as dt_util


async def test_ha_default_preserves_incoming_id() -> None:
    manager = ConversationContinuity("agent")
    result = await manager.async_resolve(
        CONVERSATION_CONTINUITY_HA_DEFAULT,
        user_scope("one", source="test"),
        "kitchen",
        "incoming",
        30,
    )
    assert result.conversation_id == "incoming"
    assert result.key is None


async def test_per_device_resume_isolated_and_reset() -> None:
    manager = ConversationContinuity("agent")
    scope = unretained_scope(device_id="kitchen")
    first = await manager.async_resolve(
        CONVERSATION_CONTINUITY_DEVICE, scope, "kitchen", None, 30
    )
    history = [conversation.SystemContent(content="system")]
    await manager.async_record_success(first.key, history)
    resumed = await manager.async_resolve(
        CONVERSATION_CONTINUITY_DEVICE, scope, "kitchen", "fresh-ha-id", 30
    )
    other = await manager.async_resolve(
        CONVERSATION_CONTINUITY_DEVICE, scope, "bedroom", None, 30
    )
    assert resumed.conversation_id == first.conversation_id
    assert resumed.history == history
    assert other.conversation_id != first.conversation_id
    assert first.key is not None and await manager.async_end(first.key)
    reset = await manager.async_resolve(
        CONVERSATION_CONTINUITY_DEVICE, scope, "kitchen", None, 30
    )
    assert reset.conversation_id != first.conversation_id


async def test_per_user_cross_device_and_safe_fallback() -> None:
    manager = ConversationContinuity("agent")
    known = user_scope("alice", source="test")
    first = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER, known, "kitchen", None, 30
    )
    await manager.async_record_success(
        first.key, [conversation.SystemContent(content="system")]
    )
    cross_device = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER, known, "study", None, 30
    )
    unknown = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER,
        unretained_scope(device_id="kitchen"),
        "kitchen",
        None,
        30,
    )
    shared = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER,
        shared_scope(source="test"),
        None,
        "ha-id",
        30,
    )
    assert cross_device.conversation_id == first.conversation_id
    assert unknown.conversation_id != first.conversation_id
    assert unknown.key == "device:kitchen"
    assert shared.conversation_id == "ha-id"
    assert shared.key is None


async def test_inactivity_and_success_reset_timer() -> None:
    manager = ConversationContinuity("agent")
    scope = user_scope("alice", source="test")
    first = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, None, None, 30
    )
    await manager.async_release(first.key)
    manager._sessions[first.key].last_active = dt_util.utcnow() - timedelta(minutes=31)
    expired = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, None, None, 30
    )
    assert expired.conversation_id != first.conversation_id
    manager._sessions[expired.key].last_active = dt_util.utcnow() - timedelta(
        minutes=29
    )
    await manager.async_record_success(
        expired.key, [conversation.SystemContent(content="bounded")]
    )
    assert manager._sessions[expired.key].last_active > dt_util.utcnow() - timedelta(
        seconds=2
    )


async def test_overlapping_requests_do_not_share_mutable_chat_log() -> None:
    manager = ConversationContinuity("agent")
    scope = user_scope("alice", source="test")
    first = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, "kitchen", None, 30
    )
    overlapping = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, "study", None, 30
    )
    assert overlapping.key is None
    assert overlapping.conversation_id != first.conversation_id


async def test_in_flight_session_is_not_pruned_until_released() -> None:
    manager = ConversationContinuity("agent")
    scope = user_scope("alice", source="test")
    active = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, None, None, 1
    )
    assert active.key is not None
    manager._sessions[active.key].last_active = dt_util.utcnow() - timedelta(minutes=2)

    await manager.async_list(1)
    assert active.key in manager._sessions

    await manager.async_release(active.key)
    await manager.async_list(1)
    assert active.key not in manager._sessions


async def test_consecutive_guest_turns_resume_guest_history() -> None:
    manager = ConversationContinuity("agent")
    scope = unretained_scope(device_id="kitchen")
    first = await manager.async_resolve(
        CONVERSATION_CONTINUITY_DEVICE,
        scope,
        "kitchen",
        "owner-conversation",
        30,
        namespace=GUEST_CONTINUITY_NAMESPACE,
    )
    history = [conversation.SystemContent(content="guest follow-up context")]
    await manager.async_record_success(first.key, history)

    resumed = await manager.async_resolve(
        CONVERSATION_CONTINUITY_DEVICE,
        scope,
        "kitchen",
        first.conversation_id,
        30,
        namespace=GUEST_CONTINUITY_NAMESPACE,
    )

    assert first.key == "guest:device:kitchen"
    assert resumed.conversation_id == first.conversation_id
    assert resumed.history == history
    assert resumed.resumed is True


async def test_owner_to_guest_never_inherits_owner_history() -> None:
    manager = ConversationContinuity("agent")
    scope = unretained_scope(device_id="kitchen")
    owner = await manager.async_resolve(
        CONVERSATION_CONTINUITY_DEVICE, scope, "kitchen", None, 30
    )
    owner_history = [conversation.SystemContent(content="owner private history")]
    await manager.async_record_success(owner.key, owner_history)

    guest = await manager.async_resolve(
        CONVERSATION_CONTINUITY_DEVICE,
        scope,
        "kitchen",
        owner.conversation_id,
        30,
        namespace=GUEST_CONTINUITY_NAMESPACE,
    )

    assert owner.key == "device:kitchen"
    assert guest.key == "guest:device:kitchen"
    assert guest.conversation_id != owner.conversation_id
    assert guest.history == []


async def test_guest_to_owner_never_inherits_guest_history() -> None:
    cases = (
        (
            CONVERSATION_CONTINUITY_HA_DEFAULT,
            unretained_scope(device_id="kitchen"),
            "kitchen",
        ),
        (CONVERSATION_CONTINUITY_USER, shared_scope(source="test"), None),
    )
    for mode, scope, device_id in cases:
        manager = ConversationContinuity("agent")
        guest = await manager.async_resolve(
            mode,
            scope,
            device_id,
            "owner-conversation",
            30,
            namespace=GUEST_CONTINUITY_NAMESPACE,
        )
        guest_follow_up = await manager.async_resolve(
            mode,
            scope,
            device_id,
            guest.conversation_id,
            30,
            namespace=GUEST_CONTINUITY_NAMESPACE,
        )

        owner = await manager.async_resolve(
            mode,
            scope,
            device_id,
            guest.conversation_id,
            30,
        )

        assert guest.conversation_id is not None
        assert guest.conversation_id.startswith("extended-openai-guest-")
        assert guest.conversation_id != "owner-conversation"
        assert guest_follow_up.conversation_id == guest.conversation_id
        assert owner.conversation_id is None
        assert owner.key is None
        assert owner.history == []


async def test_guest_continuity_respects_timeout_and_explicit_end() -> None:
    manager = ConversationContinuity("agent")
    scope = unretained_scope(device_id="kitchen")
    first = await manager.async_resolve(
        CONVERSATION_CONTINUITY_DEVICE,
        scope,
        "kitchen",
        None,
        30,
        namespace=GUEST_CONTINUITY_NAMESPACE,
    )
    await manager.async_record_success(
        first.key, [conversation.SystemContent(content="expired guest history")]
    )
    assert first.key is not None
    manager._sessions[first.key].last_active = dt_util.utcnow() - timedelta(minutes=31)

    expired = await manager.async_resolve(
        CONVERSATION_CONTINUITY_DEVICE,
        scope,
        "kitchen",
        first.conversation_id,
        30,
        namespace=GUEST_CONTINUITY_NAMESPACE,
    )
    assert expired.conversation_id != first.conversation_id
    assert expired.history == []
    await manager.async_release(expired.key)
    assert expired.key is not None and await manager.async_end(expired.key)

    restarted = await manager.async_resolve(
        CONVERSATION_CONTINUITY_DEVICE,
        scope,
        "kitchen",
        expired.conversation_id,
        30,
        namespace=GUEST_CONTINUITY_NAMESPACE,
    )
    assert restarted.conversation_id != expired.conversation_id
    assert restarted.history == []
