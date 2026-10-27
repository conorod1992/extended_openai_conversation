"""Remove private agent data after its Home Assistant configuration is deleted."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntryChange
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_call_later

from .agent_maintenance import (
    _async_run_exclusive_operation,
    get_agent_maintenance_gate,
)
from .const import DOMAIN
from .strict_store import PropagatingWriteStore, async_remove_agent_stores

KNOWN_AGENTS = f"{DOMAIN}.known_agents"
_LOGGER = logging.getLogger(__name__)
_LISTENER = f"{DOMAIN}.agent_deletion_listener"
_RETRY = f"{DOMAIN}.agent_deletion_retry"
_SECTIONS = (
    "memory",
    "temporary_memory",
    "knowledge",
    "archive",
    "usage",
    "guest_mode",
    "request_rules",
    "restore_transaction",
)
_MANAGER_KEYS = (
    "request_debug_managers",
    "memory_managers",
    "temporary_memory_managers",
    "knowledge_managers",
    "archive_managers",
    "usage_managers",
    "volatile_usage_managers",
    "guest_mode_managers",
    "request_rule_managers",
    "request_rule_runtimes",
    "function_group_runtimes",
    "continuity_managers",
    "usage_getter_locks",
    "backup_locks",
    "subsystem_status",
)


def _storage_names(
    hass: Any, entry_id: str, subentry_id: str | None = None
) -> list[str]:
    """Select exact agent prefixes, including archive partitions and embedding caches."""
    directory = Path(hass.config.path(".storage"))
    if not directory.exists():
        return []
    prefixes = tuple(f"{DOMAIN}.{section}.{entry_id}." for section in _SECTIONS)
    names = []
    for path in directory.iterdir():
        for prefix in prefixes:
            if not path.name.startswith(prefix):
                continue
            suffix = path.name[len(prefix) :]
            if (
                subentry_id is None
                or suffix == subentry_id
                or suffix.startswith(f"{subentry_id}.")
            ):
                names.append(path.name)
            break
    return names


def _stored_provider_ids(hass: Any) -> set[str]:
    """Discover only EOAI's exact private-store namespaces, including orphans."""
    directory = Path(hass.config.path(".storage"))
    if not directory.exists():
        return set()
    result = set()
    prefixes = tuple(f"{DOMAIN}.{section}." for section in _SECTIONS)
    for path in directory.iterdir():
        for prefix in prefixes:
            if path.name.startswith(prefix):
                entry_id, separator, subentry = path.name[len(prefix) :].partition(".")
                if entry_id and separator and subentry:
                    result.add(entry_id)
                break
    return result


def async_setup_agent_deletion(hass: Any) -> None:
    """Observe native assistant deletion for the integration's whole lifetime."""
    if _LISTENER in hass.data:
        return

    async def reconcile(*_: Any) -> None:
        hass.data.pop(_RETRY, lambda: None)()
        try:
            await async_reconcile_agent_storage(hass)
        except Exception:
            _LOGGER.exception("Private agent data cleanup failed; retrying")
            if _RETRY not in hass.data:
                hass.data[_RETRY] = async_call_later(hass, 60, reconcile)

    async def changed(change: ConfigEntryChange, entry: Any) -> None:
        if entry.domain == DOMAIN and change in {
            ConfigEntryChange.UPDATED,
            ConfigEntryChange.REMOVED,
        }:
            await reconcile()

    unsubscribe = async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, changed)
    hass.data[_LISTENER] = unsubscribe

    def stop(_: Any) -> None:
        hass.data.pop(_LISTENER, lambda: None)()
        hass.data.pop(_RETRY, lambda: None)()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop)


