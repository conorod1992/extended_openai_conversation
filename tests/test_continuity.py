"""Focused conversation-continuity tests."""

from datetime import timedelta

from custom_components.extended_openai_conversation_responses.const import (
    CONVERSATION_CONTINUITY_DEVICE,
    CONVERSATION_CONTINUITY_HA_DEFAULT,
    CONVERSATION_CONTINUITY_USER,
)
from custom_components.extended_openai_conversation_responses.continuity import (
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
