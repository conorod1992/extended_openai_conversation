"""Restart-safe transaction handling for full agent restores."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import asdict
from functools import wraps
import logging
from typing import Any, cast
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import backup
from .const import DOMAIN, SUBSYSTEM_STATUS_KEY

_LOGGER = logging.getLogger(__name__)

RESTORE_JOURNAL_VERSION = 1
RESTORE_JOURNAL_PREFIX = f"{DOMAIN}.restore_transaction"
_PHASE_APPLYING = "applying"
_PHASE_COMMITTED = "committed"
_PHASE_ROLLING_BACK = "rolling_back"
_PHASES = {_PHASE_APPLYING, _PHASE_COMMITTED, _PHASE_ROLLING_BACK}
_CATEGORIES = (
    "persistent_memory",
    "temporary_memory",
    "knowledge",
    "archive",
    "usage",
    "guest_mode",
    "request_rules",
    "configuration",
)
_INSTALLED = False


def _journal_store(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> Store[dict[str, Any]]:
    """Return the deterministic private journal for one exact agent."""
    return Store(
        hass,
        RESTORE_JOURNAL_VERSION,
        f"{RESTORE_JOURNAL_PREFIX}.{entry_id}.{subentry_id}",
        private=True,
        atomic_writes=True,
        serialize_in_event_loop=False,
    )


def _prepared_document(
    prepared: backup.PreparedRestore, entry_id: str, subentry_id: str
) -> dict[str, Any]:
    """Serialize validated restore state for the private local journal."""
    return {
        "format": backup.BACKUP_FORMAT,
        "version": backup.BACKUP_VERSION,
        "created_at": prepared.created_at,
        "integration_version": prepared.integration_version,
        "agent": {
            "title": prepared.title,
            "source_entry_id": entry_id,
            "source_subentry_id": subentry_id,
            # The journal is private local recovery state, so it must retain the
            # exact effective configuration needed for deterministic rollback.
            "config": deepcopy(prepared.config),
        },
        "memories": {"memories": [asdict(item) for item in prepared.memories]},
        "temporary_memories": {
            "records": [asdict(item) for item in prepared.temporary_memories]
        },
        "knowledge": {"sources": [asdict(item) for item in prepared.knowledge]},
        "archive": {
            "sessions": [asdict(item) for item in prepared.archive_sessions],
            "turns": [asdict(item) for item in prepared.archive_turns],
        },
        "usage": {
            "totals": asdict(prepared.usage_totals),
            "daily": deepcopy(prepared.usage_daily),
            "requests": [asdict(item) for item in prepared.usage_requests],
            "runs": [asdict(item) for item in prepared.usage_runs],
        },
        "guest_mode": {
            "schedule": (
                asdict(prepared.guest_mode_schedule)
                if prepared.guest_mode_schedule is not None
                else None
            )
        },
        "request_rules": deepcopy(prepared.request_rules),
    }


def _new_journal(
    entry_id: str,
    subentry_id: str,
    target: backup.PreparedRestore,
    rollback: backup.PreparedRestore,
) -> dict[str, Any]:
    """Build the complete write-ahead record before destructive work begins."""
    return {
        "transaction_id": uuid4().hex,
        "entry_id": entry_id,
        "subentry_id": subentry_id,
        "phase": _PHASE_APPLYING,
        "completed_categories": [],
        "rollback_completed_categories": [],
        "created_at": dt_util.utcnow().isoformat(),
        "target": _prepared_document(target, entry_id, subentry_id),
        "rollback": _prepared_document(rollback, entry_id, subentry_id),
    }


def _validate_category_list(value: Any, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or item not in _CATEGORIES for item in value)
        or len(set(value)) != len(value)
    ):
        raise backup.BackupError(f"Pending restore {field} is corrupted")
    return list(value)


def _load_journal(
    value: Any, entry_id: str, subentry_id: str
) -> tuple[dict[str, Any], backup.PreparedRestore, backup.PreparedRestore]:
    """Validate a pending transaction without trusting mutable journal fields."""
    required = {
        "transaction_id",
        "entry_id",
        "subentry_id",
        "phase",
        "completed_categories",
        "rollback_completed_categories",
        "created_at",
        "target",
        "rollback",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise backup.BackupError("Pending restore transaction is corrupted")
    transaction_id = value["transaction_id"]
    phase = value["phase"]
    created_at = value["created_at"]
    if (
        not isinstance(transaction_id, str)
        or not transaction_id
        or value["entry_id"] != entry_id
        or value["subentry_id"] != subentry_id
        or phase not in _PHASES
        or not isinstance(created_at, str)
        or dt_util.parse_datetime(created_at) is None
    ):
        raise backup.BackupError("Pending restore transaction is corrupted")
    journal = dict(value)
    journal["completed_categories"] = _validate_category_list(
        value["completed_categories"], "progress"
    )
    journal["rollback_completed_categories"] = _validate_category_list(
        value["rollback_completed_categories"], "rollback progress"
    )
    try:
        target = backup.inspect_backup(value["target"], subentry_id)
        rollback = backup.inspect_backup(value["rollback"], subentry_id)
    except HomeAssistantError as err:
        raise backup.BackupError("Pending restore transaction is corrupted") from err
    return journal, target, rollback


async def _save_progress(
    store: Store[dict[str, Any]],
    journal: dict[str, Any],
    field: str,
    category: str,
) -> None:
    """Durably record a completed category without relying on in-memory progress."""
    completed = cast(list[str], journal[field])
    if category not in completed:
        completed.append(category)
    await store.async_save(deepcopy(journal))


async def _set_phase(
    store: Store[dict[str, Any]], journal: dict[str, Any], phase: str
) -> None:
    journal["phase"] = phase
    await store.async_save(deepcopy(journal))


async def _durable_managers(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> tuple[Any, ...]:
    """Load the actual durable managers; restore must never target Usage fallback RAM."""
    from .conversation_archive import async_get_archive
    from .guest_mode import async_get_guest_mode
    from .knowledge import async_get_knowledge
    from .memory import async_get_memory
    from .request_rules import async_get_request_rules
    from .runtime_failure_hardening import _ORIGINAL_ASYNC_GET_USAGE
    from .temporary_memory import async_get_temporary_memory

    return (
        await async_get_memory(hass, entry_id, subentry_id),
        await async_get_temporary_memory(hass, entry_id, subentry_id),
        await async_get_knowledge(hass, entry_id, subentry_id),
        await async_get_archive(hass, entry_id, subentry_id),
        await _ORIGINAL_ASYNC_GET_USAGE(hass, entry_id, subentry_id),
        await async_get_guest_mode(hass, entry_id, subentry_id),
        await async_get_request_rules(hass, entry_id, subentry_id),
    )


async def _apply_prepared(
    managers: tuple[Any, ...],
    prepared: backup.PreparedRestore,
    progress: Callable[[str], Awaitable[None]],
) -> None:
    """Apply validated categories in a deterministic, idempotent order."""
    (
        memory,
        temporary,
        knowledge,
        archive,
        usage,
        guest_mode,
        request_rules,
    ) = managers
    usage.request_retention_days = int(
        prepared.config.get(
            backup.CONF_USAGE_REQUEST_RETENTION_DAYS,
            backup.DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
        )
    )
    usage.run_retention_days = int(
        prepared.config.get(
            backup.CONF_USAGE_RUN_RETENTION_DAYS,
            backup.DEFAULT_USAGE_RUN_RETENTION_DAYS,
        )
    )

    await memory.async_replace_backup(prepared.memories)
    await progress("persistent_memory")
    await temporary.async_replace_backup(prepared.temporary_memories)
    await progress("temporary_memory")
    await knowledge.async_replace_backup(prepared.knowledge)
    await progress("knowledge")
    await archive.async_replace_backup(prepared.archive_sessions, prepared.archive_turns)
    await progress("archive")
    await usage.async_replace_backup(
        prepared.usage_totals,
        prepared.usage_daily,
        prepared.usage_requests,
        prepared.usage_runs,
    )
    await progress("usage")
    await guest_mode.async_replace_backup(prepared.guest_mode_schedule)
    await progress("guest_mode")
    await request_rules.async_replace_backup(prepared.request_rules)
    await progress("request_rules")


def reset_restored_runtime(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> None:
    """Discard transient state that may encode the pre-restore runtime generation."""
    from . import continuity, function_groups, request_rules, runtime_failure_hardening

    key = (entry_id, subentry_id)

    continuity_managers = hass.data.get(continuity._MANAGERS, {})
    continuity_manager = continuity_managers.pop(key, None)
    if continuity_manager is not None:
        continuity_manager._sessions.clear()
        continuity_manager._memory_bundles.clear()
        continuity_manager._pending_ends.clear()
        continuity_manager._ignored_conversation_ids.clear()

    rule_runtimes = hass.data.get(request_rules._RUNTIMES, {})
    rule_runtime = rule_runtimes.pop(key, None)
    if rule_runtime is not None:
        rule_runtime._conversation_overrides.clear()

    group_runtimes = hass.data.get(function_groups._RUNTIMES, {})
    group_runtime = group_runtimes.pop(key, None)
    if group_runtime is not None:
        group_runtime._sessions.clear()
        group_runtime._last_request.clear()

    # A previous Store startup failure may have left the active entity using a
    # volatile Usage manager. Do not let that fallback survive a durable restore.
    hass.data.get(runtime_failure_hardening._VOLATILE_USAGE_MANAGERS, {}).pop(key, None)

    statuses = hass.data.get(SUBSYSTEM_STATUS_KEY)
    if isinstance(statuses, dict):
        statuses.pop(key, None)


async def _update_configuration(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    prepared: backup.PreparedRestore,
    progress: Callable[[str], Awaitable[None]],
) -> None:
    hass.config_entries.async_update_subentry(
        entry, subentry, data=prepared.config, title=prepared.title
    )
    await progress("configuration")


async def _rollback_transaction(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    managers: tuple[Any, ...],
    store: Store[dict[str, Any]],
    journal: dict[str, Any],
    rollback: backup.PreparedRestore,
) -> None:
    """Durably return every category to the pre-restore snapshot."""
    await _set_phase(store, journal, _PHASE_ROLLING_BACK)

    async def progress(category: str) -> None:
        await _save_progress(
            store, journal, "rollback_completed_categories", category
        )

    await _apply_prepared(managers, rollback, progress)
    await _update_configuration(hass, entry, subentry, rollback, progress)
    reset_restored_runtime(hass, entry.entry_id, subentry.subentry_id)
    await store.async_remove()


async def async_restore_backup_recoverably(
    hass: HomeAssistant, entry: Any, subentry: Any, value: Any
) -> dict[str, Any]:
    """Restore one agent with a durable cross-category commit decision."""
    prepared = backup.inspect_backup(value, subentry.subentry_id)
    entry_id = entry.entry_id
    subentry_id = subentry.subentry_id

    async with backup._backup_lock(hass, entry_id, subentry_id):
        managers = await _durable_managers(hass, entry_id, subentry_id)
        rollback = await backup._snapshot_for_restore(managers, subentry)
        store = _journal_store(hass, entry_id, subentry_id)
        journal = _new_journal(entry_id, subentry_id, prepared, rollback)

        # This is the write-ahead boundary. If it fails, no category has changed.
        await store.async_save(deepcopy(journal))

        async def progress(category: str) -> None:
            await _save_progress(store, journal, "completed_categories", category)

        try:
            await _apply_prepared(managers, prepared, progress)
            await _update_configuration(hass, entry, subentry, prepared, progress)
            # Once committed is durable, every recovery pass finishes the target;
            # before this point every recovery pass rolls back the old snapshot.
            await _set_phase(store, journal, _PHASE_COMMITTED)
        except Exception as err:
            try:
                await _rollback_transaction(
                    hass, entry, subentry, managers, store, journal, rollback
                )
            except Exception:
                _LOGGER.exception("Agent backup restore rollback remains pending")
                raise backup.BackupError(
                    "Restore failed and recovery is still pending; restart Home "
                    "Assistant to retry the saved rollback"
                ) from err
            raise backup.BackupError(
                "Restore failed; the previous agent state was recovered"
            ) from err

        try:
            reset_restored_runtime(hass, entry_id, subentry_id)
            await store.async_remove()
        except Exception as err:
            _LOGGER.exception("Agent restore committed but cleanup remains pending")
            raise backup.BackupError(
                "Restore committed successfully, but cleanup is pending; restart "
                "Home Assistant to finish recovery"
            ) from err

    return {"status": "restored", "summary": prepared.summary()}


async def async_recover_pending_restore(
    hass: HomeAssistant, entry: Any, subentry: Any
) -> bool:
    """Finish or roll back one interrupted transaction; safe to call repeatedly."""
    entry_id = entry.entry_id
    subentry_id = subentry.subentry_id
    store = _journal_store(hass, entry_id, subentry_id)

    async with backup._backup_lock(hass, entry_id, subentry_id):
        raw = await store.async_load()
        if raw is None:
            return False
        journal, target, rollback = _load_journal(raw, entry_id, subentry_id)
        managers = await _durable_managers(hass, entry_id, subentry_id)

        committed = journal["phase"] == _PHASE_COMMITTED
        selected = target if committed else rollback
        field = "completed_categories" if committed else "rollback_completed_categories"
        if not committed and journal["phase"] != _PHASE_ROLLING_BACK:
            await _set_phase(store, journal, _PHASE_ROLLING_BACK)

        async def progress(category: str) -> None:
            await _save_progress(store, journal, field, category)

        try:
            await _apply_prepared(managers, selected, progress)
            await _update_configuration(hass, entry, subentry, selected, progress)
            reset_restored_runtime(hass, entry_id, subentry_id)
            await store.async_remove()
        except Exception as err:
            _LOGGER.exception(
                "Unable to recover pending full restore for %s/%s",
                entry_id,
                subentry_id,
            )
            raise backup.BackupError(
                "An interrupted full restore could not be recovered safely"
            ) from err
        return True


async def async_recover_pending_restores(hass: HomeAssistant) -> None:
    """Recover all configured conversation-agent journals before agents are loaded."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        for subentry in entry.subentries.values():
            if subentry.subentry_type != "conversation":
                continue
            await async_recover_pending_restore(hass, entry, subentry)


def install_restore_recovery() -> None:
    """Install durable restore orchestration before the maintenance gate wraps it."""
    global _INSTALLED
    if _INSTALLED:
        return
    from . import management_ui

    current = backup.async_restore_backup
    if not getattr(current, "_extended_openai_restart_recovery", False):

        @wraps(current)
        async def recoverable_restore(
            hass: HomeAssistant, entry: Any, subentry: Any, value: Any
        ) -> dict[str, Any]:
            return await async_restore_backup_recoverably(
                hass, entry, subentry, value
            )

        recoverable_restore._extended_openai_restart_recovery = True  # type: ignore[attr-defined]
        backup.async_restore_backup = recoverable_restore
        management_ui.async_restore_backup = recoverable_restore

    _INSTALLED = True
