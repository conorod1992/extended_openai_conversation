"""Unloaded deletion and readable restore-journal regressions."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import custom_components.extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import (
    agent_deletion,
    backup,
    restore_recovery,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.knowledge import (
    KnowledgeSource,
)
from homeassistant.helpers.storage import Store
from tests.test_restore_recovery import (
    FakeHass,
    MemoryJournalStore,
    _identity,
    _recover_with_journal,
    _states,
)


async def test_startup_reconciles_disabled_parent_without_tracking(hass, tmp_path, monkeypatch):
    root = tmp_path / ".storage"
    root.mkdir()
    removed = [f"{DOMAIN}.{section}.entry.removed{suffix}" for section, suffix in [
        ("memory", ""), ("memory", ".embeddings"), ("archive", ".turns.2026-10"),
        ("knowledge", ""), ("restore_transaction", ""),
    ]]
    retained = [f"{DOMAIN}.memory.entry.kept", f"{DOMAIN}.memory.entry.removed_other", f"{DOMAIN}.memory.other.removed"]
    for name in removed + retained:
        (root / name).write_text("private data", encoding="utf-8")
    async def remove(store):
        Path(store.path).unlink(missing_ok=True)
    monkeypatch.setattr(Store, "async_remove", remove)
    entry = SimpleNamespace(entry_id="entry", disabled_by="user", subentries={"kept": object(), "removed_other": object()})
    hass.config_entries.async_entries.return_value = [entry]
    hass.data.pop(agent_deletion.KNOWN_AGENTS, None)
    await agent_deletion.async_reconcile_agent_storage(hass)
    assert sorted(path.name for path in root.iterdir()) == sorted(retained)
    await agent_deletion.async_reconcile_agent_storage(hass)
    assert sorted(path.name for path in root.iterdir()) == sorted(retained)


async def test_reenable_reconciles_before_provider_authentication(hass, tmp_path, monkeypatch):
    root = tmp_path / ".storage"
    root.mkdir()
    orphan = root / f"{DOMAIN}.memory.entry.removed"
    orphan.write_text("private", encoding="utf-8")
    async def remove(store):
        Path(store.path).unlink(missing_ok=True)
    monkeypatch.setattr(Store, "async_remove", remove)
    entry = SimpleNamespace(entry_id="entry", data={"api_key": "invalid"}, subentries={})
    async def authenticate(**_):
        assert not orphan.exists()
        raise RuntimeError("provider unavailable")
    monkeypatch.setattr(integration, "get_authenticated_client", authenticate)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await integration.async_setup_entry(hass, entry)


def _oversized_states(monkeypatch):
    target, rollback = _states()
    rollback.knowledge.append(KnowledgeSource("large", "Reference", "", "\U0001f600" * 10000,
        "2026-10-12T00:00:00+00:00", "2026-10-12T00:00:00+00:00"))
    journal = restore_recovery._new_journal("entry-1", "agent-new", target, rollback)
    size = len(json.dumps(journal["target"], ensure_ascii=False, separators=(",", ":")).encode())
    # Exercise the production boundary with a small cap instead of allocating
    # hundreds of MiB. The rollback is valid but exceeds this reader's byte cap.
    monkeypatch.setattr(backup, "MAX_BACKUP_BYTES", size + 1024)
    return target, rollback, journal


async def test_oversized_rollback_is_rejected_before_journal_or_mutation(monkeypatch):
    target, rollback, _ = _oversized_states(monkeypatch)
    hass, store = FakeHass(), MemoryJournalStore()
    entry, subentry = _identity()
    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_: store)
    monkeypatch.setattr(restore_recovery, "_durable_managers", AsyncMock(return_value=(object(),) * 7))
    monkeypatch.setattr(backup, "_snapshot_for_restore", AsyncMock(return_value=rollback))
    apply = AsyncMock()
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)
    with pytest.raises(backup.BackupError, match="rollback recovery snapshot is not recoverable"):
        await restore_recovery.async_restore_backup_recoverably(hass, entry, subentry, target)
    assert not store.saved
    apply.assert_not_awaited()
    assert not hass.config_entries.updates


async def test_older_committed_oversized_rollback_recovers_authoritative_target(monkeypatch):
    _, _, journal = _oversized_states(monkeypatch)
    with pytest.raises(backup.BackupError, match="corrupted"):
        restore_recovery._load_journal(journal, "entry-1", "agent-new")
    journal["phase"] = "committed"
    _, _, subentry, store, selected, recovered = await _recover_with_journal(monkeypatch, journal)
    assert recovered
    assert selected == ["Restored"]
    assert subentry.title == "Restored"
    assert store.data is None
