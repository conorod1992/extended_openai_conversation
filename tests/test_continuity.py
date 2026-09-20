"""Focused conversation-continuity tests."""

from datetime import timedelta
from types import SimpleNamespace


from custom_components.extended_openai_conversation_responses import continuity
from custom_components.extended_openai_conversation_responses.const import (
    CONVERSATION_CONTINUITY_DEVICE,
    CONVERSATION_CONTINUITY_HA_DEFAULT,
    CONVERSATION_CONTINUITY_USER,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    GUEST_CONTINUITY_NAMESPACE,
    ConversationContinuity,
    async_get_continuity,
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
    await manager.async_record_success(first.key, first.claim_token, history)
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
        first.key, first.claim_token, [conversation.SystemContent(content="system")]
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
    await manager.async_release(first.key, first.claim_token)
    manager._sessions[first.key].last_active = dt_util.utcnow() - timedelta(minutes=31)
    expired = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, None, None, 30
    )
    assert expired.conversation_id != first.conversation_id
    manager._sessions[expired.key].last_active = dt_util.utcnow() - timedelta(
        minutes=29
    )
    await manager.async_record_success(
        expired.key,
        expired.claim_token,
        [conversation.SystemContent(content="bounded")],
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

    await manager.async_release(active.key, active.claim_token)
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
    await manager.async_record_success(first.key, first.claim_token, history)

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
    await manager.async_record_success(owner.key, owner.claim_token, owner_history)

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
        await manager.async_release(guest.key, guest.claim_token)

        guest_follow_up = await manager.async_resolve(
            mode,
            scope,
            device_id,
            guest.conversation_id,
            30,
            namespace=GUEST_CONTINUITY_NAMESPACE,
        )
        await manager.async_release(guest_follow_up.key, guest_follow_up.claim_token)

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
        first.key,
        first.claim_token,
        [conversation.SystemContent(content="expired guest history")],
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
    await manager.async_release(expired.key, expired.claim_token)
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


async def test_stale_success_cannot_mutate_replacement_session() -> None:
    manager = ConversationContinuity("agent")
    scope = user_scope("alice", source="test")
    first = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, None, None, 30
    )
    assert first.key is not None
    assert first.claim_token is not None
    assert await manager.async_end(first.key)

    replacement = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, None, None, 30
    )
    assert replacement.key == first.key
    assert replacement.claim_token is not None
    assert replacement.claim_token != first.claim_token

    stale_history = [conversation.SystemContent(content="stale")]
    await manager.async_record_success(first.key, first.claim_token, stale_history)

    active = manager._sessions[first.key]
    assert active.history == []
    assert active.in_flight is True
    assert active.claim_token == replacement.claim_token

    fresh_history = [conversation.SystemContent(content="fresh")]
    await manager.async_record_success(
        replacement.key, replacement.claim_token, fresh_history
    )
    assert active.history == fresh_history
    assert active.in_flight is False
    assert active.claim_token is None


async def test_stale_release_cannot_clear_newer_claim() -> None:
    manager = ConversationContinuity("agent")
    scope = user_scope("alice", source="test")
    first = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, None, None, 30
    )
    assert first.key is not None
    assert first.claim_token is not None
    await manager.async_record_success(
        first.key,
        first.claim_token,
        [conversation.SystemContent(content="first")],
    )

    second = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, None, None, 30
    )
    assert second.claim_token is not None
    assert second.claim_token != first.claim_token

    await manager.async_release(first.key, first.claim_token)
    active = manager._sessions[first.key]
    assert active.in_flight is True
    assert active.claim_token == second.claim_token

    await manager.async_release(second.key, second.claim_token)
    assert active.in_flight is False
    assert active.claim_token is None


async def test_release_ignores_empty_and_stale_claims() -> None:
    manager = ConversationContinuity("agent")

    await manager.async_release(None, None)
    await manager.async_release("missing", "stale-token")

    assert manager.stats()["active_continuity_sessions"] == 0


async def test_end_missing_session_clears_stale_pending_marker() -> None:
    manager = ConversationContinuity("agent")
    manager._pending_ends.add("missing")

    assert await manager.async_end("missing") is False
    assert "missing" not in manager._pending_ends


