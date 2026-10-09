"""Reusable ledger of assistant-owned runtime resources after HA removal.

Avoid global task-count assertions: HA legitimately schedules unrelated work.
Only resources carrying the removed assistant's entry/subentry identity count.
"""
from dataclasses import dataclass

import pytest
from homeassistant.exceptions import HomeAssistantError
from pathlib import Path

from custom_components.extended_openai_conversation_responses.const import DOMAIN


@dataclass(frozen=True)
class ResourceLedger:
    manager_keys: frozenset[tuple[str, str]]
    retained_paths: frozenset[Path]
    callbacks: tuple[object, ...]
    retirement_fences: tuple[object, ...] = ()


def capture_owned(hass, entry_id: str, subentry_id: str):
    key = (entry_id, subentry_id)
    managers = {}
    callbacks = []
    retirement_fences = []
    for name, value in hass.data.items():
        if not str(name).startswith(DOMAIN + ".") or not isinstance(value, dict):
            continue
        if key in value:
            manager = value[key]
            # A deleted gate is an intentional security fence, not a live manager.
            if str(name) == DOMAIN + ".agent_maintenance_gates" and manager.deleted:
                retirement_fences.append(manager)
                continue
            managers[str(name)] = manager
            for attr in ("_cancel_retention", "_cancel_stop", "_unsub", "_unsubscriber"):
                handle = getattr(manager, attr, None)
                if callable(handle):
                    callbacks.append(handle)
    config = Path(hass.config.path(".storage"))
    paths = (
        frozenset(p for p in config.iterdir() if entry_id in p.name and subentry_id in p.name)
        if config.is_dir() else frozenset()
    )
    return ResourceLedger(frozenset((k, subentry_id) for k in managers), paths, tuple(callbacks), tuple(retirement_fences))


def assert_owned_removed(before: ResourceLedger, after: ResourceLedger):
    assert not after.manager_keys, f"Stale assistant manager keys: {after.manager_keys}"
    assert not after.retained_paths, f"Stale assistant store paths: {after.retained_paths}"
    assert not after.callbacks, "Stale assistant-owned maintenance subscriptions"

    for fence in after.retirement_fences:
        assert fence.deleted and not fence._writer_active
        assert not fence._active_readers and not fence._reader_tasks
        with pytest.raises(HomeAssistantError, match="deleted"):
            fence.require_available()
