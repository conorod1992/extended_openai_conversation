"""Residual ownership, persistence, cancellation and Quiet Hours contracts."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest

from custom_components import extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import (
    agent_deletion,
    delayed_tools,
    quiet_hours_runtime as quiet,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONVERSATION_CONTINUITY_USER,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    ConversationContinuity,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from custom_components.extended_openai_conversation_responses.skills import SkillManager
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from tests.test_memory import _memory


async def test_memory_browse_is_owner_filtered_case_insensitive_and_paged():
    memory = await _memory()
    await memory.async_add("alice", "Kitchen lights", "devices", "explicit")
    await memory.async_add("alice", "Kitchen temperature", "devices", "explicit")
    await memory.async_add("bob", "Kitchen private setting", "devices", "explicit")
    page, total = await memory.async_browse("alice", "KITCHEN", limit=1)
    assert total == 2 and len(page) == 1
    second, second_total = await memory.async_browse(
        "alice", "kitchen", limit=1, offset=1
    )
    assert second_total == total
    assert second[0].memory_id != page[0].memory_id
    assert all(item.user_id == "alice" for item in [*page, *second])
    assert memory.memory_count == 3
    assert await memory.async_browse("alice", "absent") == ([], 0)


async def test_memory_rejects_bad_revision_before_mutating():
    memory = await _memory()
    created = await memory.async_add("alice", "Retain this", "general", "explicit")
    with pytest.raises(ValueError, match="expected_revision is invalid"):
        await memory.async_update(
            "alice",
            created["memory"]["memory_id"],
            "Changed",
            expected_revision="not-a-revision",
        )
    assert (await memory.async_list("alice"))[0].content == "Retain this"


async def test_memory_committed_rollback_invalidates_regenerable_embeddings():
    memory = await _memory()
    await memory.async_add("alice", "Committed fact", "general", "explicit")
    memory._memories.clear()
    memory._embedding_cache["stale"] = object()
    memory._restore_committed_state()
    assert (await memory.async_list("alice"))[0].content == "Committed fact"
    assert memory._embedding_cache == {}
    assert memory._embedding_cache_dirty is True


async def test_memory_without_embedding_storage_settles_dirty_cache():
    memory = await _memory()
    memory._embedding_cache_dirty = True
    assert await memory._async_save_embedding_cache_locked() is True
    assert not memory._embedding_cache_dirty
    assert memory._committed_state is not None


@pytest.mark.parametrize("new_id", ["replacement", None])
async def test_owned_continuity_replacement_clears_history_and_memory_selection(new_id):
    manager = ConversationContinuity("agent")
    scope = user_scope("alice", source="test")
    first = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, None, None, 30
    )
    await manager.async_record_success(
        first.key,
        first.claim_token,
        [conversation.SystemContent(content="old history")],
    )
    active = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, None, None, 30
    )
    await manager.async_set_memory_bundle(
        f"continuity:{active.key}", [("alice", "old-memory")], 30
    )
    changed = await manager.async_replace_conversation_id(active, new_id)
    assert changed.history == [] and not changed.resumed
    assert manager._sessions[active.key].history == []
    assert manager._sessions[active.key].conversation_id == (
        new_id or active.conversation_id
    )
    assert await manager.async_get_memory_bundle(f"continuity:{active.key}", 30) is None
    await manager.async_release(changed.key, changed.claim_token)


async def test_stale_continuity_claim_cannot_replace_current_session():
    manager = ConversationContinuity("agent")
    scope = user_scope("alice", source="test")
    old = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, None, None, 30
    )
    await manager.async_record_success(
        old.key, old.claim_token, [conversation.SystemContent(content="retained")]
    )
    active = await manager.async_resolve(
        CONVERSATION_CONTINUITY_USER, scope, None, None, 30
    )
    await manager.async_replace_conversation_id(old, "intruder")
    assert manager._sessions[active.key].conversation_id == active.conversation_id
    assert manager._sessions[active.key].history[0].content == "retained"
    await manager.async_release(active.key, active.claim_token)


@pytest.mark.parametrize("inner_fails", [False, True])
async def test_skill_mutation_retains_lock_through_repeated_cancellation(
    hass, inner_fails
):
    manager = SkillManager(hass)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def operation():
        entered.set()
        await finish.wait()
        if inner_fails:
            raise OSError("filesystem failure after cancellation")

    task = asyncio.create_task(manager._async_run_locked(operation))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert manager._filesystem_lock.locked()
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not manager._filesystem_lock.locked()


def test_failed_staged_skill_activation_restores_original(tmp_path, monkeypatch):
    staged, target, backup = (
        tmp_path / name for name in ("staged", "target", "backup")
    )
    staged.mkdir()
    target.mkdir()
    (target / "SKILL.md").write_text("original")
    (staged / "SKILL.md").write_text("candidate")
    original = type(staged).rename

    def rename(path, destination):
        if path == staged:
            raise OSError("publish failed")
        return original(path, destination)

    monkeypatch.setattr(type(staged), "rename", rename)
    with pytest.raises(OSError, match="publish failed"):
        SkillManager._activate_staged_skill_sync(staged, target, backup)
    assert (target / "SKILL.md").read_text() == "original"
    assert (staged / "SKILL.md").read_text() == "candidate"
    assert not backup.exists()


def test_staged_skill_rollback_restores_previous_installation(tmp_path):
    target, backup = tmp_path / "target", tmp_path / "backup"
    target.mkdir()
    backup.mkdir()
    (target / "SKILL.md").write_text("new")
    (backup / "SKILL.md").write_text("old")
    SkillManager._rollback_staged_skill_sync(target, backup)
    assert (target / "SKILL.md").read_text() == "old"
    assert not backup.exists()


def test_quiet_hours_normalizes_spring_clock_gap():
    now = datetime(2026, 3, 29, 2, 45, tzinfo=ZoneInfo("Europe/Dublin"))
    period = quiet.quiet_period_for(now, "01:30", "03:00")
    assert period is not None
    assert period.start.hour == 2 and period.start.minute == 30
    assert (
        period.start.astimezone(UTC) <= now.astimezone(UTC) < period.end.astimezone(UTC)
    )


async def test_agent_deletion_removes_delayed_calls(hass):
    delayed = SimpleNamespace(async_remove_agent=AsyncMock())
    hass.data[DOMAIN] = {delayed_tools.DATA_DELAYED_TOOL_MANAGER: delayed}
    await agent_deletion.async_delete_agent_data(hass, "entry", "agent")
    delayed.async_remove_agent.assert_awaited_once_with("entry", "agent")


@pytest.mark.parametrize("loaded,sole", [(False, True), (True, False)])
async def test_prewarm_skips_unloaded_or_ambiguous_agents(
    hass, monkeypatch, loaded, sole
):
    child = SimpleNamespace(subentry_type="conversation")
    entry = SimpleNamespace(
        state=ConfigEntryState.LOADED if loaded else ConfigEntryState.NOT_LOADED,
        subentries={"one": child, **({"two": child} if not sole else {})},
    )
    hass.config_entries.async_entries.return_value = [entry]
    warm = AsyncMock()
    monkeypatch.setattr(integration, "async_prewarm_persisted_config_projection", warm)
    await integration._async_prewarm_sole_agent(hass, entry)
    warm.assert_not_awaited()


def test_failed_background_prewarm_creation_closes_coroutine(hass):
    child = SimpleNamespace(subentry_type="conversation")
    entry = SimpleNamespace(
        state=ConfigEntryState.LOADED,
        subentries={"one": child},
        async_on_state_change=Mock(return_value=Mock()),
        async_on_unload=Mock(),
        async_create_background_task=Mock(side_effect=RuntimeError("shutdown")),
    )
    hass.config_entries.async_entries.return_value = [entry]
    integration._schedule_sole_agent_prewarm(hass, entry)
    coroutine = entry.async_create_background_task.call_args.args[1]
    assert coroutine.cr_frame is None


async def test_removing_one_provider_preserves_shared_services_for_other_entries(
    hass, monkeypatch
):
    entry = SimpleNamespace(entry_id="removed", subentries={})
    hass.config_entries.async_entries.return_value = [
        SimpleNamespace(entry_id="retained")
    ]
    erase = AsyncMock()
    monkeypatch.setattr(agent_deletion, "async_delete_entry_data", erase)
    await integration.async_remove_entry(hass, entry)
    erase.assert_awaited_once_with(hass, entry)
    hass.services.async_remove.assert_not_called()
    assert not hass.data.get(f"{DOMAIN}.removed")