async def test_request_end_missing_session_and_deferred_release_cleanup() -> None:
    manager = ConversationContinuity("agent")
    manager._pending_ends.add("missing")

    assert await manager.async_request_end("missing") is False
    assert "missing" not in manager._pending_ends

    resolved = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER,
        user_scope("alice", source="test"),
        None,
        None,
        30,
    )
    assert resolved.key is not None
    assert resolved.claim_token is not None

    assert await manager.async_request_end(resolved.key) is True
    assert resolved.key in manager._pending_ends
    assert resolved.key in manager._sessions

    await manager.async_release(resolved.key, resolved.claim_token)

    assert resolved.key not in manager._sessions
    assert resolved.key not in manager._pending_ends


async def test_ignored_conversation_ids_are_bounded_and_consumed_once() -> None:
    manager = ConversationContinuity("agent")
    limit = continuity._MAX_IGNORED_CONVERSATION_IDS

    ids = [f"conversation-{index}" for index in range(limit + 1)]
    for conversation_id in ids:
        await manager.async_ignore_next_incoming_conversation_id(conversation_id)

    assert len(manager._ignored_conversation_ids) == limit
    assert ids[0] not in manager._ignored_conversation_ids
    assert ids[-1] in manager._ignored_conversation_ids

    first = await manager.async_resolve(
        CONVERSATION_CONTINUITY_HA_DEFAULT,
        user_scope("alice", source="test"),
        None,
        ids[-1],
        30,
    )
    second = await manager.async_resolve(
        CONVERSATION_CONTINUITY_HA_DEFAULT,
        user_scope("alice", source="test"),
        None,
        ids[-1],
        30,
    )

    assert first.conversation_id is None
    assert second.conversation_id == ids[-1]


async def test_memory_bundle_reuses_first_selection_and_clear_reports_state() -> None:
    manager = ConversationContinuity("agent")
    first = [("user", "memory-a")]

    assert await manager.async_get_memory_bundle("continuity:alice", 30) is None
    assert await manager.async_set_memory_bundle("continuity:alice", first, 30) == first

    first.append(("user", "mutated-after-store"))
    reused = await manager.async_set_memory_bundle(
        "continuity:alice", [("user", "memory-b")], 30
    )

    assert reused == [("user", "memory-a")]
    assert await manager.async_get_memory_bundle("continuity:alice", 30) == [
        ("user", "memory-a")
    ]
    assert await manager.async_clear_memory_bundle("continuity:alice") is True
    assert await manager.async_clear_memory_bundle("continuity:alice") is False


async def test_expired_memory_bundle_is_pruned_on_read() -> None:
    manager = ConversationContinuity("agent")
    key = "continuity:alice"
    await manager.async_set_memory_bundle(key, [("user", "memory-a")], 30)
    manager._memory_bundles[key].last_active = dt_util.utcnow() - timedelta(minutes=31)

    assert await manager.async_get_memory_bundle(key, 30) is None
    assert key not in manager._memory_bundles


async def test_list_stats_and_session_removal_include_ephemeral_state() -> None:
    manager = ConversationContinuity("agent")
    resolved = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER,
        user_scope("alice", source="test"),
        None,
        None,
        30,
    )
    assert resolved.key is not None
    await manager.async_release(resolved.key, resolved.claim_token)
    await manager.async_set_memory_bundle(
        f"continuity:{resolved.key}", [("user", "memory-a")], 30
    )

    listing = await manager.async_list(30)
    stats = manager.stats()

    assert listing[0]["key"] == resolved.key
    assert listing[0]["label"] == "Resolved user"
    assert "last_active" in listing[0]
    assert "expires_at" in listing[0]
    assert stats == {
        "active_continuity_sessions": 1,
        "continuity_resume_count": 0,
        "continuity_new_session_count": 1,
        "active_memory_bundles": 1,
    }

    assert await manager.async_end(resolved.key) is True
    assert f"continuity:{resolved.key}" not in manager._memory_bundles


async def test_async_get_continuity_creates_and_reuses_per_agent_manager() -> None:
    hass = SimpleNamespace(data={})

    first = async_get_continuity(hass, "entry", "agent-a")
    again = async_get_continuity(hass, "entry", "agent-a")
    other = async_get_continuity(hass, "entry", "agent-b")

    assert first is again
    assert other is not first
