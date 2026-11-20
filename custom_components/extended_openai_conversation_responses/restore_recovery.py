"""Restart-safe transaction handling for full agent restores."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import asdict, replace
import json
import logging
import os
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import backup
from .agent_config import agent_retention_snapshot, normalized_agent_integer
from .agent_maintenance import (
    _async_run_exclusive_operation,
    get_agent_maintenance_gate,
)
from .const import (
    CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
    DEFAULT_ARCHIVE_SESSION_TIMEOUT_MINUTES,
    DOMAIN,
    SUBSYSTEM_STATUS_KEY,
)
from .conversation_archive import archive_session_inactive
from .live_subentry_updates import (
    configuration_supports_live_update,
    live_subentry_update,
    update_live_subentry,
)
from .mutation_outcomes import mark_mutation_failure, mutation_failure
from .operation_settlement import async_settle_owned_operation
from .operational_errors import log_handled_failure
from .storage_namespaces import RESTORE_JOURNAL_PREFIX as RESTORE_JOURNAL_PREFIX
from .strict_store import PropagatingWriteStore

_LOGGER = logging.getLogger(__name__)

RESTORE_JOURNAL_VERSION = 1

_PHASE_APPLYING = "applying"
_PHASE_COMMITTED = "committed"
_PHASES = {_PHASE_APPLYING, _PHASE_COMMITTED}
_DATA_SECTIONS = frozenset(
    {
        "memories",
        "temporary_memories",
        "knowledge",
        "archive",
        "usage",
        "guest_mode",
        "request_rules",
    }
)


class _JournalVerificationUnavailable(Exception):
    """The durable outcome of one journal write could not be determined."""


def _sync_journal_directory(path: str) -> None:
    """Make journal removal/absence durable on the supported POSIX filesystems.

    Windows does not expose directory fsync through this API. Writes retain
    HA's atomic writer guarantees there, but deletion has no added guarantee.
    """
    if os.name != "posix":
        return
    parent = Path(path).parent
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(parent, flags)
    except FileNotFoundError:
        # Before the first Store write, .storage itself may not yet exist.
        # Its absence is a directory entry in the existing HA config directory.
        descriptor = os.open(parent.parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class _RestoreJournalStore(PropagatingWriteStore):
    """Propagate write failures and preserve strict on-disk recovery evidence."""

    async def async_load(self) -> dict[str, Any] | None:
        """Read the file, never HA's pending-save or shared load cache."""
        result = await self._async_load_data()
        if result is None:
            try:
                await self.hass.async_add_executor_job(
                    _sync_journal_directory, self.path
                )
            except OSError as err:
                raise backup.BackupError(
                    "Restore journal absence could not be durably verified"
                ) from err
        return result

    async def async_remove(self) -> None:
        """Retire recovery evidence only after the directory entry is synced."""
        await super().async_remove()
        try:
            await self.hass.async_add_executor_job(_sync_journal_directory, self.path)
        except OSError as err:
            raise backup.BackupError(
                "Restore journal retirement could not be durably verified"
            ) from err

    async def _async_load_data(self) -> dict[str, Any] | None:
        def read() -> dict[str, Any] | None:
            try:
                envelope = json.loads(Path(self.path).read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except (OSError, UnicodeError, ValueError) as err:
                raise backup.BackupError(
                    "Restore journal is unreadable; recovery is required"
                ) from err
            if (
                not isinstance(envelope, dict)
                or envelope.get("version") != self.version
                or envelope.get("minor_version", 1) != self.minor_version
                or envelope.get("key") != self.key
                or not isinstance(envelope.get("data"), dict)
            ):
                raise backup.BackupError(
                    "Restore journal is corrupted; recovery is required"
                )
            return cast(dict[str, Any], envelope["data"])

        return await self.hass.async_add_executor_job(read)


def _journal_store(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> Store[dict[str, Any]]:
    """Return the deterministic private journal for one exact agent."""
    return _RestoreJournalStore(
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
    document: dict[str, Any] = {
        "format": backup.BACKUP_FORMAT,
        "version": backup.BACKUP_VERSION,
        "created_at": prepared.created_at,
        "integration_version": prepared.integration_version,
        "agent": {
            "title": prepared.title,
            "source_entry_id": entry_id,
            "source_subentry_id": subentry_id,
            # Retain the exact configuration needed to verify recovery.
            "config": deepcopy(prepared.config),
        },
    }
    if prepared.restores("memories"):
        document["memories"] = {
            "memories": [asdict(item) for item in prepared.memories]
        }
    if prepared.restores("temporary_memories"):
        document["temporary_memories"] = {
            "records": [asdict(item) for item in prepared.temporary_memories]
        }
    if prepared.restores("knowledge"):
        document["knowledge"] = {
            "sources": [asdict(item) for item in prepared.knowledge]
        }
    if prepared.restores("archive"):
        archive: dict[str, Any] = {
            "sessions": [asdict(item) for item in prepared.archive_sessions],
            "turns": [asdict(item) for item in prepared.archive_turns],
        }
        if prepared.archive_active is not None:
            archive["active"] = deepcopy(prepared.archive_active)
        document["archive"] = archive
    if prepared.restores("usage"):
        document["usage"] = {
            "totals": asdict(prepared.usage_totals),
            "daily": deepcopy(prepared.usage_daily),
            "requests": [asdict(item) for item in prepared.usage_requests],
            "runs": [asdict(item) for item in prepared.usage_runs],
        }
    if prepared.restores("guest_mode"):
        schedule = prepared.guest_mode_schedule
        document["guest_mode"] = {
            "schedule": asdict(schedule) if schedule is not None else None
        }
    if prepared.restores("request_rules"):
        document["request_rules"] = deepcopy(prepared.request_rules)
    return document


def _new_journal(
    entry_id: str,
    subentry_id: str,
    target: backup.PreparedRestore,
    rollback: backup.PreparedRestore,
    *,
    target_provider: str | None = None,
    rollback_provider: str | None = None,
    provider_connections: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the complete write-ahead record before destructive work begins."""
    return {
        "transaction_id": uuid4().hex,
        "entry_id": entry_id,
        "subentry_id": subentry_id,
        "phase": _PHASE_APPLYING,
        "created_at": dt_util.utcnow().isoformat(),
        "target": _prepared_document(target, entry_id, subentry_id),
        "rollback": _prepared_document(rollback, entry_id, subentry_id),
        **(
            {"data_sections": sorted(target.journaled_sections)}
            if target.journaled_sections is not None
            else {}
        ),
        **(
            {
                "configuration_providers": {
                    "target": target_provider,
                    "rollback": rollback_provider,
                }
            }
            if target_provider is not None and rollback_provider is not None
            else {}
        ),
        **(
            {"provider_connections": provider_connections}
            if provider_connections is not None
            else {}
        ),
    }


def _journal_providers(
    value: Mapping[str, Any], fallback: str | None
) -> dict[str, str | None]:
    """Read local validation hints without restoring or inferring credentials.

    Older journals lack these hints and retain their existing parent-provider
    fallback. New journals identify the two independently validated generations.
    """
    if "configuration_providers" not in value:
        return {"target": fallback, "rollback": fallback}
    from .const import API_PROVIDERS

    contexts = value["configuration_providers"]
    if (
        not isinstance(contexts, Mapping)
        or set(contexts) != {"target", "rollback"}
        or any(
            not isinstance(item, str) or item not in {p["key"] for p in API_PROVIDERS}
            for item in contexts.values()
        )
    ):
        raise backup.BackupError("Pending restore provider context is corrupted")
    return dict(contexts)


def _journal_provider_connections(
    journal: Mapping[str, Any], providers: Mapping[str, str | None]
) -> dict[str, dict[str, Any]] | None:
    """Validate credential generations contained only in the private journal."""
    if "provider_connections" not in journal:
        return None
    from .provider_credentials import validate_provider_connection

    connections = journal["provider_connections"]
    if not isinstance(connections, dict) or set(connections) != {"target", "rollback"}:
        raise backup.BackupError("Pending restore provider connection is corrupted")
    try:
        for section, data in connections.items():
            if (
                not isinstance(data, dict)
                or validate_provider_connection(data) != providers[section]
            ):
                raise HomeAssistantError("Incorrect provider context")
    except HomeAssistantError:
        raise backup.BackupError(
            "Pending restore provider connection is corrupted"
        ) from None
    return connections


def _journal_backup_document(value: Any, sections: frozenset[str] | None) -> Any:
    """Fill only explicitly absent stores for the existing backup validator."""
    if sections is None:
        return value
    if not isinstance(value, dict) or set(value) & _DATA_SECTIONS != sections:
        raise backup.BackupError("Pending restore sections do not match the snapshot")
    return {
        "memories": {"memories": []},
        "temporary_memories": {"records": []},
        "knowledge": {"sources": []},
        "archive": {"sessions": [], "turns": []},
        "usage": {
            "totals": asdict(backup.UsageTotals()),
            "daily": {},
            "requests": [],
            "runs": [],
        },
        "guest_mode": {"schedule": None},
        "request_rules": {"defaults": {}, "rules": []},
        **value,
    }


async def _async_load_journal(
    value: Any, entry_id: str, subentry_id: str, *, provider: str | None = None
) -> tuple[dict[str, Any], backup.PreparedRestore, backup.PreparedRestore]:
    """Validate a pending transaction without trusting mutable journal fields."""
    required = {
        "transaction_id",
        "entry_id",
        "subentry_id",
        "phase",
        "created_at",
        "target",
        "rollback",
    }
    if (
        not isinstance(value, Mapping)
        or not required <= set(value)
        or set(value)
        - required
        - {"configuration_providers", "data_sections", "provider_connections"}
    ):
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
    sections = None
    if "data_sections" in journal:
        raw_sections = journal["data_sections"]
        if (
            not isinstance(raw_sections, list)
            or any(
                not isinstance(section, str) or section not in _DATA_SECTIONS
                for section in raw_sections
            )
            or len(raw_sections) != len(set(raw_sections))
        ):
            raise backup.BackupError("Pending restore sections are corrupted")
        sections = frozenset(raw_sections)
    providers = _journal_providers(journal, provider)
    _journal_provider_connections(journal, providers)
    try:
        try:
            rollback = await backup.async_inspect_backup(
                _journal_backup_document(value["rollback"], sections),
                subentry_id,
                max_bytes=backup.MAX_BACKUP_BYTES,
                private_journal=True,
                provider=providers["rollback"],
            )
        except backup.BackupError as err:
            # Older writers could journal an oversized rollback. Once committed,
            # only the target is authoritative; never replay that rollback.
            if phase != _PHASE_COMMITTED or "safety limit" not in str(err):
                raise
            rollback = await backup.async_inspect_backup(
                _journal_backup_document(value["target"], sections),
                subentry_id,
                max_bytes=backup.MAX_BACKUP_BYTES,
                private_journal=True,
                provider=providers["target"],
            )
        try:
            target = await backup.async_inspect_backup(
                _journal_backup_document(value["target"], sections),
                subentry_id,
                max_bytes=backup.MAX_BACKUP_BYTES,
                private_journal=True,
                provider=providers["target"],
            )
        except HomeAssistantError:
            if phase != _PHASE_APPLYING:
                raise
            # An uncommitted target is never replayed. A valid rollback must
            # remain recoverable even if an older release journaled bad input.
            target = rollback
    except HomeAssistantError as err:
        raise backup.BackupError("Pending restore transaction is corrupted") from err
    if sections is not None:
        target.journaled_sections = rollback.journaled_sections = sections
    return journal, target, rollback


async def _async_write_journal_verified(
    store: Store[dict[str, Any]], journal: dict[str, Any]
) -> bool:
    """Require both writer success and matching on-disk journal content."""
    try:
        await store.async_save(await asyncio.to_thread(deepcopy, journal))
        persisted = await store.async_load()
    except Exception as err:
        raise _JournalVerificationUnavailable from err
    return await asyncio.to_thread(lambda: persisted == journal)


async def _durable_managers(
    hass: HomeAssistant, entry_id: str, subentry_id: str, *, include_usage: bool = True
) -> backup.AgentDataParticipants:
    """Load the actual durable managers; restore must never target Usage fallback RAM."""
    from .conversation_archive import async_get_archive
    from .guest_mode import async_get_guest_mode
    from .knowledge import async_get_knowledge
    from .memory import async_get_memory
    from .request_rules import async_get_request_rules
    from .temporary_memory import async_get_temporary_memory
    from .usage import async_get_durable_usage

    return backup.AgentDataParticipants(
        memory=await async_get_memory(hass, entry_id, subentry_id),
        temporary=await async_get_temporary_memory(hass, entry_id, subentry_id),
        knowledge=await async_get_knowledge(hass, entry_id, subentry_id),
        archive=await async_get_archive(hass, entry_id, subentry_id),
        usage=(
            await async_get_durable_usage(hass, entry_id, subentry_id)
            if include_usage
            else None
        ),
        guest_mode=await async_get_guest_mode(hass, entry_id, subentry_id),
        request_rules=await async_get_request_rules(hass, entry_id, subentry_id),
    )


async def _apply_prepared(
    managers: backup.AgentDataParticipants, prepared: backup.PreparedRestore
) -> None:
    """Apply validated durable categories in a deterministic, idempotent order."""
    if prepared.restores("usage"):
        if managers.usage is None:
            raise RuntimeError("Usage restore requires a durable manager")
        retention = agent_retention_snapshot(prepared.config)
        managers.usage.request_retention_days = int(
            retention[CONF_USAGE_REQUEST_RETENTION_DAYS]
        )
        managers.usage.run_retention_days = int(
            retention[CONF_USAGE_RUN_RETENTION_DAYS]
        )

    if prepared.restores("memories"):
        await managers.memory.async_replace_backup(prepared.memories)
    if prepared.restores("temporary_memories"):
        await managers.temporary.async_replace_backup(prepared.temporary_memories)
    if prepared.restores("knowledge"):
        await managers.knowledge.async_replace_backup(prepared.knowledge)
    if prepared.restores("archive"):
        archive_kwargs = (
            {"active": prepared.archive_active}
            if prepared.archive_active is not None
            else {}
        )
        await managers.archive.async_replace_backup(
            prepared.archive_sessions, prepared.archive_turns, **archive_kwargs
        )
    if prepared.restores("usage"):
        assert managers.usage is not None
        await managers.usage.async_replace_backup(
            prepared.usage_totals,
            prepared.usage_daily,
            prepared.usage_requests,
            prepared.usage_runs,
        )
    if prepared.restores("guest_mode"):
        await managers.guest_mode.async_replace_backup(prepared.guest_mode_schedule)
    if prepared.restores("request_rules"):
        await managers.request_rules.async_replace_backup(prepared.request_rules)


async def _recovery_managers(
    hass: HomeAssistant,
    entry_id: str,
    subentry_id: str,
    selected: backup.PreparedRestore,
) -> backup.AgentDataParticipants:
    """Recover known initialization blockers from an already validated journal.

    Ordinary restores still require an intact rollback snapshot. Only existing
    journal recovery has authority to replace a rejected destination generation.
    Reuse normal getters afterward so indexes, ownership and expiry are rebuilt
    by production loaders, without inventing an initialized manager generation.
    """
    if not get_agent_maintenance_gate(hass, entry_id, subentry_id).owns_exclusive():
        raise RuntimeError("Recovery participants require exclusive maintenance")
    from .usage import async_verify_rejected_usage_stores

    include_usage = selected.restores("usage")
    try:
        managers = await _durable_managers(
            hass, entry_id, subentry_id, include_usage=include_usage
        )
        if include_usage:
            await async_verify_rejected_usage_stores(hass, entry_id, subentry_id)
        return managers
    except ValueError as initial_error:
        from .conversation_archive import HomeAssistantArchiveStorage, async_get_archive
        from .guest_mode import (
            async_get_guest_mode,
            async_stage_guest_mode_journal_recovery,
        )
        from .knowledge import HomeAssistantKnowledgeStorage, async_get_knowledge
        from .memory import (
            HomeAssistantMemoryStorage,
            PersistentMemory,
            _record_as_storage_dict,
            async_get_memory,
        )
        from .persistence_hardening import StorageRepairRequired
        from .request_rules import (
            async_get_request_rules,
            async_stage_request_rules_journal_recovery,
        )
        from .temporary_memory import (
            _temporary_memory_store,
            async_get_temporary_memory,
        )
        from .usage import async_get_durable_usage, async_stage_usage_journal_recovery

        async def recovery_usage(
            hass: HomeAssistant, entry_id: str, subentry_id: str
        ) -> None:
            await async_get_durable_usage(hass, entry_id, subentry_id)
            await async_verify_rejected_usage_stores(hass, entry_id, subentry_id)

        repaired = False
        try:
            await async_get_memory(hass, entry_id, subentry_id)
        except StorageRepairRequired:
            if not selected.restores("memories"):
                raise

            def memory_payload() -> dict[str, Any]:
                payload = {
                    "memories": [
                        _record_as_storage_dict(record) for record in selected.memories
                    ]
                }
                records = PersistentMemory.validate_backup_data(payload)
                return {
                    "memories": [_record_as_storage_dict(record) for record in records]
                }

            await HomeAssistantMemoryStorage(hass, entry_id, subentry_id).async_save(
                await asyncio.to_thread(memory_payload)
            )
            repaired = True
        for section, getter, storage_factory, field, records in (
            (
                "temporary_memories",
                async_get_temporary_memory,
                _temporary_memory_store,
                "records",
                selected.temporary_memories,
            ),
            (
                "knowledge",
                async_get_knowledge,
                HomeAssistantKnowledgeStorage,
                "sources",
                selected.knowledge,
            ),
        ):
            try:
                await getter(hass, entry_id, subentry_id)
            except ValueError:
                if not selected.restores(section):
                    raise

                def serialize(
                    field: str = field, records: list[Any] = records
                ) -> dict[str, Any]:
                    return {field: [asdict(record) for record in records]}

                payload = await asyncio.to_thread(serialize)
                await storage_factory(hass, entry_id, subentry_id).async_save(payload)
                repaired = True
        try:
            await async_get_archive(hass, entry_id, subentry_id)
        except ValueError:
            if not selected.restores("archive"):
                raise
            await HomeAssistantArchiveStorage(
                hass, entry_id, subentry_id
            ).async_stage_journal_recovery(
                selected.archive_sessions,
                selected.archive_turns,
                selected.archive_active,
            )
            repaired = True
        for section, recovery_getter, stage in (
            (
                "guest_mode",
                async_get_guest_mode,
                lambda: async_stage_guest_mode_journal_recovery(
                    hass, entry_id, subentry_id, selected.guest_mode_schedule
                ),
            ),
            (
                "request_rules",
                async_get_request_rules,
                lambda: async_stage_request_rules_journal_recovery(
                    hass, entry_id, subentry_id, selected.request_rules
                ),
            ),
            (
                "usage",
                recovery_usage,
                lambda: async_stage_usage_journal_recovery(
                    hass,
                    entry_id,
                    subentry_id,
                    selected.usage_totals,
                    selected.usage_daily,
                    selected.usage_requests,
                    selected.usage_runs,
                ),
            ),
        ):
            if section == "usage" and not include_usage:
                continue
            try:
                await recovery_getter(hass, entry_id, subentry_id)
            except StorageRepairRequired:
                if not selected.restores(section):
                    raise
                await stage()
                repaired = True
        if not repaired:
            raise initial_error
        return await _durable_managers(
            hass, entry_id, subentry_id, include_usage=include_usage
        )


def _active_agent(hass: HomeAssistant, entry_id: str, subentry_id: str) -> Any | None:
    """Return the loaded conversation entity for one exact agent subentry."""
    from homeassistant.components import conversation

    def matches(candidate: Any) -> bool:
        return bool(
            candidate is not None
            and getattr(getattr(candidate, "entry", None), "entry_id", None) == entry_id
            and getattr(getattr(candidate, "subentry", None), "subentry_id", None)
            == subentry_id
        )

    try:
        registered = conversation.async_get_agent(hass, entry_id)
    except KeyError, ValueError:
        registered = None
    if matches(registered):
        return registered

    # Several conversation subentries share one parent config-entry registration
    # key. If another subentry currently owns that Core mapping, scan the loaded
    # ConversationEntity platform so restore cleanup still reaches the exact agent.
    component = hass.data.get(conversation.DATA_COMPONENT)
    for candidate in getattr(component, "entities", ()):
        if matches(candidate):
            return candidate
    return None


def reset_restored_runtime(
    hass: HomeAssistant,
    entry_id: str,
    subentry_id: str,
    managers: backup.AgentDataParticipants | None = None,
    preserve_continuity: bool = False,
    replace_usage: bool = True,
) -> None:
    """Reset transient state without splitting live agents from manager registries."""
    from . import continuity, function_groups, request_rules, usage
    from .conversation_id_ownership import has_live_conversation_owner

    key = (entry_id, subentry_id)
    agent = _active_agent(hass, entry_id, subentry_id)

    # A portable restore can retain destination privacy bindings. Retained
    # continuity is kept only for a restore that leaves the live history intact.
    archive = managers.archive if managers is not None else None
    private_bindings = (
        archive.private_continuity_bindings()
        if isinstance(archive, backup.ConversationArchive)
        else None
    )
    continuity_managers = hass.data.setdefault(continuity._MANAGERS, {})
    continuity_manager = continuity_managers.get(key)
    agent_continuity = getattr(agent, "_continuity", None)
    retained_ha_default_ids = (
        frozenset(
            conversation_id
            for conversation_id, scope_id in archive.retained_ha_default_scopes().items()
            if has_live_conversation_owner(hass, subentry_id, conversation_id, scope_id)
        )
        if preserve_continuity
        and agent_continuity is not None
        and isinstance(archive, backup.ConversationArchive)
        else frozenset()
    )
    retained_sessions: frozenset[str] = frozenset()
    for current in {
        id(item): item
        for item in (continuity_manager, agent_continuity)
        if item is not None
    }.values():
        if preserve_continuity and isinstance(archive, backup.ConversationArchive):
            retained = current.reset_all(
                private_bindings=private_bindings,
                retained_bindings=archive.retained_continuity_bindings(),
                retained_ha_default_ids=(
                    retained_ha_default_ids
                    if current is agent_continuity
                    else frozenset()
                ),
            )
            if current is (agent_continuity or continuity_manager):
                retained_sessions = frozenset(
                    request_rules.request_rule_session_id(
                        continuity_key, conversation_id
                    )
                    for continuity_key, conversation_id in retained
                )
        else:
            current.reset_all(private_bindings=private_bindings)
    if agent_continuity is not None:
        continuity_managers[key] = agent_continuity

    rule_runtimes = hass.data.setdefault(request_rules._RUNTIMES, {})
    rule_runtime = rule_runtimes.get(key)
    agent_rule_runtime = getattr(agent, "_request_rule_runtime", None)
    for current in {
        id(item): item
        for item in (rule_runtime, agent_rule_runtime)
        if item is not None
    }.values():
        if retained_sessions and current is (agent_rule_runtime or rule_runtime):
            current.reset_all(retained_sessions)
        else:
            current.reset_all()
    if agent_rule_runtime is not None:
        rule_runtimes[key] = agent_rule_runtime

    group_runtimes = hass.data.setdefault(function_groups._RUNTIMES, {})
    group_runtime = group_runtimes.get(key)
    agent_group_runtime = getattr(agent, "_function_groups_runtime", None)
    for current in {
        id(item): item
        for item in (group_runtime, agent_group_runtime)
        if item is not None
    }.values():
        if retained_sessions and current is (agent_group_runtime or group_runtime):
            current.reset_all(retained_sessions)
        else:
            current.reset_all()
    if agent_group_runtime is not None:
        group_runtimes[key] = agent_group_runtime

    # A previous Store startup failure may have left the active entity using a
    # volatile Usage manager. Replace that exact stale pointer with the restored
    # durable manager rather than merely deleting the fallback registry entry.
    fallback = (
        hass.data.get(usage._VOLATILE_USAGE_MANAGERS, {}).pop(key, None)
        if replace_usage and managers is not None
        else None
    )
    if fallback is not None and managers is not None:
        durable_usage = managers.usage
        assert durable_usage is not None
        if agent is not None and getattr(agent, "_usage", None) is fallback:
            agent._usage = durable_usage
        from .sensor import rebind_usage_sensors

        rebind_usage_sensors(hass, subentry_id, fallback, durable_usage)
        if isinstance(fallback, usage.UsageManager):
            fallback.dispose_maintenance()

    statuses = hass.data.get(SUBSYSTEM_STATUS_KEY)
    if isinstance(statuses, dict):
        statuses.pop(key, None)


def _persisted_subentry_matches(
    value: Any,
    entry_id: str,
    subentry_id: str,
    prepared: backup.PreparedRestore,
    *,
    provider_data: Mapping[str, Any] | None = None,
) -> bool:
    """Verify the exact restored subentry in Core's on-disk config snapshot."""
    if not isinstance(value, Mapping):
        return False
    entries = value.get("entries")
    if not isinstance(entries, list):
        return False
    for raw_entry in entries:
        if not isinstance(raw_entry, Mapping) or raw_entry.get("entry_id") != entry_id:
            continue
        if provider_data is not None and raw_entry.get("data") != provider_data:
            return False
        subentries = raw_entry.get("subentries")
        if not isinstance(subentries, list):
            return False
        for raw_subentry in subentries:
            if (
                isinstance(raw_subentry, Mapping)
                and raw_subentry.get("subentry_id") == subentry_id
            ):
                return bool(
                    raw_subentry.get("title") == prepared.title
                    and raw_subentry.get("data") == prepared.config
                )
        return False
    return False


async def _async_persist_config_entries(
    hass: HomeAssistant,
    entry_id: str,
    subentry_id: str,
    prepared: backup.PreparedRestore,
) -> None:
    """Force and verify Core config persistence before discarding recovery state."""
    await async_persist_config_entries(
        hass,
        lambda persisted: _persisted_subentry_matches(
            persisted, entry_id, subentry_id, prepared
        ),
        "Restored agent configuration could not be durably verified",
    )
    await _apply_restored_volatile_retention(hass, entry_id, subentry_id, prepared)


async def _apply_restored_volatile_retention(
    hass: HomeAssistant,
    entry_id: str,
    subentry_id: str,
    prepared: backup.PreparedRestore,
) -> None:
    from .usage import async_apply_volatile_usage_retention, usage_retention_policy

    await async_apply_volatile_usage_retention(
        hass, entry_id, subentry_id, usage_retention_policy(prepared.config)
    )


async def async_persist_config_entries(
    hass: HomeAssistant,
    matches: Callable[[Any], bool],
    error_message: str,
) -> None:
    """Flush Core's config store and verify its on-disk contents."""
    manager = hass.config_entries
    store = getattr(manager, "_store", None)
    data_to_save = getattr(manager, "_data_to_save", None)
    if store is None or not callable(data_to_save):
        raise backup.BackupError(error_message)
    try:
        await store.async_save(data_to_save())
        # Use a fresh Store instance so a concurrent delayed Core update cannot make
        # async_load return pending in-memory data instead of the on-disk snapshot.
        verifier = Store[dict[str, Any]](
            hass,
            store.version,
            store.key,
            minor_version=store.minor_version,
        )
        persisted = await verifier.async_load()
    except Exception as err:
        raise backup.BackupError(error_message) from err
    if not matches(persisted):
        raise backup.BackupError(error_message)


async def _update_configuration(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    prepared: backup.PreparedRestore,
    *,
    provider: str | None = None,
    previous_provider: str | None = None,
    provider_data: dict[str, Any] | None = None,
) -> None:
    # The owned restore already resets live subsystem runtimes. Apply its
    # configuration through the normal live-update path instead of starting an
    # unowned entry reload while exclusive recovery is still settling.
    from .helpers import entry_api_provider

    destination_provider = (
        provider if provider is not None else entry_api_provider(entry)
    )
    if provider_data is not None:
        await _async_publish_provider_pair(
            hass, entry, subentry, prepared, provider_data
        )
        return
    # A committed journal can be replayed after target configuration was already
    # persisted. That exact generation must use its own context on both sides.
    current_provider = (
        destination_provider
        if subentry.data == prepared.config
        else previous_provider
        if previous_provider is not None
        else entry_api_provider(entry)
    )
    if not configuration_supports_live_update(
        # Interpret authoritative private data as persisted on both sides.
        # A raw legacy [] must not look like a decoded empty edit and reload.
        backup.private_configuration_snapshot(subentry.data, provider=current_provider),
        (
            backup.private_configuration_snapshot(
                prepared.config, provider=destination_provider
            )
            if prepared.authoritative_config
            else backup.recoverable_configuration_snapshot(
                prepared.config, provider=destination_provider
            )
        ),
        title_changed=subentry.title != prepared.title,
    ):
        hass.data.setdefault(f"{DOMAIN}.restore_reload_pending", set()).add(
            entry.entry_id
        )
    update_live_subentry(
        hass, entry, subentry, data=prepared.config, title=prepared.title
    )
    await _async_persist_config_entries(
        hass, entry.entry_id, subentry.subentry_id, prepared
    )


def _claim_provider_restore(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> None:
    from .provider_credentials import PROVIDER_RESTORE_OWNERS

    owners = hass.data.setdefault(PROVIDER_RESTORE_OWNERS, {})
    if owners.setdefault(entry_id, subentry_id) != subentry_id:
        raise backup.BackupError(
            "Another assistant has a pending restore for this shared provider connection"
        )


async def _async_publish_provider_pair(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    prepared: backup.PreparedRestore,
    provider_data: dict[str, Any],
) -> None:
    """Publish one Core config snapshot with the matching runtime connection."""
    from homeassistant.config_entries import ConfigEntryState

    client = await _async_prepare_provider_runtime(hass, entry, provider_data)
    previous_config = dict(subentry.data)
    previous_title = subentry.title
    if getattr(entry, "state", None) is ConfigEntryState.LOADED and (
        entry.data != provider_data
        or not configuration_supports_live_update(
            previous_config,
            prepared.config,
            title_changed=previous_title != prepared.title,
        )
    ):
        hass.data.setdefault(f"{DOMAIN}.restore_reload_pending", set()).add(
            entry.entry_id
        )
    # Core queues both updates on the same Store. Neither setter yields, so
    # persistence observes the complete pair in one config entry snapshot.
    with live_subentry_update():
        hass.config_entries.async_update_subentry(
            entry, subentry, data=prepared.config, title=prepared.title
        )
        hass.config_entries.async_update_entry(entry, data=deepcopy(provider_data))
    await async_persist_config_entries(
        hass,
        lambda persisted: _persisted_subentry_matches(
            persisted,
            entry.entry_id,
            subentry.subentry_id,
            prepared,
            provider_data=provider_data,
        ),
        "Restored assistant and provider connection could not be durably verified",
    )
    await _apply_restored_volatile_retention(
        hass, entry.entry_id, subentry.subentry_id, prepared
    )
    if client is not None:
        _publish_provider_runtime(entry, client)


async def _async_prepare_provider_runtime(
    hass: HomeAssistant, entry: Any, provider_data: dict[str, Any]
) -> Any:
    """Construct the runtime for a provider generation before changing Core."""
    from homeassistant.config_entries import ConfigEntryState

    from .helpers import get_authenticated_client

    if getattr(entry, "state", None) is not ConfigEntryState.LOADED:
        return None
    return await get_authenticated_client(
        hass,
        provider_data["api_key"],
        provider_data.get("base_url"),
        provider_data.get("api_version"),
        provider_data.get("organization"),
        skip_authentication=True,
        api_provider=provider_data.get("api_provider"),
    )


def _publish_provider_runtime(entry: Any, client: Any) -> None:
    from . import _wrap_provider_client
    from .provider_errors import provider_authentication_snapshot

    entry.runtime_data = _wrap_provider_client(
        client, entry, provider_authentication_snapshot(entry)
    )


async def async_settle_deleted_agent_provider(
    hass: HomeAssistant, entry: Any, subentry_id: str
) -> None:
    """Resolve the shared connection before the deleted agent's journal is erased."""
    from .helpers import entry_api_provider

    entry_id = entry.entry_id
    store = _journal_store(hass, entry_id, subentry_id)
    raw = await store.async_load()
    if raw is None or "provider_connections" not in raw:
        return
    _claim_provider_restore(hass, entry_id, subentry_id)
    provider = entry_api_provider(entry)
    journal, _, _ = await _async_load_journal(
        raw, entry_id, subentry_id, provider=provider
    )
    connections = _journal_provider_connections(
        journal, _journal_providers(journal, provider)
    )
    assert connections is not None
    if not any(entry.data == data for data in connections.values()):
        raise backup.BackupError(
            "Pending restore cannot establish the provider connection generation"
        )
    side = "target" if journal["phase"] == _PHASE_COMMITTED else "rollback"
    provider_data = connections[side]
    client = await _async_prepare_provider_runtime(hass, entry, provider_data)
    with live_subentry_update():
        hass.config_entries.async_update_entry(entry, data=deepcopy(provider_data))

    def matches(persisted: Any) -> bool:
        entries = persisted.get("entries") if isinstance(persisted, Mapping) else None
        return isinstance(entries, list) and any(
            isinstance(item, Mapping)
            and item.get("entry_id") == entry_id
            and item.get("data") == provider_data
            for item in entries
        )

    await async_persist_config_entries(
        hass, matches, "Recovered provider connection could not be durably verified"
    )
    if client is not None:
        _publish_provider_runtime(entry, client)
    await store.async_remove()


async def _rollback_transaction(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    managers: backup.AgentDataParticipants,
    store: Store[dict[str, Any]],
    rollback: backup.PreparedRestore,
) -> None:
    """Durably return every category to the pre-restore snapshot."""
    await _apply_prepared(managers, rollback)
    # The exclusive transaction restored this same agent's pre-restore Archive.
    # Retain only live identities verified by those restored bindings; portable
    # target replacement must still use its separate reset policy.
    reset_restored_runtime(
        hass, entry.entry_id, subentry.subentry_id, managers, True, False
    )
    await _update_configuration(hass, entry, subentry, rollback)
    await store.async_remove()


async def _recover_failed_apply(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    managers: backup.AgentDataParticipants,
    store: Store[dict[str, Any]],
    rollback: backup.PreparedRestore,
    original_error: Exception,
) -> None:
    """Rollback an uncommitted apply or leave its journal for startup recovery."""
    try:
        await _rollback_transaction(hass, entry, subentry, managers, store, rollback)
    except Exception:
        _LOGGER.exception("Agent backup restore rollback remains pending")
        raise mark_mutation_failure(
            backup.BackupError(
                "Restore failed and recovery is still pending; restart Home Assistant "
                "to retry the saved rollback"
            ),
            "uncertain",
            recovery_pending=True,
        ) from original_error
    log_handled_failure(
        _LOGGER,
        f"Agent restore failed entry={entry.entry_id} assistant={subentry.subentry_id}; rollback=recovered",
        original_error,
    )
    raise mark_mutation_failure(
        backup.BackupError("Restore failed; the previous agent state was recovered"),
        "rejected",
    ) from original_error


def _with_destination_archive_privacy(
    prepared: backup.PreparedRestore,
    current: backup.PreparedRestore,
    subentry_id: str,
) -> backup.PreparedRestore:
    """Carry live destination privacy boundaries into the journaled target."""
    if not prepared.restores("archive") or prepared.preserve_retained_continuity:
        return prepared
    if not current.archive_active and prepared.archive_active is None:
        return prepared
    sessions = {session.session_id: session for session in prepared.archive_sessions}
    local = {session.session_id: session for session in current.archive_sessions}
    bindings: dict[str, str] = {}
    timeout = min(
        normalized_agent_integer(
            snapshot.config,
            CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
            DEFAULT_ARCHIVE_SESSION_TIMEOUT_MINUTES,
        )
        for snapshot in (current, prepared)
    )
    now = dt_util.utcnow()
    for key, session_id in (current.archive_active or {}).items():
        session = local[session_id]
        conversation_id = session.home_assistant_conversation_id
        if (
            session.retention_state != "private"
            or session.agent_subentry_id != subentry_id
            or not conversation_id
            or not (
                key == f"conversation:{conversation_id}"
                or key.startswith("continuity:")
            )
            or archive_session_inactive(session, now, timeout)
        ):
            continue
        collision = sessions.get(session_id)
        if collision is not None and (
            collision.retention_state != "private"
            or collision.scope_id != session.scope_id
            or collision.home_assistant_conversation_id != conversation_id
        ):
            # Keep the imported transcript and allocate a distinct, local marker.
            while session_id in sessions:
                session_id = uuid4().hex
        sessions[session_id] = replace(
            session, session_id=session_id, title="", turn_count=0
        )
        bindings[key] = session_id
    return replace(
        prepared,
        archive_sessions=list(sessions.values()),
        archive_active=bindings or None,
    )


async def _async_restore_backup_recoverably(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    value: Any,
    *,
    provider_data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Restore one agent with a durable cross-category commit decision.

    Live callers must already hold the agent's exclusive maintenance lease and
    defer cancellation until this operation finishes. This inner path acquires
    only the backup lock; both full and selective restores share its journal and
    rollback implementation without nesting maintenance-gate ownership.
    """
    prepared = await backup.async_inspect_backup(value, subentry.subentry_id)
    entry_id = entry.entry_id
    subentry_id = subentry.subentry_id

    async with backup._backup_lock(hass, entry_id, subentry_id):
        store = _journal_store(hass, entry_id, subentry_id)
        if await store.async_load() is not None:
            raise backup.BackupError(
                "A previous full restore still has pending recovery; restart Home "
                "Assistant before starting another restore"
            )

        include_usage = prepared.restores("usage")
        managers = await _durable_managers(
            hass, entry_id, subentry_id, include_usage=include_usage
        )
        from .helpers import effective_api_provider, entry_api_provider

        rollback_provider = entry_api_provider(entry)
        target_provider = (
            effective_api_provider(
                provider_data.get("api_provider"), provider_data.get("base_url")
            )
            if provider_data is not None
            else rollback_provider
        )
        connections = None
        if provider_data is not None and provider_data != entry.data:
            connections = {
                "target": deepcopy(dict(provider_data)),
                "rollback": deepcopy(dict(entry.data)),
            }
        rollback = await backup._snapshot_for_restore(
            managers,
            subentry,
            provider=entry_api_provider(entry),
            include_usage=include_usage,
        )
        if not isinstance(value, backup.PreparedRestore):
            from .transfer import async_materialize_restore

            # Public legacy documents contain redaction markers. Resolve them
            # against the exclusively owned destination before journaling, using
            # the same context checks and fail-closed policy as selective restore.
            prepared, _preview = await async_materialize_restore(
                hass, entry, subentry, value, current_snapshot=rollback
            )
        if prepared.journaled_sections is not None:
            rollback = replace(rollback, journaled_sections=prepared.journaled_sections)
        prepared = await asyncio.to_thread(
            _with_destination_archive_privacy, prepared, rollback, subentry_id
        )
        from .request import validate_runtime_readiness

        try:
            validate_runtime_readiness(
                prepared.config,
                provider_data
                if provider_data is not None
                else getattr(entry, "data", {}),
            )
        except HomeAssistantError as err:
            raise backup.BackupError(
                f"Restored request is not runtime-ready: {err}"
            ) from err
        journal = await asyncio.to_thread(
            _new_journal,
            entry_id,
            subentry_id,
            prepared,
            rollback,
            target_provider=target_provider,
            rollback_provider=rollback_provider,
            provider_connections=connections,
        )
        if connections:
            _journal_provider_connections(
                journal, {"target": target_provider, "rollback": rollback_provider}
            )
        # PreparedRestore bypasses inspect_backup's serialized-size validation.
        # Validate the actual journal documents before even the write-ahead save.
        for section in ("target", "rollback"):
            try:
                await backup.async_inspect_backup(
                    _journal_backup_document(
                        journal[section], prepared.journaled_sections
                    ),
                    subentry_id,
                    max_bytes=backup.MAX_BACKUP_BYTES,
                    private_journal=True,
                    provider=target_provider
                    if section == "target"
                    else rollback_provider,
                )
            except HomeAssistantError as err:
                raise backup.BackupError(
                    f"Restore cannot start because its {section} recovery snapshot is not recoverable: {err}"
                ) from err

        # This is the write-ahead boundary. If it cannot be verified, no category
        # has changed and a possibly-written journal remains harmless recovery data.
        if connections:
            _claim_provider_restore(hass, entry_id, subentry_id)
        try:
            journal_saved = await _async_write_journal_verified(store, journal)
        except _JournalVerificationUnavailable as err:
            raise mark_mutation_failure(
                backup.BackupError(
                    "Restore could not start because its recovery journal could not be "
                    "durably verified"
                ),
                "rejected",
            ) from err
        if not journal_saved:
            raise mark_mutation_failure(
                backup.BackupError(
                    "Restore could not start because its recovery journal could not be "
                    "durably verified"
                ),
                "rejected",
            )

        try:
            await _apply_prepared(managers, prepared)
        except Exception as err:
            await _recover_failed_apply(
                hass, entry, subentry, managers, store, rollback, err
            )

        # The target durable categories are complete. Persist and verify the commit
        # decision before exposing target configuration. If verification itself is
        # unavailable, the on-disk phase is authoritative at the next startup.
        committed_journal = {**journal, "phase": _PHASE_COMMITTED}
        try:
            committed = await _async_write_journal_verified(store, committed_journal)
        except _JournalVerificationUnavailable as err:
            raise mark_mutation_failure(
                backup.BackupError(
                    "Restore data was written, but its commit decision could not be "
                    "verified; restart Home Assistant to finish recovery"
                ),
                "uncertain",
                recovery_pending=True,
            ) from err
        if not committed:
            await _recover_failed_apply(
                hass,
                entry,
                subentry,
                managers,
                store,
                rollback,
                backup.BackupError("Restore commit decision could not be persisted"),
            )

        try:
            reset_restored_runtime(
                hass,
                entry_id,
                subentry_id,
                managers,
                prepared.preserve_retained_continuity,
                prepared.replaces_usage and prepared.restores("usage"),
            )
            context: dict[str, Any] = {}
            if target_provider != rollback_provider:
                context["provider"] = target_provider
            if connections:
                context["provider_data"] = connections["target"]
            await _update_configuration(
                hass,
                entry,
                subentry,
                prepared,
                **context,
            )
            await store.async_remove()
        except Exception as err:
            _LOGGER.exception("Agent restore committed but completion remains pending")
            raise mark_mutation_failure(
                backup.BackupError(
                    "Restore committed successfully, but completion is pending; restart "
                    "Home Assistant to finish recovery"
                ),
                "committed",
                recovery_pending=True,
            ) from err

    return {"status": "restored", "summary": prepared.summary()}


async def _async_recover_pending_restore(
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
        from .helpers import entry_api_provider

        if isinstance(raw, Mapping) and "provider_connections" in raw:
            _claim_provider_restore(hass, entry_id, subentry_id)
        journal, target, rollback = await _async_load_journal(
            raw, entry_id, subentry_id, provider=entry_api_provider(entry)
        )
        side = "target" if journal["phase"] == _PHASE_COMMITTED else "rollback"
        selected = target if side == "target" else rollback
        providers = _journal_providers(journal, entry_api_provider(entry))
        selected_provider = providers[side]
        connections = _journal_provider_connections(journal, providers)
        if connections is not None:
            if not any(entry.data == data for data in connections.values()):
                raise backup.BackupError(
                    "Pending restore cannot establish the provider connection generation"
                )
            from .request import validate_runtime_readiness

            validate_runtime_readiness(selected.config, connections[side])
        elif providers["target"] != providers["rollback"] and (
            entry_api_provider(entry) != selected_provider
            or (
                side == "target"
                and (
                    subentry.data != selected.config or subentry.title != selected.title
                )
            )
        ):
            raise backup.BackupError(
                "Older restore journal cannot establish the matching provider connection"
            )

        try:
            managers = await _recovery_managers(hass, entry_id, subentry_id, selected)
            await _apply_prepared(managers, selected)
            reset_restored_runtime(
                hass,
                entry_id,
                subentry_id,
                managers,
                journal["phase"] != _PHASE_COMMITTED,
                journal["phase"] == _PHASE_COMMITTED and selected.restores("usage"),
            )
            context: dict[str, Any] = {}
            if any(value != entry_api_provider(entry) for value in providers.values()):
                context.update(
                    provider=selected_provider, previous_provider=providers["rollback"]
                )
            if connections is not None:
                context["provider_data"] = connections[side]
            await _update_configuration(
                hass,
                entry,
                subentry,
                selected,
                **context,
            )
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


async def _async_recovery_operation[T](
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    operation: Callable[[], Awaitable[T]],
    *,
    quarantine_on_entry: bool = True,
) -> T:
    """Retain quarantine until the journal is authoritatively absent."""
    gate = get_agent_maintenance_gate(hass, entry.entry_id, subentry.subentry_id)

    async def run() -> T:
        if quarantine_on_entry:
            gate.recovery_required = True
        with gate.recovery_work():
            failure: Exception | None = None
            try:
                return await operation()
            except Exception as err:
                failure = err
                raise
            finally:
                try:
                    absent = (
                        await _journal_store(
                            hass, entry.entry_id, subentry.subentry_id
                        ).async_load()
                        is None
                    )
                except Exception:
                    absent = False
                gate.recovery_required = not absent
                if absent:
                    from .provider_credentials import PROVIDER_RESTORE_OWNERS

                    owners = hass.data.get(PROVIDER_RESTORE_OWNERS, {})
                    if owners.get(entry.entry_id) == subentry.subentry_id:
                        owners.pop(entry.entry_id)
                if failure is not None:
                    evidence = mutation_failure(failure)
                    if evidence is not None or not absent:
                        mark_mutation_failure(
                            failure,
                            evidence.outcome if evidence is not None else "uncertain",
                            recovery_pending=not absent,
                        )
                statuses = hass.data.setdefault(SUBSYSTEM_STATUS_KEY, {})
                if not absent:
                    statuses.setdefault((entry.entry_id, subentry.subentry_id), {})[
                        "restore_recovery"
                    ] = {
                        "configured": True,
                        "status": "recovery_required",
                    }
                elif (
                    status := statuses.get((entry.entry_id, subentry.subentry_id))
                ) is not None:
                    status.pop("restore_recovery", None)

    if gate.owns_exclusive():
        return await run()
    return await _async_run_exclusive_operation(gate, run)


async def async_restore_backup_recoverably(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    value: Any,
    *,
    provider_data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Restore with exclusive ownership and quarantine any unresolved journal."""
    result = await _async_recovery_operation(
        hass,
        entry,
        subentry,
        lambda: _async_restore_backup_recoverably(
            hass, entry, subentry, value, provider_data=provider_data
        ),
        quarantine_on_entry=False,
    )
    await async_finish_restore_reload(hass, entry, subentry)
    return result


async def async_recover_pending_restore(
    hass: HomeAssistant, entry: Any, subentry: Any
) -> bool:
    """Recover under exclusive ownership, keeping failures isolated to this agent."""
    return await _async_recovery_operation(
        hass,
        entry,
        subentry,
        lambda: _async_recover_pending_restore(hass, entry, subentry),
    )


async def async_recover_pending_restores(hass: HomeAssistant) -> None:
    """Recover all configured conversation-agent journals before agents are loaded."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        for subentry in entry.subentries.values():
            if subentry.subentry_type != "conversation":
                continue
            try:
                await async_recover_pending_restore(hass, entry, subentry)
            except Exception:
                _LOGGER.exception(
                    "Assistant %s/%s remains unavailable pending restore recovery",
                    entry.entry_id,
                    subentry.subentry_id,
                )


async def async_finish_restore_reload(
    hass: HomeAssistant, entry: Any, subentry: Any
) -> None:
    """Refresh setup-owned metadata only after the outer restore lease is released."""
    gate = get_agent_maintenance_gate(hass, entry.entry_id, subentry.subentry_id)
    pending = hass.data.get(f"{DOMAIN}.restore_reload_pending", set())
    if gate.owns_exclusive() or entry.entry_id not in pending:
        return
    from homeassistant.config_entries import ConfigEntryState

    async def reload_and_reconcile() -> None:
        if getattr(
            entry, "state", None
        ) is ConfigEntryState.LOADED and not await hass.config_entries.async_reload(
            entry.entry_id
        ):
            raise HomeAssistantError(
                "Restore completed, but reloading the assistant failed"
            )
        pending.discard(entry.entry_id)

    # This transition spans removal of the old entry's runtime. HA must own and
    # track the task; binding it to the unloading entry would cancel the reload.
    try:
        await async_settle_owned_operation(
            hass.async_create_task(
                reload_and_reconcile(), name="EOAI restore reload and reconciliation"
            )
        )
    except Exception as err:
        mark_mutation_failure(err, "committed")
        raise
