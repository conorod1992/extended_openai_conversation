"""Unloaded Rule generations cannot overwrite their acknowledged replacements."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import custom_components.extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import request_rules as rr
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import json as json_util
from tests.test_request_rules import local_rule


@pytest.fixture
async def unload_case(tmp_path, monkeypatch):
    hass = HomeAssistant(str(tmp_path))
    platforms_unloaded = asyncio.Event()

    async def unload_platforms(*_):
        platforms_unloaded.set()
        return True

    entry = SimpleNamespace(
        entry_id="entry", subentries={"agent": SimpleNamespace(subentry_id="agent")}
    )
    hass.config_entries = SimpleNamespace(
        async_get_entry=lambda _: None,
        async_unload_platforms=AsyncMock(side_effect=unload_platforms),
    )
    # A disabled assistant has no platforms to stop, but its data/getters remain.
    monkeypatch.setattr(
        integration.entity_platform, "async_get_platforms", lambda *_: []
    )
    key = f"{rr.STORAGE_KEY_PREFIX}.entry.agent"
    try:
        yield hass, entry, key, platforms_unloaded
    finally:
        await hass.async_stop(force=True)


async def fresh(hass, key):
    manager = rr.RequestRules(rr.RequestRuleStore(hass, rr.STORAGE_VERSION, key))
    await manager.async_initialize()
    return manager


@pytest.mark.parametrize("cancel_unload", [False, True])
async def test_unload_drains_cold_migration_before_replacement_and_next_edit(
    unload_case, monkeypatch, cancel_unload
):
    hass, entry, key, platforms_unloaded = unload_case
    seed = rr.RequestRuleStore(hass, rr.STORAGE_VERSION, key).bind_agent(
        "entry", "agent"
    )
    rule = local_rule("Legacy saved rule")
    await seed.async_save({"defaults": dict(rr.DEFAULT_MATCHING), "rules": [rule]})
    entered, release = asyncio.Event(), threading.Event()
    load_json = json_util.load_json
    loop = asyncio.get_running_loop()
    first = True

    def blocked_read(path, *args, **kwargs):
        nonlocal first
        value = load_json(path, *args, **kwargs)
        if path == seed.path and first:
            first = False
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(10)
        return value

    monkeypatch.setattr(json_util, "load_json", blocked_read)
    getter = asyncio.create_task(rr.async_get_request_rules(hass, "entry", "agent"))
    unloading = None
    try:
        await entered.wait()
        unloading = asyncio.create_task(integration.async_unload_entry(hass, entry))
        await platforms_unloaded.wait()
        done, _ = await asyncio.wait({unloading}, timeout=0.05)
        assert not done, "Unload exposed a replacement before the old read settled"
        if cancel_unload:
            unloading.cancel()
            done, _ = await asyncio.wait({unloading}, timeout=0.05)
            assert not done, "Cancellation abandoned owned Rule retirement"
        release.set()
        with pytest.raises(HomeAssistantError, match="reloaded"):
            await getter
        if cancel_unload:
            with pytest.raises(asyncio.CancelledError):
                await unloading
        else:
            assert await unloading
        assert ("entry", "agent") not in hass.data[rr._MANAGERS]
        replacement = await rr.async_get_request_rules(hass, "entry", "agent")
        updated = replacement.rule_snapshot(rule["id"])
        updated["name"] = "Newer acknowledged edit"
        await replacement.async_update(
            rule["id"], updated, expected_revision=replacement.revision()
        )
        assert (await fresh(hass, key)).rule_snapshot(rule["id"])[
            "name"
        ] == "Newer acknowledged edit"
    finally:
        release.set()
        await asyncio.gather(
            *(task for task in (getter, unloading) if task), return_exceptions=True
        )


@pytest.mark.parametrize("action", ["create", "update", "replace"])
async def test_detached_writer_without_revision_cannot_overwrite_new_generation(
    unload_case, action
):
    hass, entry, key, _ = unload_case
    old = await rr.async_get_request_rules(hass, "entry", "agent")
    created = await old.async_create(local_rule("Retained"))
    backup = await old.async_backup_data()
    assert await integration.async_unload_entry(hass, entry)
    current = await rr.async_get_request_rules(hass, "entry", "agent")
    updated = {**current.rule_snapshot(created["id"]), "name": "New owner"}
    await current.async_update(
        created["id"], updated, expected_revision=current.revision()
    )
    with pytest.raises(HomeAssistantError, match="reloaded"):
        if action == "create":
            await old.async_create(local_rule("Detached create"))
        elif action == "update":
            await old.async_update(
                created["id"], {**created, "name": "Detached update"}
            )
        else:
            await old.async_replace_backup(backup)
    assert (await fresh(hass, key)).snapshot()["rules"] == current.snapshot()["rules"]
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await rr.async_evaluate_rule(
            hass, old, rr.RequestRuleRuntime(), "good night", "session"
        )
    await current.async_update(
        created["id"],
        {**updated, "name": "Next edit"},
        expected_revision=current.revision(),
    )
    assert (await fresh(hass, key)).rule_snapshot(created["id"])["name"] == "Next edit"


async def test_failed_platform_unload_retains_current_writable_owner(unload_case):
    hass, entry, key, _ = unload_case
    rules = await rr.async_get_request_rules(hass, "entry", "agent")
    created = await rules.async_create(local_rule("Retained"))
    hass.config_entries.async_unload_platforms.side_effect = None
    hass.config_entries.async_unload_platforms.return_value = False
    assert not await integration.async_unload_entry(hass, entry)
    assert await rr.async_get_request_rules(hass, "entry", "agent") is rules
    await rules.async_update(created["id"], {**created, "name": "Still writable"})
    assert (await fresh(hass, key)).rule_snapshot(created["id"])[
        "name"
    ] == "Still writable"


async def test_native_shared_reload_does_not_upgrade_its_maintenance_lease(unload_case):
    hass, entry, key, _ = unload_case
    old = await rr.async_get_request_rules(hass, "entry", "agent")
    created = await old.async_create(local_rule("Retained"))
    gate = get_agent_maintenance_gate(hass, "entry", "agent")
    async with gate.shared():
        assert await asyncio.wait_for(integration.async_unload_entry(hass, entry), 2)
        current = await rr.async_get_request_rules(hass, "entry", "agent")
        await current.async_update(
            created["id"], {**created, "name": "After native reload"}
        )
    assert (await fresh(hass, key)).rule_snapshot(created["id"])[
        "name"
    ] == "After native reload"


async def test_unload_waits_for_active_restore_participant_before_retirement(
    unload_case,
):
    hass, entry, key, platforms_unloaded = unload_case
    rules = await rr.async_get_request_rules(hass, "entry", "agent")
    created = await rules.async_create(local_rule("Retained"))
    replacement = await rules.async_backup_data()
    replacement["rules"][0]["name"] = "Restored generation"
    gate = get_agent_maintenance_gate(hass, "entry", "agent")
    unloading = None
    try:
        async with gate.exclusive():
            # Model an independently dispatched reload, not the exclusive owner.
            from contextvars import Context

            unloading = asyncio.create_task(
                integration.async_unload_entry(hass, entry), context=Context()
            )
            await platforms_unloaded.wait()
            done, _ = await asyncio.wait({unloading}, timeout=0.05)
            assert not done
            await rules.async_replace_backup(replacement)
            assert rules.initialized
        assert await unloading
        current = await rr.async_get_request_rules(hass, "entry", "agent")
        assert current.rule_snapshot(created["id"])["name"] == "Restored generation"
        await current.async_update(
            created["id"], {**current.rule_snapshot(created["id"]), "name": "Next edit"}
        )
        assert (await fresh(hass, key)).rule_snapshot(created["id"])[
            "name"
        ] == "Next edit"
    finally:
        if unloading:
            await asyncio.gather(unloading, return_exceptions=True)


async def test_unload_does_not_evict_an_owner_published_after_its_capture(
    unload_case, monkeypatch
):
    hass, entry, key, _ = unload_case
    old = await rr.async_get_request_rules(hass, "entry", "agent")
    created = await old.async_create(local_rule("Retained"))
    current = await fresh(hass, key)
    runtime, newer_runtime = object(), object()
    hass.data[rr._RUNTIMES] = {("entry", "agent"): runtime}
    retire = old.async_retire

    async def replace_during_retirement():
        await retire()
        hass.data[rr._MANAGERS]["entry", "agent"] = current
        hass.data[rr._RUNTIMES]["entry", "agent"] = newer_runtime

    monkeypatch.setattr(old, "async_retire", replace_during_retirement)
    assert await integration.async_unload_entry(hass, entry)
    assert hass.data[rr._MANAGERS]["entry", "agent"] is current
    assert hass.data[rr._RUNTIMES]["entry", "agent"] is newer_runtime
    await current.async_update(created["id"], {**created, "name": "Preserved owner"})
    assert (await fresh(hass, key)).rule_snapshot(created["id"])[
        "name"
    ] == "Preserved owner"


async def test_unload_drains_an_already_dispatched_write_before_replacement(
    unload_case, monkeypatch
):
    hass, entry, key, platforms_unloaded = unload_case
    old = await rr.async_get_request_rules(hass, "entry", "agent")
    created = await old.async_create(local_rule("Retained"))
    write = old._store._write_prepared_data
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def blocked_write(*args):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10)
        write(*args)

    monkeypatch.setattr(old._store, "_write_prepared_data", blocked_write)
    writing = asyncio.create_task(
        old.async_update(created["id"], {**created, "name": "Before reload"})
    )
    unloading = None
    try:
        await entered.wait()
        unloading = asyncio.create_task(integration.async_unload_entry(hass, entry))
        await platforms_unloaded.wait()
        done, _ = await asyncio.wait({unloading}, timeout=0.05)
        assert not done
        release.set()
        assert (await writing)["name"] == "Before reload"
        assert await unloading
        current = await rr.async_get_request_rules(hass, "entry", "agent")
        assert current.rule_snapshot(created["id"])["name"] == "Before reload"
        await current.async_update(created["id"], {**created, "name": "After reload"})
        assert (await fresh(hass, key)).rule_snapshot(created["id"])[
            "name"
        ] == "After reload"
    finally:
        release.set()
        await asyncio.gather(
            *(task for task in (writing, unloading) if task), return_exceptions=True
        )


async def test_unload_retires_a_condition_checker_prepared_before_publication(
    unload_case, monkeypatch
):
    hass, entry, key, _ = unload_case
    old = await rr.async_get_request_rules(hass, "entry", "agent")
    rule = local_rule("Retained")
    rule["conditions"] = [
        {
            "condition": "state",
            "entity_id": "sensor.probe",
            "state": "on",
            "for": "00:00:10",
        }
    ]
    created = await old.async_create(rule)
    entered, release = asyncio.Event(), asyncio.Event()
    initialize = rr._NativeConditions.async_create.__func__
    prepared = []

    async def pause(cls, *args):
        checker = await initialize(cls, *args)
        prepared.append(checker)
        entered.set()
        await release.wait()
        return checker

    monkeypatch.setattr(rr._NativeConditions, "async_create", classmethod(pause))
    matching = asyncio.create_task(old.async_match(hass, "good night"))
    try:
        await entered.wait()
        assert await integration.async_unload_entry(hass, entry)
        release.set()
        with pytest.raises(
            HomeAssistantError, match="condition could not be evaluated"
        ):
            await matching
        assert all(not checker._checkers for checker in prepared)
        assert not old._condition_checkers
        current = await rr.async_get_request_rules(hass, "entry", "agent")
        await current.async_update(created["id"], {**created, "name": "New owner"})
        assert (await fresh(hass, key)).rule_snapshot(created["id"])[
            "name"
        ] == "New owner"
    finally:
        release.set()
        await asyncio.gather(matching, return_exceptions=True)


async def test_function_validation_cannot_commit_against_retired_rule_owner(
    unload_case, monkeypatch
):
    from copy import deepcopy
    from unittest.mock import Mock

    import yaml

    from custom_components.extended_openai_conversation_responses import (
        function_dependency_integrity as integrity,
        management_ui as ui,
    )
    from tests.test_save_validation_reuse import _hass_with_agent

    hass, _, key, _ = unload_case
    _, entry, subentry = _hass_with_agent()
    entry.entry_id, subentry.subentry_id = "entry", "agent"
    entry.subentries = {"agent": subentry}
    monkeypatch.setattr(ui, "entry_and_agent", lambda *_: (entry, subentry))

    def save(_entry, _subentry, *, data, **_):
        subentry.data = deepcopy(data)

    hass.config_entries.async_update_subentry = Mock(side_effect=save)
    old = await rr.async_get_request_rules(hass, "entry", "agent")
    rule = local_rule("Retained")
    rule["action"]["actions"] = [
        {
            "action": f"{ui.DOMAIN}.call_function",
            "data": {"function": "native_probe", "arguments": {}},
        }
    ]
    created = await old.async_create(rule)
    before = deepcopy(subentry.data)
    original = integrity.async_validate_static_function_arguments
    changed = False

    async def unload_during_validation(*args):
        nonlocal changed
        await original(*args)
        if not changed:
            changed = True
            assert await integration.async_unload_entry(hass, entry)

    monkeypatch.setattr(
        integrity, "async_validate_static_function_arguments", unload_during_validation
    )
    tool = yaml.safe_load(subentry.data["functions"])[0]
    tool["spec"]["description"] = "New description"
    message = {
        "entry_id": "entry",
        "subentry_id": "agent",
        "section": "tools",
        "action": "save",
        "original_name": "native_probe",
        "tool": tool,
    }
    with pytest.raises(HomeAssistantError, match="Request Rules changed"):
        await ui.async_management_command(hass, "admin", True, message)
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()
    monkeypatch.setattr(integrity, "async_validate_static_function_arguments", original)
    await ui.async_management_command(hass, "admin", True, message)
    assert (
        yaml.safe_load(subentry.data["functions"])[0]["spec"]["description"]
        == "New description"
    )
    current = await rr.async_get_request_rules(hass, "entry", "agent")
    await current.async_update(
        created["id"],
        {**current.rule_snapshot(created["id"]), "name": "Next Rule edit"},
    )
    assert (await fresh(hass, key)).rule_snapshot(created["id"])[
        "name"
    ] == "Next Rule edit"
