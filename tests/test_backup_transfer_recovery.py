"""Regression tests for scalable backup restore recovery."""

from __future__ import annotations

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses import restore_recovery


def test_restore_journal_uses_scalable_backup_limit(monkeypatch) -> None:
    """Pending recovery must accept the same bounded payload size as chunked restore."""
    calls: list[tuple[object, str, int]] = []
    target = object()
    rollback = object()

    def inspect(value, subentry_id, *, max_bytes):
        calls.append((value, subentry_id, max_bytes))
        return target if len(calls) == 1 else rollback

    monkeypatch.setattr(backup, "inspect_backup", inspect)
    journal = {
        "transaction_id": "transaction-1",
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "phase": "applying",
        "created_at": "2026-09-07T12:00:00+00:00",
        "target": {"large": "target"},
        "rollback": {"large": "rollback"},
    }

    loaded, loaded_target, loaded_rollback = restore_recovery._load_journal(
        journal, "entry-1", "agent-1"
    )

    assert loaded == journal
    assert loaded_target is target
    assert loaded_rollback is rollback
    assert calls == [
        (journal["target"], "agent-1", backup.MAX_BACKUP_BYTES),
        (journal["rollback"], "agent-1", backup.MAX_BACKUP_BYTES),
    ]