async def async_delete_agent_data(hass: Any, entry_id: str, subentry_id: str) -> None:
    """Drain old requests, erase durable stores, then fence out detached writers."""
    gate = get_agent_maintenance_gate(hass, entry_id, subentry_id)
    if gate.deleted:
        return
    await gate.async_retire_readers()

    async def remove() -> None:
        with gate.recovery_work():
            from .backup_transfer import async_remove_agent_transfers
            from .delayed_tools import (
                DATA_DELAYED_TOOL_MANAGER,
                async_remove_stored_agent_calls,
            )
            from .model_lifecycle import clear_retirement_failure

            delayed = hass.data.get(DOMAIN, {}).get(DATA_DELAYED_TOOL_MANAGER)
            if delayed is not None:
                await delayed.async_remove_agent(entry_id, subentry_id)
            else:
                await async_remove_stored_agent_calls(hass, entry_id, subentry_id)
            await async_remove_agent_transfers(hass, entry_id, subentry_id)
            key = (entry_id, subentry_id)
            for suffix in _MANAGER_KEYS:
                manager = hass.data.get(f"{DOMAIN}.{suffix}", {}).get(key)
                if manager is None:
                    continue
                if suffix == "request_debug_managers":
                    manager.clear()
                if suffix == "request_rule_managers":
                    manager.async_unload()
                if suffix in {
                    "usage_managers",
                    "volatile_usage_managers",
                    "temporary_memory_managers",
                }:
                    await manager.async_prepare_for_deletion()
            await async_remove_agent_stores(gate)
            names = await hass.async_add_executor_job(
                _storage_names, hass, entry_id, subentry_id
            )
            for name in names:
                await PropagatingWriteStore(hass, 1, name).async_remove()
            for suffix in _MANAGER_KEYS:
                hass.data.get(f"{DOMAIN}.{suffix}", {}).pop(key, None)
            clear_retirement_failure(hass, entry_id=entry_id, subentry_id=subentry_id)
            gate.deleted = True

    await _async_run_exclusive_operation(gate, remove)


async def async_delete_removed_subentries(hass: Any, entry: Any) -> None:
    known = hass.data.setdefault(KNOWN_AGENTS, {}).setdefault(
        entry.entry_id, set(entry.subentries)
    )
    # The parent's update listener is absent while unloaded, and KNOWN_AGENTS
    # disappears on restart. Discover exact owners from durable private stores.
    names = await hass.async_add_executor_job(_storage_names, hass, entry.entry_id)
    for name in names:
        for section in _SECTIONS:
            prefix = f"{DOMAIN}.{section}.{entry.entry_id}."
            if name.startswith(prefix):
                identity = name[len(prefix) :].split(".", 1)[0]
                if identity:
                    known.add(identity)
                break
    # A subentry can be added while the executor enumerates its new store.
    # Read configuration after the scan, and again after each awaited deletion.
    current = set(entry.subentries)
    for subentry_id in known - current:
        if subentry_id in entry.subentries:
            continue
        await async_delete_agent_data(hass, entry.entry_id, subentry_id)
        known.discard(subentry_id)
    known.update(entry.subentries)


async def async_reconcile_agent_storage(hass: Any) -> None:
    """Reconcile disabled parents too, before recovery and delayed work starts."""
    entries = list(hass.config_entries.async_entries(DOMAIN))
    for entry in entries:
        await async_delete_removed_subentries(hass, entry)
    stored = await hass.async_add_executor_job(_stored_provider_ids, hass)
    known = hass.data.get(KNOWN_AGENTS, {})
    for entry_id in stored | set(known):
        # Re-read after awaits: a provider added during enumeration is live.
        if any(
            entry.entry_id == entry_id
            for entry in hass.config_entries.async_entries(DOMAIN)
        ):
            continue
        await async_delete_entry_data(
            hass, SimpleNamespace(entry_id=entry_id, subentries={})
        )


async def async_delete_entry_data(hass: Any, entry: Any) -> None:
    """Also collect older orphan stores when the entire parent entry is removed."""
    names = await hass.async_add_executor_job(_storage_names, hass, entry.entry_id)
    identities = set(entry.subentries) | hass.data.get(KNOWN_AGENTS, {}).get(
        entry.entry_id, set()
    )
    for name in names:
        for section in _SECTIONS:
            prefix = f"{DOMAIN}.{section}.{entry.entry_id}."
            if name.startswith(prefix):
                identities.add(name[len(prefix) :].split(".", 1)[0])
                break
    for subentry_id in identities:
        await async_delete_agent_data(hass, entry.entry_id, subentry_id)
    hass.data.get(KNOWN_AGENTS, {}).pop(entry.entry_id, None)
