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
    await archive.async_replace_backup(
        prepared.archive_sessions, prepared.archive_turns
    )
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


def _active_agent(hass: HomeAssistant, entry_id: str, subentry_id: str) -> Any | None:
    """Return the currently registered agent only when it is the exact subentry."""
    from homeassistant.components import conversation

    try:
        agent = conversation.async_get_agent(hass, entry_id)
    except (KeyError, ValueError):
        return None
    if (
        agent is None
        or getattr(getattr(agent, "subentry", None), "subentry_id", None)
        != subentry_id
    ):
        return None
    return agent


def reset_restored_runtime(
    hass: HomeAssistant,
    entry_id: str,
    subentry_id: str,
    managers: tuple[Any, ...] | None = None,
) -> None:
    """Reset transient state without splitting live agents from manager registries."""
    from . import continuity, function_groups, request_rules, runtime_failure_hardening

    key = (entry_id, subentry_id)
    agent = _active_agent(hass, entry_id, subentry_id)

    continuity_managers = hass.data.setdefault(continuity._MANAGERS, {})
    continuity_manager = continuity_managers.get(key)
    agent_continuity = getattr(agent, "_continuity", None)
    for current in {id(item): item for item in (continuity_manager, agent_continuity) if item is not None}.values():
        current._sessions.clear()
        current._memory_bundles.clear()
        current._pending_ends.clear()
        current._ignored_conversation_ids.clear()
    if agent_continuity is not None:
        continuity_managers[key] = agent_continuity

    rule_runtimes = hass.data.setdefault(request_rules._RUNTIMES, {})
    rule_runtime = rule_runtimes.get(key)
    agent_rule_runtime = getattr(agent, "_request_rule_runtime", None)
    for current in {id(item): item for item in (rule_runtime, agent_rule_runtime) if item is not None}.values():
        current._conversation_overrides.clear()
    if agent_rule_runtime is not None:
        rule_runtimes[key] = agent_rule_runtime

    group_runtimes = hass.data.setdefault(function_groups._RUNTIMES, {})
    group_runtime = group_runtimes.get(key)
    agent_group_runtime = getattr(agent, "_function_groups_runtime", None)
    for current in {id(item): item for item in (group_runtime, agent_group_runtime) if item is not None}.values():
        current._sessions.clear()
        current._last_request.clear()
    if agent_group_runtime is not None:
        group_runtimes[key] = agent_group_runtime

    # A previous Store startup failure may have left the active entity using a
    # volatile Usage manager. Replace that exact stale pointer with the restored
    # durable manager rather than merely deleting the fallback registry entry.
    fallback = hass.data.get(runtime_failure_hardening._VOLATILE_USAGE_MANAGERS, {}).pop(
        key, None
    )
    if fallback is not None and managers is not None and agent is not None:
        if getattr(agent, "_usage", None) is fallback:
            agent._usage = managers[4]

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
        await _save_progress(store, journal, "rollback_completed_categories", category)

    await _apply_prepared(managers, rollback, progress)
    reset_restored_runtime(hass, entry.entry_id, subentry.subentry_id, managers)
    await _update_configuration(hass, entry, subentry, rollback, progress)
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
        try:
            await store.async_save(deepcopy(journal))
        except Exception as err:
            raise backup.BackupError(
                "Restore could not start because its recovery journal could not be saved"
            ) from err

        async def progress(category: str) -> None:
            await _save_progress(store, journal, "completed_categories", category)

        try:
            await _apply_prepared(managers, prepared, progress)
            # This is the durable commit decision. Configuration changes can schedule
            # an HA reload task, so do not expose them while rollback is still valid.
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
            reset_restored_runtime(hass, entry_id, subentry_id, managers)
            await _update_configuration(hass, entry, subentry, prepared, progress)
            await store.async_remove()
        except Exception as err:
            _LOGGER.exception("Agent restore committed but completion remains pending")
            raise backup.BackupError(
                "Restore committed successfully, but completion is pending; restart "
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
            reset_restored_runtime(hass, entry_id, subentry_id, managers)
            await _update_configuration(hass, entry, subentry, selected, progress)
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
            return await async_restore_backup_recoverably(hass, entry, subentry, value)

        recoverable_restore._extended_openai_restart_recovery = True  # type: ignore[attr-defined]
        backup.async_restore_backup = recoverable_restore
        management_ui.async_restore_backup = recoverable_restore

    _INSTALLED = True
