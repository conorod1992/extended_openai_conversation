"""Private transfer and Usage lifecycle invariants through genuine HA."""

from dataclasses import asdict
from datetime import timedelta
import logging
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.extended_openai_conversation_responses import backup_transfer
from custom_components.extended_openai_conversation_responses.agent_deletion import (
    async_delete_agent_data,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    UsageRequest,
    UsageRun,
    async_get_durable_usage,
    async_get_usage,
)
from homeassistant.components import conversation
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from tests_real_ha.test_acceptance_lifecycle import _conversation_subentry, _make_entry
from tests_real_ha.test_cross_feature_acceptance import _agent


async def test_expired_export_removes_private_payload_during_idle(hass, freezer):
    agent = await _agent(hass, memory_mode="manual")
    await agent._memory.async_add(
        "audit-owner",
        "Audit backup retained canary: backup-cyan.",
        "calibration",
        "explicit",
    )
    await hass.async_start()
    reply = await backup_transfer.async_backup_transfer_command(
        hass,
        {
            "entry_id": agent.entry.entry_id,
            "subentry_id": agent.subentry.subentry_id,
            "action": "export_start",
            "data": {},
        },
    )
    session = backup_transfer._exports(hass)[reply["session_id"]]
    payload = Path(session.path)
    assert payload.exists()
    freezer.move_to(dt_util.utcnow() + timedelta(hours=1))
    async_fire_time_changed(hass, fire_all=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert session.expires_at < backup_transfer.time.monotonic()
    assert not payload.exists(), (
        "Expired private backup payload must be removed during inactivity"
    )
    assert reply["session_id"] not in backup_transfer._exports(hass)


async def test_removed_entry_erases_private_transfer_payload(hass):
    agent = await _agent(hass, memory_mode="manual")
    await agent._memory.async_add(
        "audit-owner",
        "Audit backup retained canary: backup-cyan.",
        "calibration",
        "explicit",
    )
    reply = await backup_transfer.async_backup_transfer_command(
        hass,
        {
            "entry_id": agent.entry.entry_id,
            "subentry_id": agent.subentry.subentry_id,
            "action": "export_start",
            "data": {},
        },
    )
    session = backup_transfer._exports(hass)[reply["session_id"]]
    payload = Path(session.path)
    import zipfile

    with zipfile.ZipFile(payload) as archive:
        assert b"backup-cyan" in archive.read("backup.json")
    removed = await hass.config_entries.async_remove(agent.entry.entry_id)
    assert removed["require_restart"] is False
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not payload.exists(), (
        "Deleting the assistant must remove its staged private backup payload"
    )
    assert reply["session_id"] not in backup_transfer._exports(hass)


async def test_removed_assistant_disposes_usage_maintenance(hass, freezer, caplog):
    await hass.async_start()
    usage = await async_get_usage(hass, "audit-removed-entry", "audit-removed-agent")
    async with usage.async_run():
        await usage.async_record_request(
            successful=True, usage=RequestUsage(total_tokens=123)
        )
    await hass.async_block_till_done(wait_background_tasks=True)
    if usage._prune_task is not None:
        await usage._prune_task
    await async_delete_agent_data(hass, "audit-removed-entry", "audit-removed-agent")
    with caplog.at_level(logging.ERROR):
        freezer.move_to(dt_util.utcnow() + timedelta(days=2))
        async_fire_time_changed(hass, fire_all=True)
        await hass.async_block_till_done(wait_background_tasks=True)
        if usage._prune_task is not None:
            await usage._prune_task
    assert "Background usage retention maintenance failed" not in caplog.text, (
        "Deleted assistant must not continue daily Usage maintenance"
    )
    assert usage._cancel_retention is None
    assert usage._cancel_stop is None


async def test_public_entry_removal_disposes_usage_timer(hass, freezer, caplog):
    from tests_real_ha.test_cross_feature_acceptance import _agent

    agent = await _agent(hass)
    usage = agent._usage
    await hass.async_start()
    result = await hass.config_entries.async_remove(agent.entry.entry_id)
    assert result["require_restart"] is False
    await hass.async_block_till_done(wait_background_tasks=True)
    with caplog.at_level(logging.ERROR):
        freezer.move_to(dt_util.utcnow() + timedelta(days=2))
        async_fire_time_changed(hass, fire_all=True)
        await hass.async_block_till_done(wait_background_tasks=True)
        if usage._prune_task is not None:
            await usage._prune_task
    assert "Background usage retention maintenance failed" not in caplog.text, (
        "Public entry removal must cancel daily Usage maintenance"
    )
    assert usage._cancel_retention is None
    assert usage._cancel_stop is None


@pytest.mark.parametrize("getter", [async_get_usage, async_get_durable_usage])
@pytest.mark.parametrize(
    "policy,expected_requests,expected_runs",
    [
        (
            {"usage_request_retention_days": 365, "usage_run_retention_days": 365},
            [10, 60, 200],
            [10, 120, 200],
        ),
        (
            {"usage_request_retention_days": 15, "usage_run_retention_days": 15},
            [10],
            [10],
        ),
        ({}, [10], [10]),
        ({"usage_request_retention_days": 0, "usage_run_retention_days": 0}, [], []),
    ],
)
async def test_cold_usage_first_access_honors_saved_policy(
    hass, getter, policy, expected_requests, expected_runs
):
    entry = _make_entry(include_ai_task=False, conversation_options=policy)
    entry.add_to_hass(hass)
    subentry = _conversation_subentry(entry)
    now = dt_util.utcnow()
    prefix = f"{DOMAIN}.usage.{entry.entry_id}.{subentry.subentry_id}"
    details = Store(hass, 2, prefix + ".details", private=True)
    daily = Store(hass, 2, prefix + ".daily")

    def ago(days):
        return (now - timedelta(days=days)).isoformat()

    requests = [
        asdict(
            UsageRequest(
                str(age),
                "seed",
                ago(age),
                subentry.subentry_id,
                "openai",
                "test-model",
                "chat_completions",
                True,
                1,
                total_tokens=123,
            )
        )
        for age in [10, 60, 200]
    ]
    runs = [
        asdict(
            UsageRun(str(age), ago(age), ago(age), 1, subentry.subentry_id, None, None)
        )
        for age in [10, 120, 200]
    ]
    await details.async_save({"requests": requests, "runs": runs})
    await daily.async_save(
        {
            "totals": {"total_tokens": 369, "api_request_count": 3},
            "days": {"2026-01-01": {"total_tokens": 369}},
        }
    )
    # Management/sensors/backups can ask for Usage before a conversation entity exists.
    manager = await getter(hass, entry.entry_id, subentry.subentry_id)
    assert [int(r.request_id) for r in manager.requests] == expected_requests
    assert [int(r.run_id) for r in manager.runs] == expected_runs
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent._usage is manager
    assert [int(r.request_id) for r in manager.requests] == expected_requests
    assert [int(r.run_id) for r in manager.runs] == expected_runs
    assert manager.totals.total_tokens == 369
    assert manager.daily["2026-01-01"]["total_tokens"] == 369
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    stored = await details.async_load()
    assert [int(r["request_id"]) for r in stored["requests"]] == expected_requests
    assert [int(r["run_id"]) for r in stored["runs"]] == expected_runs
    # Drop the shared cache to exercise another actual durable cold load.
    await manager.async_shutdown()
    hass.data[f"{DOMAIN}.usage_managers"].pop((entry.entry_id, subentry.subentry_id))
    loaded = await getter(hass, entry.entry_id, subentry.subentry_id)
    assert [int(r.request_id) for r in loaded.requests] == expected_requests
    assert [int(r.run_id) for r in loaded.runs] == expected_runs
    assert loaded.totals.total_tokens == 369


@pytest.mark.parametrize("action", ["export_start", "import_start"])
async def test_transfer_activity_extends_idle_deadline(hass, freezer, action):
    agent = await _agent(hass, memory_mode="manual")
    await hass.async_start()

    async def command(name, data):
        return await backup_transfer.async_backup_transfer_command(
            hass,
            {
                "entry_id": agent.entry.entry_id,
                "subentry_id": agent.subentry.subentry_id,
                "action": name,
                "data": data,
            },
        )

    started = await command(
        action,
        {"filename": "backup.json", "size": 8} if action == "import_start" else {},
    )
    registry = (
        backup_transfer._imports(hass)
        if action == "import_start"
        else backup_transfer._exports(hass)
    )
    session = registry[started["session_id"]]
    path = Path(session.path)
    freezer.move_to(dt_util.utcnow() + timedelta(minutes=10))
    if action == "export_start":
        await command("export_chunk", {"session_id": session.session_id, "index": 0})
    else:
        import base64

        await command(
            "import_chunk",
            {
                "session_id": session.session_id,
                "index": 0,
                "data": base64.b64encode(b"12345678").decode(),
            },
        )
    freezer.move_to(dt_util.utcnow() + timedelta(minutes=10))
    async_fire_time_changed(hass, fire_all=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert path.exists(), "Active transfers keep their refreshed deadline"
    freezer.move_to(dt_util.utcnow() + timedelta(minutes=6))
    async_fire_time_changed(hass, fire_all=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not path.exists()
    assert session.session_id not in registry
    assert backup_transfer._EXPIRY_CANCEL_KEY not in hass.data


async def test_deletion_erases_only_its_own_pending_transfers(hass):
    first = await _agent(hass, title="First")
    second = await _agent(hass, title="Second")

    async def start(agent):
        return await backup_transfer.async_backup_transfer_command(
            hass,
            {
                "entry_id": agent.entry.entry_id,
                "subentry_id": agent.subentry.subentry_id,
                "action": "import_start",
                "data": {"filename": "backup.json", "size": 8},
            },
        )

    one, two = await start(first), await start(second)
    registry = backup_transfer._imports(hass)
    first_path = Path(registry[one["session_id"]].path)
    second_path = Path(registry[two["session_id"]].path)
    await hass.config_entries.async_remove(first.entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not first_path.exists()
    assert second_path.exists()
    cancelled = await backup_transfer.async_backup_transfer_command(
        hass,
        {
            "entry_id": second.entry.entry_id,
            "subentry_id": second.subentry.subentry_id,
            "action": "import_cancel",
            "data": {"session_id": two["session_id"]},
        },
    )
    assert cancelled == {"cancelled": True}
    assert not second_path.exists()
    assert backup_transfer._EXPIRY_CANCEL_KEY not in hass.data
