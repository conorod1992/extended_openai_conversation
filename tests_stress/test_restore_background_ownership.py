"""Installed retention and real setup export during a held restore publication."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import json

from pytest_homeassistant_custom_component.common import MockUser
import yaml

from custom_components.extended_openai_conversation_responses import (
    backup,
    conversation as agent_module,
    restore_recovery,
    transfer,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_RETENTION_DAYS,
    CONF_FUNCTION_TOOLS,
    DOMAIN,
    SERVICE_CALL_FUNCTION,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
    HomeAssistantArchiveStorage,
    async_get_archive,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    async_get_request_rules,
)
from custom_components.extended_openai_conversation_responses.scope import (
    ResolvedDataScope,
)
from homeassistant.components import conversation
from homeassistant.util import dt as dt_util
from tests_real_ha.test_acceptance_lifecycle import (
    _conversation_subentry,
    _make_entry,
    _setup_entry,
)
from tests_real_ha.test_backup_transfer_protocol import _transfer_call, _user_token
from tests_stress.conftest import record
from tests_stress.test_request_rules_matrix import rule


def _tool(name):
    return {
        "spec": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": name.upper()},
    }


def _rule(name):
    return rule(
        f"rule-{name}",
        f"run {name}",
        action={
            "actions": [
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {"function": name, "arguments": {}},
                }
            ]
        },
    )


async def _held_restore(hass, monkeypatch):
    callbacks = []
    installed_tracker = agent_module.async_track_time_interval

    def capture(hass, callback, interval, *args, **kwargs):
        if callback.__name__ == "_async_prune_archive_retention":
            callbacks.append(callback)
        return installed_tracker(hass, callback, interval, *args, **kwargs)

    monkeypatch.setattr(agent_module, "async_track_time_interval", capture)
    entry = _make_entry(
        "Owned restore",
        include_ai_task=False,
        conversation_options={
            CONF_ARCHIVE_ENABLED: True,
            CONF_ARCHIVE_RETENTION_DAYS: 7,
            CONF_FUNCTION_TOOLS: yaml.safe_dump([_tool("alpha")]),
        },
    )
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    archive = await async_get_archive(hass, entry.entry_id, subentry.subentry_id)
    rules = await async_get_request_rules(hass, entry.entry_id, subentry.subentry_id)
    await rules.async_create(_rule("alpha"))
    for owner in ("historic-a", "historic-b"):
        session = await archive.async_begin_session(
            owner,
            ResolvedDataScope(f"user:{owner}", "user", "context", owner),
            owner,
            archive_enabled=True,
            shared_archive_enabled=False,
            inactivity_minutes=30,
        )
        await archive.async_record_turn(
            session.session_id,
            run_id=f"run-{owner}",
            user_text=owner,
            assistant_text=f"reply-{owner}",
            successful=True,
        )
    target = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    historical = (dt_util.utcnow() - timedelta(days=30)).isoformat()
    for session in target["archive"]["sessions"]:
        session.update(started_at=historical, last_message_at=historical)
    for turn in target["archive"]["turns"]:
        turn["timestamp"] = historical
    target["agent"]["config"][CONF_ARCHIVE_RETENTION_DAYS] = 365
    target["agent"]["config"][CONF_FUNCTION_TOOLS] = [_tool("beta")]
    target["request_rules"]["rules"] = [_rule("beta")]
    entered, release = asyncio.Event(), asyncio.Event()
    update = restore_recovery._update_configuration

    async def gated_update(*args):
        if args[1].entry_id != entry.entry_id or release.is_set():
            return await update(*args)
        assert await archive.async_backup_data() == target["archive"]
        assert subentry.data[CONF_ARCHIVE_RETENTION_DAYS] == 7
        assert (await rules.async_backup_data())["rules"][0]["id"] == "rule-beta"
        entered.set()
        await release.wait()
        return await update(*args)

    monkeypatch.setattr(restore_recovery, "_update_configuration", gated_update)
    task = asyncio.create_task(
        backup.async_restore_backup(hass, entry, subentry, target)
    )
    await asyncio.wait_for(entered.wait(), 15)
    assert len(callbacks) == 1
    return entry, subentry, archive, target, callbacks[0], release, task


async def test_installed_retention_waits_for_restored_policy(
    hass, monkeypatch, stress_trace
):
    entry, subentry, archive, target, callback, release, restore = await _held_restore(
        hass, monkeypatch
    )
    started = asyncio.Event()
    from custom_components.extended_openai_conversation_responses.agent_maintenance import (
        get_agent_maintenance_gate,
    )

    gate = get_agent_maintenance_gate(hass, entry.entry_id, subentry.subentry_id)
    lease_attempted = asyncio.Event()
    prune_finished = asyncio.Event()
    shared = gate.shared
    prune = archive.async_prune

    @asynccontextmanager
    async def observed_shared():
        lease_attempted.set()
        async with shared():
            yield

    async def observed_prune(days):
        result = await prune(days)
        prune_finished.set()
        return result

    monkeypatch.setattr(gate, "shared", observed_shared)
    monkeypatch.setattr(archive, "async_prune", observed_prune)

    async def fire():
        started.set()
        await callback(dt_util.utcnow())

    retention = asyncio.create_task(fire())
    try:
        await started.wait()
        await asyncio.sleep(0)
        record(
            stress_trace,
            "retention_during_restore",
            imported_sessions=2,
            visible_retention_days=subentry.data[CONF_ARCHIVE_RETENTION_DAYS],
        )
        # Wait for either genuine admission attempt or the old destructive prune.
        async with asyncio.timeout(10):
            while not lease_attempted.is_set() and not prune_finished.is_set():
                await asyncio.sleep(0)
        if lease_attempted.is_set():
            assert not retention.done() and not prune_finished.is_set()
        release.set()
        assert (await asyncio.wait_for(restore, 15))["status"] == "restored"
        await asyncio.wait_for(retention, 15)
        assert subentry.data[CONF_ARCHIVE_RETENTION_DAYS] == 365
        assert await archive.async_backup_data() == target["archive"]
        fresh = ConversationArchive(
            HomeAssistantArchiveStorage(hass, entry.entry_id, subentry.subentry_id),
            subentry.subentry_id,
        )
        await fresh.async_initialize()
        assert await fresh.async_backup_data() == target["archive"]
        assert await fresh.async_prune(365) == {
            "deleted_sessions": 0,
            "deleted_turns": 0,
        }
        assert await fresh.async_prune(1) == {"deleted_sessions": 2, "deleted_turns": 2}
        record(
            stress_trace,
            "retention_restore_recovered",
            retained_sessions=2,
            retained_turns=2,
            restored_policy=365,
        )
        record(
            stress_trace,
            "summary",
            retention_restore_ownership_cases=1,
            layer="genuine-ha",
        )
    finally:
        release.set()
        await asyncio.gather(restore, retention, return_exceptions=True)
        await hass.async_block_till_done()
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_websocket_setup_export_is_coherent_across_restore(
    hass, hass_ws_client, monkeypatch, stress_trace
):
    entry, subentry, _archive, _target, _callback, release, restore = await _held_restore(
        hass, monkeypatch
    )
    admin = MockUser(id="export-restore-admin", name="Export admin", is_owner=True)
    client = await hass_ws_client(hass, await _user_token(hass, admin))
    started = asyncio.Event()
    collect = transfer.async_collect_transfer_snapshot

    async def observed_collect(*args, **kwargs):
        started.set()
        return await collect(*args, **kwargs)

    monkeypatch.setattr(transfer, "async_collect_transfer_snapshot", observed_collect)
    export = asyncio.create_task(
        _transfer_call(client, entry=entry, action="setup_export")
    )
    disposable = None
    try:
        # Prove the actual WebSocket command reached the held ownership boundary.
        from custom_components.extended_openai_conversation_responses.agent_maintenance import (
            get_agent_maintenance_gate,
        )

        gate = get_agent_maintenance_gate(hass, entry.entry_id, subentry.subentry_id)
        async with asyncio.timeout(10):
            while not started.is_set() and gate._waiting_writers == 0:
                await asyncio.sleep(0)
        record(
            stress_trace,
            "setup_export_during_restore",
            durable_rule="rule-beta",
            visible_tool="alpha",
        )
        release.set()
        await asyncio.wait_for(restore, 15)
        response = await asyncio.wait_for(export, 15)
        assert response["success"], response
        document = json.loads(response["result"]["json"])
        config = document["sections"]["configuration"]
        names = [item["spec"]["name"] for item in config[CONF_FUNCTION_TOOLS]]
        reference = document["sections"]["request_rules"]["rules"][0]["action"][
            "actions"
        ][0]["data"]["function"]
        assert names == [reference] == ["beta"], "setup export mixed config and rules"
        disposable = _make_entry("Imported setup", include_ai_task=False)
        await _setup_entry(hass, disposable)
        destination = _conversation_subentry(disposable)
        result = await transfer.async_restore_transfer(
            hass, disposable, destination, document
        )
        assert result["status"] == "restored"
        await hass.async_block_till_done()
        imported_rules = await async_get_request_rules(
            hass, disposable.entry_id, destination.subentry_id
        )
        await transfer._async_validate_request_rule_function_dependencies(
            hass, await imported_rules.async_backup_data(), dict(destination.data)
        )
        loaded = conversation.async_get_agent(hass, disposable.entry_id)
        value = await loaded._async_execute_request_rule_function("beta", {}, None)
        assert "BETA" in str(value)
        normal = await _transfer_call(client, entry=entry, action="setup_export")
        assert (
            normal["success"]
            and normal["result"]["document"]["sections"] == document["sections"]
        )
        record(
            stress_trace,
            "setup_export_restore_recovered",
            imported_tool="beta",
            imported_rule="rule-beta",
        )
        record(
            stress_trace,
            "summary",
            coherent_setup_restore_exports=1,
            layer="genuine-ha",
        )
    finally:
        release.set()
        await asyncio.gather(restore, export, return_exceptions=True)
        await hass.async_block_till_done()
        if disposable is not None:
            await hass.config_entries.async_unload(disposable.entry_id)
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
