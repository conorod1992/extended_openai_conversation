"""Remove private agent data after its Home Assistant configuration is deleted."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from homeassistant.helpers.storage import Store

from .agent_maintenance import (
    _async_run_exclusive_operation,
    get_agent_maintenance_gate,
)
from .const import DOMAIN
from .strict_store import PropagatingWriteStore

KNOWN_AGENTS = f"{DOMAIN}.known_agents"
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


def _owned_stores(owner: Any) -> list[Store[Any]]:
    """Find manager-owned Store handles so pending delayed writes are cancelled too."""
    if isinstance(owner, Store):
        return [owner]
    result = []
    for attribute in (
        "_store",
        "_storage",
        "_daily_storage",
        "_detail_storage",
        "_embedding_cache_storage",
        "_metadata",
    ):
        child = getattr(owner, attribute, None)
        if child is not None:
            result.extend(_owned_stores(child))
    return result


async def async_delete_agent_data(hass: Any, entry_id: str, subentry_id: str) -> None:
    """Drain old requests, erase durable stores, then fence out detached writers."""
    gate = get_agent_maintenance_gate(hass, entry_id, subentry_id)
    if gate.deleted:
        return
    await gate.async_retire_readers()

    async def remove() -> None:
        with gate.recovery_work():
            from .delayed_tools import DATA_DELAYED_TOOL_MANAGER
            from .model_lifecycle import clear_retirement_failure

            delayed = hass.data.get(DOMAIN, {}).get(DATA_DELAYED_TOOL_MANAGER)
            if delayed is not None:
                await delayed.async_remove_agent(entry_id, subentry_id)
            key = (entry_id, subentry_id)
            stores = []
            for suffix in _MANAGER_KEYS:
                manager = hass.data.get(f"{DOMAIN}.{suffix}", {}).get(key)
                if manager is None:
                    continue
                tasks = [
                    getattr(manager, attribute, None)
                    for attribute in ("_prune_task", "_prune_save_task")
                ]
                pending = [
                    task
                    for task in tasks
                    if isinstance(task, asyncio.Task) and not task.done()
                ]
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                if hasattr(manager, "_stopping"):
                    manager._stopping = True
                stores.extend(_owned_stores(manager))
            for store in stores:
                # Cancel delayed/final writers on the actual owned handle before
                # erasing its files, including HA's in-memory Store test adapter.
                store._async_cleanup_delay_listener()
                store._async_cleanup_final_write_listener()
                await store.async_remove()
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
    current = set(entry.subentries)
    for subentry_id in known - current:
        await async_delete_agent_data(hass, entry.entry_id, subentry_id)
        known.discard(subentry_id)
    known.update(current)


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
