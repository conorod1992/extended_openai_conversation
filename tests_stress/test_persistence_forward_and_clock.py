"""Nightly persistence compatibility and wall-clock discontinuity acceptance."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses.agent_config import (
    merge_agent_config,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    _MANAGERS as RULE_MANAGERS,
    async_get_request_rules,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.util import dt as dt_util
from tests_stress.conftest import record
from tests_stress.test_temporary_memory_scale import MemoryStore


async def test_opaque_future_agent_fields_survive_edit_backup_and_reload(
    hass,
    stress_trace,
) -> None:
    future = {"schema": 99, "nested": {"keep": ["unrecognized", {"flag": True}]}}
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Future field compatibility",
        data={CONF_API_KEY: "sk-local", CONF_SKIP_AUTHENTICATION: True},
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {"future_agent_extension": deepcopy(future)},
                "subentry_type": "conversation",
                "title": "Future field agent",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    subentry = next(iter(entry.subentries.values()))
    before = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    assert before["agent"]["config"]["future_agent_extension"] == future
    edited = merge_agent_config(
        subentry.data, {"prompt": "Known edit after future schema"}
    )
    hass.config_entries.async_update_subentry(entry, subentry, data=edited)
    await hass.async_block_till_done()
    assert subentry.data["future_agent_extension"] == future
    saved = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    assert saved["agent"]["config"]["future_agent_extension"] == future
    assert saved["agent"]["config"]["prompt"] == "Known edit after future schema"
    assert (
        backup.inspect_backup(saved, subentry.subentry_id).config[
            "future_agent_extension"
        ]
        == future
    )
    without_opaque = dict(subentry.data)
    without_opaque.pop("future_agent_extension")
    hass.config_entries.async_update_subentry(entry, subentry, data=without_opaque)
    assert (await backup.async_restore_backup(hass, entry, subentry, saved))[
        "status"
    ] == "restored"
    assert subentry.data["future_agent_extension"] == future
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    subentry = next(iter(entry.subentries.values()))
    assert subentry.data["future_agent_extension"] == future
    assert (await backup.async_collect_backup_snapshot(hass, entry, subentry))["agent"][
        "config"
    ]["future_agent_extension"] == future
    record(stress_trace, "future_field_preserved", reloads=1, backup_round_trips=3)


async def test_future_backup_version_refuses_without_mutation(
    hass, stress_trace
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Unsupported downgrade boundary",
        data={CONF_API_KEY: "sk-local", CONF_SKIP_AUTHENTICATION: True},
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {},
                "subentry_type": "conversation",
                "title": "Agent",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    subentry = next(iter(entry.subentries.values()))
    before = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    future = deepcopy(before)
    future["version"] = backup.BACKUP_VERSION + 1
    with pytest.raises(backup.BackupError, match="newer unsupported backup format"):
        await backup.async_restore_backup(hass, entry, subentry, future)
    after = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    before.pop("created_at")
    after.pop("created_at")
    assert after == before
    record(
        stress_trace, "unsupported_future_restore_rejected", version=future["version"]
    )


async def test_opaque_future_rule_store_field_survives_group_edit_and_restore(
    hass,
    stress_trace,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Future rules",
        data={CONF_API_KEY: "sk-local", CONF_SKIP_AUTHENTICATION: True},
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {},
                "subentry_type": "conversation",
                "title": "Rules agent",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    subentry = next(iter(entry.subentries.values()))
    rules = await async_get_request_rules(hass, entry.entry_id, subentry.subentry_id)
    raw = await rules._store.async_load()
    future = {"format": 8, "nested": {"preserve": [1, 2, 3]}}
    await rules._store.async_save({**(raw or {}), "future_rule_index": future})
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.data.get(RULE_MANAGERS, {}).pop((entry.entry_id, subentry.subentry_id), None)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    rules = await async_get_request_rules(hass, entry.entry_id, subentry.subentry_id)
    assert (await rules._store.async_load())["future_rule_index"] == future
    await rules.async_set_groups([{"id": "nightly", "name": "Nightly group"}])
    saved = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    assert "future_rule_index" not in saved["request_rules"]
    assert (await rules._store.async_load())["future_rule_index"] == future
    assert (await backup.async_restore_backup(hass, entry, subentry, saved))[
        "status"
    ] == "restored"
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.data.get(RULE_MANAGERS, {}).pop((entry.entry_id, subentry.subentry_id), None)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    rules = await async_get_request_rules(hass, entry.entry_id, subentry.subentry_id)
    assert (await rules._store.async_load())["future_rule_index"] == future
    assert rules.snapshot()["groups"][0]["id"] == "nightly"
    record(stress_trace, "future_rule_field_preserved", reloads=2, backups=1)


async def test_temporary_memory_forward_backward_jump_is_irreversible(
    monkeypatch,
    stress_seed,
    stress_trace,
) -> None:
    base = dt_util.utcnow()
    now = base
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    store = MemoryStore({"records": []})
    manager = TemporaryMemory(store)
    await manager.async_initialize()
    owner = f"user:clock-{stress_seed}"
    await manager.async_add(
        owner,
        "clock discontinuity marker",
        (base + timedelta(hours=2)).isoformat(),
        "nightly",
        owner_scope_id=owner,
    )
    assert len(await manager.async_active(owner, owner_scope_id=owner)) == 1
    now = base + timedelta(days=30)
    for _ in range(5):
        assert await manager.async_active(owner, owner_scope_id=owner) == []
    now = base - timedelta(days=30)
    for _ in range(5):
        assert await manager.async_active(owner, owner_scope_id=owner) == []
    if manager._prune_save_task is not None:
        await manager._prune_save_task
    restarted = TemporaryMemory(MemoryStore(store.data))
    await restarted.async_initialize()
    assert await restarted.async_active(owner, owner_scope_id=owner) == []
    assert (await restarted.async_backup_data())["records"] == []
    record(
        stress_trace, "clock_jump", forward_days=30, backward_days=30, evaluations=11
    )
