"""Residual coverage for conversation continuity cleanup and caches."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses import continuity
from custom_components.extended_openai_conversation_responses.const import (
    CONVERSATION_CONTINUITY_HA_DEFAULT,
    CONVERSATION_CONTINUITY_USER,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    ConversationContinuity,
    async_get_continuity,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from homeassistant.util import dt as dt_util


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
