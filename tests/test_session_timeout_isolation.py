"""Idle timeouts belong to the retained object, not the next caller."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    continuity,
    function_groups,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    ConversationContinuity,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    FunctionGroupRuntime,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope


def test_group_timeouts_are_isolated_and_keep_strict_boundary(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(function_groups.time, "monotonic", lambda: clock[0])
    runtime = FunctionGroupRuntime()
    long = runtime.begin("long", 30)
    long.loaded_group_ids.add("original")
    short = runtime.begin("short", 5)
    clock[0] = 300
    runtime.begin("unrelated", 30)
    assert runtime._sessions["short"] is short  # Strict <, not <=.
    clock[0] += 1
    runtime.begin("fallback", 5)
    assert runtime._sessions["long"] is long
    assert long.loaded_group_ids == {"original"}
    assert long.last_active == 0
    assert long.timeout_minutes == 30
    assert "short" not in runtime._sessions
    assert runtime.begin("long", 1) is long
    assert long.timeout_minutes == 1
    clock[0] += 61
    runtime.begin("another", 30)
    assert "long" not in runtime._sessions


def test_group_active_request_refresh_and_timeout_normalization(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(function_groups.time, "monotonic", lambda: clock[0])
    runtime = FunctionGroupRuntime()
    session = runtime.begin("active", 0)
    assert session.timeout_minutes == 1
    token = runtime.begin_request(session)
    clock[0] = 1000
    runtime.begin("unrelated", 30)
    assert runtime._sessions["active"] is session
    assert runtime.begin("active", 10) is session
    clock[0] += 1000
    runtime.finish_request(token, successful=True)
    assert session.last_active == clock[0]
    clock[0] += 599
    runtime.begin("fallback", 5)
    assert runtime._sessions["active"] is session
    clock[0] += 2
    runtime.begin("unrelated", 30)
    assert "active" not in runtime._sessions
    replacement = runtime.begin("active", -1)
    assert replacement is not session
    assert replacement.timeout_minutes == 1
    token = runtime.begin_request(replacement)
    runtime.end("active")
    runtime.finish_request(token, successful=True)
    assert "active" not in runtime._sessions


@pytest.mark.parametrize("operation", ["get", "set"])
async def test_memory_timeouts_prune_both_directions_without_touching_others(
    monkeypatch, operation
):
    clock = [datetime(2026, 1, 1, tzinfo=UTC)]
    monkeypatch.setattr(continuity.dt_util, "utcnow", lambda: clock[0])
    manager = ConversationContinuity("agent")
    await manager.async_set_memory_bundle("long", [("alice", "original")], 30)
    await manager.async_set_memory_bundle("short", [], 5)
    long = manager._memory_bundles["long"]

    async def unrelated(timeout):
        if operation == "get":
            return await manager.async_get_memory_bundle("unrelated", timeout)
        return await manager.async_set_memory_bundle("unrelated", [], timeout)

    clock[0] += timedelta(minutes=5)
    await unrelated(30)
    assert "short" in manager._memory_bundles
    clock[0] += timedelta(seconds=1)
    await unrelated(5)
    assert manager._memory_bundles["long"] is long
    assert long.timeout_minutes == 30
    assert long.last_active == long.selected_at
    assert "short" not in manager._memory_bundles
    # Also exercise the long caller directly, with an expired short object.
    await manager.async_set_memory_bundle("short", [], 1)
    clock[0] += timedelta(seconds=61)
    await unrelated(30)
    assert "short" not in manager._memory_bundles
    assert await manager.async_get_memory_bundle("long", 30) == [("alice", "original")]


@pytest.mark.parametrize("operation", ["get", "set"])
async def test_memory_access_updates_only_timeout_and_activity_and_copies_references(
    monkeypatch, operation
):
    clock = [datetime(2026, 1, 1, tzinfo=UTC)]
    monkeypatch.setattr(continuity.dt_util, "utcnow", lambda: clock[0])
    manager = ConversationContinuity("agent")
    refs = [("alice", "original")]
    await manager.async_set_memory_bundle("session", refs, 30)
    bundle = manager._memory_bundles["session"]
    refs.clear()
    selected_at = bundle.selected_at
    clock[0] += timedelta(minutes=6)
    if operation == "get":
        result = await manager.async_get_memory_bundle("session", 1)
    else:
        result = await manager.async_set_memory_bundle(
            "session", [("alice", "later")], 1
        )
    assert result == [("alice", "original")]
    result.clear()
    assert bundle.references == [("alice", "original")]
    assert bundle.selected_at == selected_at
    assert bundle.last_active == clock[0]
    assert bundle.timeout_minutes == 1
    clock[0] += timedelta(seconds=61)
    # A new, longer timeout cannot revive expired state.
    assert await manager.async_get_memory_bundle("session", 30) is None
    await manager.async_set_memory_bundle("empty", [], 0)
    assert await manager.async_get_memory_bundle("empty", 0) == []
    clock[0] += timedelta(microseconds=1)
    assert await manager.async_get_memory_bundle("empty", 30) is None


async def test_conversation_fallback_preserves_original_groups_and_memory(monkeypatch):
    from custom_components.extended_openai_conversation_responses.continuity import (
        ContinuityResolution,
    )
    from custom_components.extended_openai_conversation_responses.conversation import (
        _ACTIVE_TURN,
    )
    from tests.test_conversation_orchestration import _pipeline_fixture

    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock = [0.0]
    monkeypatch.setattr(function_groups.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        continuity.dt_util, "utcnow", lambda: now + timedelta(seconds=clock[0])
    )
    entity, user_input, chat_log, policy, process = _pipeline_fixture(monkeypatch)
    manager = ConversationContinuity("agent")
    # Keep the real memory bundle API; history recording is outside this probe.
    manager.async_record_success = AsyncMock()
    entity._continuity = manager
    entity._function_groups_runtime = FunctionGroupRuntime()
    entity._memory = SimpleNamespace(
        async_get_many=AsyncMock(side_effect=lambda refs, _: refs)
    )
    entity._current_readable_memory_scope_ids = lambda _: ["alice"]
    entity._async_rank_memories = AsyncMock(
        return_value=[SimpleNamespace(user_id="alice", memory_id="first")]
    )
    seen = []

    async def handle(*_args, **_kwargs):
        groups = _ACTIVE_TURN.get().function_group_session
        refs = await entity._async_select_memories(None, "query")
        seen.append((groups, refs, _ACTIVE_TURN.get().memory_session))
        groups.loaded_group_ids.add("original")
        return SimpleNamespace(response=SimpleNamespace(error_code=None))

    entity._async_handle_message_with_ha_tools = AsyncMock(side_effect=handle)
    await process()
    original = seen[0][0]
    clock[0] = 360
    fallback = ContinuityResolution("fallback", None, [], False)
    chat_log.conversation_id = "fallback"
    await entity._async_process_claimed(
        user_input,
        SimpleNamespace(context=user_input.context),
        policy,
        user_scope("alice", source="test"),
        None,
        15,
        fallback,
    )
    assert seen[1][2] == ("conversation:fallback", 5)
    entity._async_rank_memories.return_value = [
        SimpleNamespace(user_id="alice", memory_id="later")
    ]
    chat_log.conversation_id = "conversation-1"
    await process()
    assert seen[2][0] is original
    assert original.loaded_group_ids == {"original"}
    assert seen[2][1] == [("alice", "first")]
    assert entity._async_rank_memories.await_count == 2


@pytest.mark.parametrize("operation", ["get", "set"])
async def test_same_memory_session_can_extend_its_timeout(monkeypatch, operation):
    clock = [datetime(2026, 1, 1, tzinfo=UTC)]
    monkeypatch.setattr(continuity.dt_util, "utcnow", lambda: clock[0])
    manager = ConversationContinuity("agent")
    await manager.async_set_memory_bundle("session", [], 1)
    clock[0] += timedelta(seconds=30)
    if operation == "get":
        assert await manager.async_get_memory_bundle("session", 30) == []
    else:
        assert (
            await manager.async_set_memory_bundle("session", [("alice", "later")], 30)
            == []
        )
    clock[0] += timedelta(minutes=6)
    assert await manager.async_get_memory_bundle("session", 30) == []


def test_session_constructors_keep_original_positional_arguments():
    groups = {"original"}
    session = function_groups.FunctionGroupSession("session", 0, groups)
    assert session.loaded_group_ids is groups
    references = [("alice", "first")]
    now = datetime(2026, 1, 1, tzinfo=UTC)
    bundle = continuity.ConversationMemoryBundle(references, now, now)
    assert bundle.references is references
