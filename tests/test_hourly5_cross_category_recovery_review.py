"""Combined native participants recover after cross-category partial progress."""

from dataclasses import asdict
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    conversation_archive,
    guest_mode,
    knowledge,
    memory,
    model_lifecycle,
    request_rules,
    restore_recovery,
    temporary_memory,
    usage,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
)
from tests.test_restore_initialization_recovery import _prepare


@pytest.mark.parametrize("phase", ["applying", "committed"])
@pytest.mark.parametrize("cold_retry", [False, True])
async def test_cross_category_quarantine_recovery_keeps_selected_generation(
    hass, monkeypatch, phase, cold_retry
):
    durable = restore_recovery._durable_managers
    apply = restore_recovery._apply_prepared
    entry, subentry, selected, journal, _ = await _prepare(
        hass, monkeypatch, phase, "knowledge"
    )
    entry_id, subentry_id = entry.entry_id, subentry.subentry_id
    data = await journal.async_load()
    for label, request_days, run_days in (
        ("target", 365, 30),
        ("rollback", 30, 365),
    ):
        config = data[label]["agent"]["config"]
        config[CONF_USAGE_REQUEST_RETENTION_DAYS] = request_days
        config[CONF_USAGE_RUN_RETENTION_DAYS] = run_days
    await journal.async_save(data)
    _, target, rollback = await restore_recovery._async_load_journal(
        data, entry_id, subentry_id
    )
    selected = target if phase == "committed" else rollback
    entry.subentries = {subentry_id: subentry}
    # Both current policies are valid; the selected journal has authority to
    # restore a different policy and its retained detail as one generation.
    subentry.data = {
        **selected.config,
        CONF_USAGE_REQUEST_RETENTION_DAYS: 0,
        CONF_USAGE_RUN_RETENTION_DAYS: 0,
    }
    hass.config_entries.async_get_entry.return_value = entry
    monkeypatch.setattr(model_lifecycle, "sync_entry_model_lifecycle", lambda *_: None)
    monkeypatch.setattr(restore_recovery, "_durable_managers", durable)
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)

    async def update_configuration(_hass, _entry, current, prepared):
        current.data = dict(prepared.config)
        current.title = prepared.title

    monkeypatch.setattr(restore_recovery, "_update_configuration", update_configuration)
    archive = conversation_archive.HomeAssistantArchiveStorage(
        hass, entry_id, subentry_id
    )
    totals, daily, details = usage._durable_usage_stores(hass, entry_id, subentry_id)
    stores = (
        memory.HomeAssistantMemoryStorage(hass, entry_id, subentry_id)._store,
        temporary_memory._temporary_memory_store(hass, entry_id, subentry_id),
        knowledge.HomeAssistantKnowledgeStorage(hass, entry_id, subentry_id)._store,
        archive._metadata,
        guest_mode.GuestModeManager(hass, entry_id, subentry_id)._store,
        request_rules._request_rule_store(hass, entry_id, subentry_id),
        totals,
        daily,
        details,
    )
    evidence = {}
    for native in stores:
        await native.async_save({})
        path = Path(native.path)
        path.write_text("Rejected " + native.key, encoding="utf-8")
        preserved = path.with_name(path.name + ".corrupt.review")
        path.rename(preserved)
        evidence[preserved] = preserved.read_bytes()
    save = usage._UsageStore.async_save
    with monkeypatch.context() as fault:

        async def fail_final_category(writer, payload):
            if writer.key == details.key:
                raise OSError("Final native category write failed")
            await save(writer, payload)

        fault.setattr(usage._UsageStore, "async_save", fail_final_category)
        with pytest.raises(backup.BackupError, match="could not be recovered"):
            await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert await journal.async_load() is not None
    # Every preceding bootstrap made actual durable progress, while the failed
    # final native store and the authoritative full journal remain preserved.
    assert all(Path(native.path).exists() for native in stores if native is not details)
    assert not Path(details.path).exists()
    if cold_retry:
        hass.data = {}
    assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert await journal.async_load() is None
    assert subentry.data == selected.config
    assert subentry.title == selected.title
    assert {path: path.read_bytes() for path in evidence} == evidence
    managers = await durable(hass, entry_id, subentry_id)
    snapshot = await backup._snapshot_for_restore(managers, subentry)
    for name in (
        "memories",
        "temporary_memories",
        "knowledge",
        "archive_sessions",
        "archive_turns",
        "archive_active",
        "usage_totals",
        "usage_daily",
        "usage_requests",
        "usage_runs",
        "guest_mode_schedule",
        "request_rules",
    ):
        expected = getattr(selected, name)
        if name == "archive_active" and expected is None:
            # Legacy documents omit continuity; current native snapshots expose
            # that intentional lack of bindings as the canonical empty mapping.
            expected = {}
        assert getattr(snapshot, name) == expected, name
    fresh_totals, _, _ = usage._durable_usage_stores(hass, entry_id, subentry_id)
    assert (await fresh_totals.async_load()) == asdict(selected.usage_totals)
    fresh_stores = usage._durable_usage_stores(hass, entry_id, subentry_id)
    fresh_usage = usage.UsageManager(
        *fresh_stores,
        agent_subentry_id=subentry_id,
        **usage._saved_retention(hass, entry_id, subentry_id),
    )
    await fresh_usage.async_initialize()
    usage_snapshot = await fresh_usage.async_backup_data()
    assert usage_snapshot["requests"] == [
        asdict(item) for item in selected.usage_requests
    ]
    assert usage_snapshot["runs"] == [asdict(item) for item in selected.usage_runs]
    assert (
        fresh_usage.request_retention_days
        == selected.config[CONF_USAGE_REQUEST_RETENTION_DAYS]
    )
    assert (
        fresh_usage.run_retention_days == selected.config[CONF_USAGE_RUN_RETENTION_DAYS]
    )
