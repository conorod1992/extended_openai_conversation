"""Validated restore journals replace quarantined required category stores."""

import asyncio
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    guest_mode,
    knowledge,
    memory,
    model_lifecycle,
    request_rules,
    restore_recovery,
    usage,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from tests.restore_participants import participants
from tests.test_request_rules import local_rule
from tests.test_restore_initialization_recovery import _prepare
from tests.test_restore_recovery import _states


async def _quarantined_category(hass, monkeypatch, phase, category):
    entry, subentry, selected, journal, _ = await _prepare(
        hass, monkeypatch, phase, "knowledge"
    )
    entry_id, subentry_id = entry.entry_id, subentry.subentry_id
    data = await journal.async_load()
    for label, count in (("target", 17), ("rollback", 3)):
        data[label]["usage"]["totals"]["conversation_count"] = count
        data[label]["guest_mode"]["schedule"] = {
            "active_from": data["created_at"],
            "active_until": None,
            "source": label + " recovery",
            "updated_at": data["created_at"],
        }
        data[label]["request_rules"]["rules"] = [
            local_rule(label + " retained", ["(on; downstairs)"], "sentence_pattern")
        ]
    await journal.async_save(data)
    _, target, rollback = await restore_recovery._async_load_journal(
        data, entry_id, subentry_id
    )
    selected = target if phase == "committed" else rollback
    await knowledge.HomeAssistantKnowledgeStorage(
        hass, entry_id, subentry_id
    ).async_save({"sources": [asdict(record) for record in selected.knowledge]})
    monkeypatch.setattr(model_lifecycle, "sync_entry_model_lifecycle", lambda *_: None)
    if category == "guest_mode":
        native = guest_mode.GuestModeManager(hass, entry_id, subentry_id)._store
        getter = guest_mode.async_get_guest_mode
        field = "guest_mode"
    elif category == "request_rules":
        native = request_rules.RequestRuleStore(
            hass,
            request_rules.STORAGE_VERSION,
            f"{request_rules.STORAGE_KEY_PREFIX}.{entry_id}.{subentry_id}",
        ).bind_agent(entry_id, subentry_id)
        getter = request_rules.async_get_request_rules
        field = "request_rules"
    else:
        suffix = {
            "usage_totals": "",
            "usage_daily": ".daily",
            "usage_details": ".details",
        }[category]
        native = usage._UsageStore(
            hass,
            1 if category == "usage_totals" else usage.STORAGE_VERSION,
            f"{usage.STORAGE_KEY_PREFIX}.{entry_id}.{subentry_id}{suffix}",
            atomic_writes=True,
        ).bind_agent(entry_id, subentry_id)
        getter = usage.async_get_durable_usage
        field = "usage"
    await native.async_save({})
    path = Path(native.path)
    path.write_text("Preserved bad JSON", encoding="utf-8")
    evidence = path.with_name(path.name + ".corrupt.review")
    path.rename(evidence)

    async def managers(hass, entry_id, subentry_id):
        return participants(**{field: await getter(hass, entry_id, subentry_id)})

    async def apply(managers, prepared):
        manager = getattr(managers, field)
        if field == "guest_mode":
            await manager.async_replace_backup(prepared.guest_mode_schedule)
        elif field == "request_rules":
            await manager.async_replace_backup(prepared.request_rules)
        else:
            await manager.async_replace_backup(
                prepared.usage_totals,
                prepared.usage_daily,
                prepared.usage_requests,
                prepared.usage_runs,
            )

    monkeypatch.setattr(restore_recovery, "_durable_managers", managers)
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)
    return entry, subentry, selected, journal, evidence, native, field


@pytest.mark.parametrize("phase", ["applying", "committed"])
@pytest.mark.parametrize(
    "category",
    ["guest_mode", "request_rules", "usage_totals", "usage_daily", "usage_details"],
)
async def test_valid_journal_replaces_quarantined_required_category(
    hass, monkeypatch, phase, category
):
    (
        entry,
        subentry,
        selected,
        journal,
        evidence,
        _,
        field,
    ) = await _quarantined_category(hass, monkeypatch, phase, category)
    entry_id, subentry_id = entry.entry_id, subentry.subentry_id
    assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert evidence.read_text(encoding="utf-8") == "Preserved bad JSON"
    assert await journal.async_load() is None
    if field == "guest_mode":
        fresh = guest_mode.GuestModeManager(hass, entry_id, subentry_id)
        expected = {"schedule": asdict(selected.guest_mode_schedule)}
    elif field == "request_rules":
        fresh = request_rules.RequestRules(
            request_rules._request_rule_store(hass, entry_id, subentry_id)
        )
        expected = selected.request_rules
    else:
        fresh = usage.UsageManager(
            *usage._durable_usage_stores(hass, entry_id, subentry_id),
            agent_subentry_id=subentry_id,
            request_retention_days=10_000,
            run_retention_days=10_000,
        )
        expected = {
            "totals": asdict(selected.usage_totals),
            "daily": selected.usage_daily,
            "requests": [asdict(record) for record in selected.usage_requests],
            "runs": [asdict(record) for record in selected.usage_runs],
        }
    await fresh.async_initialize()
    persisted = await fresh.async_backup_data()
    assert {key: persisted[key] for key in expected} == expected
    if field == "request_rules":
        assert fresh._diagnostics
        assert persisted["rules"][0]["enabled"] is True


