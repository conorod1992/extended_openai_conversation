"""Review freshness follows selected categories; commit still merges live state."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    backup_transfer as bt,
    transfer,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ArchiveSession,
    ArchiveTurn,
)
from tests.test_restore_recovery import _states
from tests.test_selective_restore_concurrency import RestoreHarness


def _activity(state, category):
    if category == transfer.SECTION_USAGE:
        state.usage_totals.conversation_count += 1
        state.usage_daily.clear()
        state.usage_requests.clear()
        state.usage_runs.clear()
    elif category == transfer.SECTION_TEMPORARY_MEMORY:
        state.temporary_memories.clear()
    elif category == transfer.SECTION_CONVERSATION_ARCHIVE:
        now = state.created_at
        state.archive_sessions.append(
            ArchiveSession(
                "session",
                "conversation",
                "agent-new",
                "user:user-1",
                "user",
                "authenticated",
                None,
                now,
                now,
                "Completed conversation",
                1,
                "retained",
            )
        )
        state.archive_turns.append(
            ArchiveTurn("turn", "session", None, now, "Hello", "Done", True)
        )


@pytest.mark.parametrize(
    "category",
    [
        transfer.SECTION_USAGE,
        transfer.SECTION_TEMPORARY_MEMORY,
        transfer.SECTION_CONVERSATION_ARCHIVE,
    ],
)
@pytest.mark.parametrize("selected_activity", [False, True])
async def test_knowledge_preview_survives_only_unselected_completed_activity(
    hass, tmp_path, monkeypatch, category, selected_activity
):
    tx = RestoreHarness(monkeypatch)
    # Use the complete real transaction harness, including its exclusive gate,
    # second materialization snapshot, all-category apply and durable journal.
    selected = (
        (transfer.SECTION_KNOWLEDGE, category)
        if selected_activity
        else (transfer.SECTION_KNOWLEDGE,)
    )
    tx.imported = replace(tx.imported, available_sections=frozenset(selected))
    tx.state.archive_sessions.clear()
    tx.state.archive_turns.clear()
    path = tmp_path / "upload.json"
    path.write_text("{}")
    current = await transfer._current_snapshot(tx.hass, tx.entry, tx.subentry)
    session = bt.ImportSession(
        "upload",
        tx.entry.entry_id,
        tx.subentry.subentry_id,
        str(path),
        "upload.json",
        2,
        "legacy_json",
        10**12,
        received=2,
        preview_token="preview",
        preview_revision=bt._snapshot_revision(current, selected, {}),
        preview_sections=selected,
        preview_user_scope_mappings=(),
    )
    bt._imports(tx.hass)[session.session_id] = session
    bt._latest_previews(tx.hass)[(tx.entry.entry_id, tx.subentry.subentry_id)] = (
        session.session_id,
        "preview",
    )
    monkeypatch.setattr(bt, "_async_cleanup_expired", AsyncMock())
    monkeypatch.setattr(
        bt, "_async_load_prepared_restore", AsyncMock(return_value=tx.imported)
    )
    monkeypatch.setattr(bt, "_async_remove_path", AsyncMock())
    _activity(tx.state, category)
    latest = deepcopy(tx.state)
    operation = bt._restore_import(
        tx.hass,
        tx.entry,
        tx.subentry,
        {"session_id": "upload", "preview_token": "preview"},
    )
    if selected_activity:
        with pytest.raises(backup.BackupError, match="target changed after preview"):
            await operation
        assert not tx.writes and not tx.journal.saved
    else:
        result = await operation
        assert result["transfer"]["selected_sections"] == [transfer.SECTION_KNOWLEDGE]
        assert tx.state.usage_totals == latest.usage_totals
        assert tx.state.temporary_memories == latest.temporary_memories
        assert tx.state.archive_sessions == latest.archive_sessions
        assert tx.state.archive_turns == latest.archive_turns
        assert tx.state.knowledge == []
        assert tx.journal.data is None
        assert all(owned for owned, _locked in tx.snapshots[1:])


@pytest.mark.parametrize(
    "field", ["title", "config", "request_rules", "knowledge", "provider"]
)
def test_knowledge_review_dependencies_still_invalidate(field):
    state = _states()[0]
    provider = {"api_provider": "openai", "api_key": "private-original"}
    selected = (transfer.SECTION_KNOWLEDGE,)
    original = bt._snapshot_revision(state, selected, provider)
    if field == "provider":
        provider["api_key"] = "private-changed"
    elif field == "title":
        state.title = "Changed"
    elif field == "config":
        state.config["prompt"] = "Changed"
    elif field == "request_rules":
        state.request_rules["rules"].append({"name": "changed dependency"})
    else:
        state.knowledge.clear()
    assert bt._snapshot_revision(state, selected, provider) != original


def test_archive_bindings_only_matter_when_archive_is_selected():
    state = _states()[0]
    before = deepcopy(state)
    state.archive_active = {"conversation": "session"}
    assert bt._snapshot_revision(
        state, (transfer.SECTION_KNOWLEDGE,)
    ) == bt._snapshot_revision(before, (transfer.SECTION_KNOWLEDGE,))
    assert bt._snapshot_revision(
        state, (transfer.SECTION_CONVERSATION_ARCHIVE,)
    ) != bt._snapshot_revision(before, (transfer.SECTION_CONVERSATION_ARCHIVE,))


def test_internal_provenance_and_export_metadata_do_not_stale_even_full_restore():
    state = _states()[0]
    changed = replace(
        state,
        created_at="later",
        integration_version="later",
        authoritative_config=not state.authoritative_config,
        preserve_retained_continuity=True,
    )
    assert bt._snapshot_revision(state) == bt._snapshot_revision(changed)


@pytest.mark.parametrize(
    "section,field",
    [
        (transfer.SECTION_PERSISTENT_MEMORY, "memories"),
        (transfer.SECTION_TEMPORARY_MEMORY, "temporary_memories"),
        (transfer.SECTION_KNOWLEDGE, "knowledge"),
        (transfer.SECTION_CONVERSATION_ARCHIVE, "archive_sessions"),
        (transfer.SECTION_CONVERSATION_ARCHIVE, "archive_turns"),
        (transfer.SECTION_CONVERSATION_ARCHIVE, "archive_active"),
        (transfer.SECTION_USAGE, "usage_totals"),
        (transfer.SECTION_USAGE, "usage_daily"),
        (transfer.SECTION_USAGE, "usage_requests"),
        (transfer.SECTION_USAGE, "usage_runs"),
        (transfer.SECTION_GUEST_MODE, "guest_mode_schedule"),
    ],
)
def test_each_selected_category_field_and_full_restore_detect_changes(section, field):
    state = _states()[0]
    before = deepcopy(state)
    # Hash sensitivity is separate from category validation (covered by native
    # restore tests); exercise even fields that start empty in this fixture.
    setattr(state, field, {"changed": True})
    assert bt._snapshot_revision(state, (section,)) != bt._snapshot_revision(
        before, (section,)
    )
    assert bt._snapshot_revision(state) != bt._snapshot_revision(before)
