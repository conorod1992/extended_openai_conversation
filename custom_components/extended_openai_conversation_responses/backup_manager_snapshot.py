"""Lock-held synchronous backup copies for mutable agent managers."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from homeassistant.util import dt as dt_util

from .backup_snapshot import BackupSnapshotAdapter
from .conversation_archive import ConversationArchive
from .guest_mode import GuestModeManager
from .knowledge import KnowledgeLibrary
from .memory import PersistentMemory, _record_as_storage_dict
from .request_rules import RequestRules, STORAGE_VERSION as REQUEST_RULES_STORAGE_VERSION
from .temporary_memory import TemporaryMemory, _parse_expiry
from .usage import UsageManager


def _require_initialized(manager: Any, name: str) -> None:
    if not manager._initialized:
        raise RuntimeError(f"{name} has not been initialized")


def _memory_snapshot(manager: PersistentMemory) -> dict[str, Any]:
    _require_initialized(manager, "persistent memory")
    return {
        "memories": [
            _record_as_storage_dict(memory) for memory in manager._memories.values()
        ]
    }


def _temporary_memory_snapshot(manager: TemporaryMemory) -> dict[str, Any]:
    _require_initialized(manager, "temporary memory")
    now = dt_util.utcnow()
    return {
        "records": [
            asdict(record)
            for record in manager._records.values()
            if _parse_expiry(record.expires_at) > now
        ]
    }


def _knowledge_snapshot(manager: KnowledgeLibrary) -> dict[str, Any]:
    _require_initialized(manager, "Knowledge Library")
    return {"sources": [asdict(source) for source in manager._sources.values()]}


def _archive_snapshot(manager: ConversationArchive) -> dict[str, Any]:
    _require_initialized(manager, "conversation archive")
    sessions = [
        session
        for session in manager._sessions.values()
        if session.retention_state != "unretained"
    ]
    retained_ids = {session.session_id for session in sessions}
    return {
        "sessions": [asdict(session) for session in sessions],
        "turns": [
            asdict(turn)
            for session_id, turns in manager._turns.items()
            if session_id in retained_ids
            for turn in turns
        ],
    }


def _usage_snapshot(manager: UsageManager) -> dict[str, Any]:
    _require_initialized(manager, "usage statistics")
    return {
        "totals": manager.as_dict(),
        "daily": deepcopy(manager.daily),
        "requests": [asdict(request) for request in manager.requests],
        "runs": [asdict(run) for run in manager.runs],
    }


def _guest_mode_snapshot(manager: GuestModeManager) -> dict[str, Any]:
    _require_initialized(manager, "Guest Mode")
    return {"schedule": asdict(manager._schedule) if manager._schedule else None}


def _request_rules_snapshot(manager: RequestRules) -> dict[str, Any]:
    _require_initialized(manager, "Request Rules")
    return {
        "storage_version": REQUEST_RULES_STORAGE_VERSION,
        "defaults": dict(manager._defaults),
        "wording_groups": deepcopy(manager._wording_groups),
        "rules": deepcopy(manager._rules),
    }


def manager_snapshot_participants(
    memory: PersistentMemory,
    temporary_memory: TemporaryMemory,
    knowledge: KnowledgeLibrary,
    archive: ConversationArchive,
    usage: UsageManager,
    guest_mode: GuestModeManager,
    request_rules: RequestRules,
) -> tuple[BackupSnapshotAdapter, ...]:
    """Return deterministic adapters over all durable mutable agent managers."""
    return (
        BackupSnapshotAdapter(memory._lock, lambda: _memory_snapshot(memory)),
        BackupSnapshotAdapter(
            temporary_memory._lock,
            lambda: _temporary_memory_snapshot(temporary_memory),
        ),
        BackupSnapshotAdapter(knowledge._lock, lambda: _knowledge_snapshot(knowledge)),
        BackupSnapshotAdapter(archive._lock, lambda: _archive_snapshot(archive)),
        BackupSnapshotAdapter(usage._lock, lambda: _usage_snapshot(usage)),
        BackupSnapshotAdapter(
            guest_mode._lock,
            lambda: _guest_mode_snapshot(guest_mode),
        ),
        BackupSnapshotAdapter(
            request_rules._lock,
            lambda: _request_rules_snapshot(request_rules),
        ),
    )