@pytest.mark.parametrize(
    "category",
    ["guest_mode", "request_rules", "usage_totals", "usage_daily", "usage_details"],
)
@pytest.mark.parametrize("reached_disk", [False, True])
@pytest.mark.parametrize("cold_reload", [False, True])
async def test_failed_category_bootstrap_retains_journal_and_retries_every_store(
    hass, monkeypatch, category, reached_disk, cold_reload
):
    entry, subentry, _, journal, evidence, native, _ = await _quarantined_category(
        hass, monkeypatch, "committed", category
    )
    save = type(native).async_save
    with monkeypatch.context() as fault:

        async def fail(writer, payload):
            if writer.key == native.key:
                if reached_disk:
                    await save(writer, payload)
                raise OSError("Native staging acknowledgement failed")
            return await save(writer, payload)

        fault.setattr(type(native), "async_save", fail)
        with pytest.raises(backup.BackupError, match="could not be recovered"):
            await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert await journal.async_load() is not None
    assert evidence.read_text(encoding="utf-8") == "Preserved bad JSON"
    if cold_reload:
        # Simulate a new process's manager/gate registry, retaining native files.
        hass.data = {}
    assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert await journal.async_load() is None
    assert Path(native.path).exists(), (
        "Recovery must also settle the failed native store"
    )


async def test_canonical_daily_keeps_unread_legacy_mirror_best_effort(
    hass, monkeypatch
):
    (
        entry,
        subentry,
        selected,
        journal,
        evidence,
        native,
        _,
    ) = await _quarantined_category(hass, monkeypatch, "committed", "usage_totals")
    entry_id, subentry_id = entry.entry_id, subentry.subentry_id
    await native.async_save(asdict(selected.usage_totals))
    _, daily, _ = usage._durable_usage_stores(hass, entry_id, subentry_id)
    await daily.async_save(
        {"totals": asdict(selected.usage_totals), "days": selected.usage_daily}
    )
    manager = await usage.async_get_durable_usage(hass, entry_id, subentry_id)
    mirror_read = AsyncMock(side_effect=OSError("Legacy mirror read unavailable"))
    monkeypatch.setattr(manager._storage, "async_load", mirror_read)
    assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert await journal.async_load() is None
    mirror_read.assert_not_awaited()
    assert evidence.read_text(encoding="utf-8") == "Preserved bad JSON"


def _stage_call(hass, category, *, invalid=False):
    if category == "guest_mode":
        schedule = guest_mode.GuestModeSchedule(
            active_from="bad" if invalid else "2026-11-02T05:00:00+00:00",
            active_until=None,
            source="review",
        )
        return guest_mode.async_stage_guest_mode_journal_recovery(
            hass, "guard-entry", "guard-agent", schedule
        )
    if category == "request_rules":
        return request_rules.async_stage_request_rules_journal_recovery(
            hass, "guard-entry", "guard-agent", {"rules": [None]} if invalid else {}
        )
    return usage.async_stage_usage_journal_recovery(
        hass,
        "guard-entry",
        "guard-agent",
        usage.UsageTotals(conversation_count=-1 if invalid else 0),
        {},
        [],
        [],
    )


@pytest.mark.parametrize("category", ["guest_mode", "request_rules", "usage"])
async def test_category_staging_requires_exclusive_quarantine(hass, category):
    with pytest.raises(RuntimeError, match="exclusive quarantined maintenance"):
        await _stage_call(hass, category)
    gate = get_agent_maintenance_gate(hass, "guard-entry", "guard-agent")
    async with gate.exclusive():
        with pytest.raises(RuntimeError, match="exclusive quarantined maintenance"):
            await _stage_call(hass, category)


@pytest.mark.parametrize("category", ["guest_mode", "request_rules", "usage"])
async def test_invalid_category_candidate_cannot_reach_any_native_save(
    hass, monkeypatch, category
):
    save = AsyncMock()
    for kind in (
        guest_mode.PropagatingWriteStore,
        request_rules.RequestRuleStore,
        usage._UsageStore,
    ):
        monkeypatch.setattr(kind, "async_save", save)
    gate = get_agent_maintenance_gate(hass, "guard-entry", "guard-agent")
    async with gate.exclusive():
        gate.recovery_required = True
        try:
            with gate.recovery_work(), pytest.raises(ValueError):
                await _stage_call(hass, category, invalid=True)
        finally:
            gate.recovery_required = False
    save.assert_not_awaited()


@pytest.mark.parametrize("category", ["memory", "guest_mode", "request_rules", "usage"])
@pytest.mark.parametrize(
    "kind", [OSError, RuntimeError, ValueError, asyncio.CancelledError]
)
async def test_unrelated_category_getter_errors_never_authorize_bootstrap(
    hass, monkeypatch, category, kind
):
    module, getter = {
        "memory": (memory, "async_get_memory"),
        "guest_mode": (guest_mode, "async_get_guest_mode"),
        "request_rules": (request_rules, "async_get_request_rules"),
        "usage": (usage, "async_get_durable_usage"),
    }[category]
    monkeypatch.setattr(
        module, getter, AsyncMock(side_effect=kind("Unrelated failure"))
    )
    monkeypatch.setattr(
        restore_recovery,
        "_durable_managers",
        AsyncMock(side_effect=ValueError("Initial participant unavailable")),
    )
    saves = []
    for module, name in (
        (memory.HomeAssistantMemoryStorage, "async_save"),
        (guest_mode, "async_stage_guest_mode_journal_recovery"),
        (request_rules, "async_stage_request_rules_journal_recovery"),
        (usage, "async_stage_usage_journal_recovery"),
    ):
        save = AsyncMock()
        monkeypatch.setattr(module, name, save)
        saves.append(save)
    target, _ = _states()
    gate = get_agent_maintenance_gate(hass, "error-entry", "error-agent")
    async with gate.exclusive():
        gate.recovery_required = True
        try:
            with gate.recovery_work(), pytest.raises(kind):
                await restore_recovery._recovery_managers(
                    hass, "error-entry", "error-agent", target
                )
        finally:
            gate.recovery_required = False
    for save in saves:
        save.assert_not_awaited()
