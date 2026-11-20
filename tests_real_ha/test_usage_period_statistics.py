"""Sensor events, schedule boundaries and usage totals in genuine Home Assistant."""

from copy import deepcopy
from datetime import timedelta
from functools import partial
import json

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
    do_adhoc_statistics,
    statistics_during_period,
)

from custom_components.extended_openai_conversation_responses.agent_config import (
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.sensor import UsageSensor
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    async_get_usage,
)
from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from tests.ha_store_test_helpers import invalidate_store_cache
from tests_real_ha.test_acceptance_lifecycle import _registry_entries
from tests_real_ha.test_cross_feature_acceptance import _agent
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


async def _sensor_agent(hass, **options):
    """Keep native lifecycle coverage independent of newer schema-only tests."""
    return await _agent(
        hass,
        **normalize_agent_config(
            {
                "chat_model": "gpt-5.6",
                "api_mode": "chat_completions",
                "reasoning_effort": "none",
                "functions": [],
                **options,
            }
        ),
    )


async def _enabled_usage_entities(hass, agent):
    """Enable the actual diagnostic entities and let HA recreate their platform."""
    suffixes = ("usage", "usage_today", "usage_month", "last_response_usage")
    ids = {
        suffix: next(
            row.entity_id
            for row in _registry_entries(hass, agent.entry)
            if row.unique_id == f"{agent.subentry.subentry_id}_{suffix}"
        )
        for suffix in suffixes
    }
    registry = er.async_get(hass)
    for entity_id in ids.values():
        registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(agent.entry.entry_id)
    await hass.async_block_till_done()
    from homeassistant.components import sensor

    entities = {
        entity.entity_id: entity for entity in hass.data[sensor.DATA_COMPONENT].entities
    }
    result = {suffix: entities[entity_id] for suffix, entity_id in ids.items()}
    assert all(not result[key].should_poll for key in suffixes[:3])
    assert result["last_response_usage"].should_poll
    return result


async def _latest_usage_statistics(hass, freezer, start, minutes, ids):
    window = start + timedelta(minutes=minutes)
    await async_wait_recording_done(hass)
    freezer.move_to(window + timedelta(minutes=5, seconds=1))
    await async_wait_recording_done(hass)
    do_adhoc_statistics(hass, start=window)
    await async_wait_recording_done(hass)
    rows = await hass.async_add_executor_job(
        partial(
            statistics_during_period, hass, start, statistic_ids=set(ids.values()),
            period="5minute", types={"state", "sum", "last_reset"},
        )
    )
    return {suffix: rows[entity_id][-1] for suffix, entity_id in ids.items()}


@pytest.mark.parametrize("window", ["future", "indefinite", "active", "expired"])
async def test_guest_sensor_follows_scheduled_boundaries_and_unload_without_polling(
    hass, freezer, window
):
    from dataclasses import asdict

    from custom_components.extended_openai_conversation_responses import guest_mode
    from homeassistant.core import callback
    from homeassistant.helpers.event import async_track_state_change_event

    base = dt_util.parse_datetime("2026-11-16T12:00:00+00:00")
    freezer.move_to(base)
    agent = await _sensor_agent(hass, memory_mode="off")
    entry = agent.entry
    key = entry.entry_id, agent.subentry.subentry_id
    old = agent._guest_mode
    schedule = guest_mode.GuestModeSchedule(
        (
            base + timedelta(seconds=5 if window in {"future", "indefinite"} else -5)
        ).isoformat(),
        None
        if window == "indefinite"
        else (base + timedelta(seconds=-1 if window == "expired" else 10)).isoformat(),
    )
    await old.async_replace_backup(schedule)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not old._listeners
    assert old._cancel_transition is None
    # Recreate the manager as on restart, loading its actual persisted Store.
    fresh = guest_mode.GuestModeManager(hass, *key)
    await fresh.async_initialize()
    assert asdict(fresh.schedule) == asdict(schedule)
    hass.data[guest_mode._MANAGERS][key] = fresh
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = next(
        row.entity_id
        for row in _registry_entries(hass, entry)
        if row.unique_id == f"{key[1]}_guest_mode"
    )
    from homeassistant.components import sensor

    entity = next(
        entity
        for entity in hass.data[sensor.DATA_COMPONENT].entities
        if entity.entity_id == entity_id
    )
    assert not entity.should_poll
    assert entity._guest_mode is fresh
    assert hass.states.get(entity_id).state == (
        "scheduled"
        if window in {"future", "indefinite"}
        else "active"
        if window == "active"
        else "inactive"
    )
    changes = []

    @callback
    def changed(event):
        changes.append(event.data["new_state"].state)

    remove = async_track_state_change_event(hass, [entity_id], changed)
    await hass.async_start()
    try:
        for seconds in (4, 5, 9, 10, 15):
            now = base + timedelta(seconds=seconds)
            freezer.move_to(now)
            async_fire_time_changed(hass, now)
            await hass.async_block_till_done()
            expected = (
                "inactive"
                if window == "expired" or (seconds >= 10 and window != "indefinite")
                else "active_indefinitely"
                if seconds >= 5 and window == "indefinite"
                else "active"
                if window == "active" or seconds >= 5
                else "scheduled"
            )
            assert hass.states.get(entity_id).state == expected
        assert (
            changes
            == {
                "future": ["active", "inactive"],
                "indefinite": ["active_indefinitely"],
                "active": ["inactive"],
                "expired": [],
            }[window]
        )
    finally:
        remove()
        assert await hass.config_entries.async_unload(entry.entry_id)
    assert not fresh._listeners
    assert fresh._cancel_transition is None
    assert fresh._transition_token is None


async def test_usage_counter_and_restored_aggregates_publish_without_polling(
    hass, freezer
):
    freezer.move_to("2026-11-16T12:00:00+00:00")
    agent = await _sensor_agent(hass, memory_mode="off")
    entities = await _enabled_usage_entities(hass, agent)
    usage = entities["usage"]._usage
    await usage.async_record_conversation()
    assert (
        hass.states.get(entities["usage"].entity_id).attributes["conversation_count"]
        == 1
    )
    async with usage.async_run():
        await usage.async_record_request(
            successful=True, usage=RequestUsage(total_tokens=321)
        )
    await hass.async_block_till_done()
    assert all(
        hass.states.get(entity.entity_id).state == "321" for entity in entities.values()
    )
    checkpoint = await usage.async_backup_data()
    await usage.async_record_request(
        successful=True, usage=RequestUsage(total_tokens=100)
    )
    assert hass.states.get(entities["usage"].entity_id).state == "421"
    await usage.async_replace_backup(
        *usage.validate_backup_data(checkpoint, agent.subentry.subentry_id)
    )
    await hass.async_block_till_done()
    # The same live entities must update; a reload/poll cannot supply the correction.
    assert all(entity._usage is usage for entity in entities.values())
    assert all(
        hass.states.get(entity.entity_id).state == "321" for entity in entities.values()
    )
    assert (
        hass.states.get(entities["usage"].entity_id).attributes["conversation_count"]
        == 2
    )
    assert await hass.config_entries.async_unload(agent.entry.entry_id)
    assert not usage._listeners


@pytest.mark.parametrize("combined", [False, True], ids=["separate", "combined"])
@pytest.mark.usefixtures("real_store_io")
async def test_readable_legacy_usage_starts_durably_during_a_write_outage(
    hass, freezer, monkeypatch, combined
):
    from custom_components.extended_openai_conversation_responses import usage
    from homeassistant.components import conversation
    from tests.test_usage import _coverage_backup
    from tests_real_ha.test_acceptance_lifecycle import (
        _conversation_subentry,
        _make_entry,
        _setup_entry,
    )
    from tests_real_ha.test_cross_feature_acceptance import _provider, _say, _speech
    from tests_real_ha.test_entry_point_contract_matrix import _management

    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-09-13T12:00:00+00:00")
    entry = _make_entry(
        include_ai_task=False,
        conversation_options={
            "api_mode": "chat_completions",
            "chat_model": "gpt-5.6",
            "reasoning_effort": "none",
            "functions": [],
        },
    )
    subentry = _conversation_subentry(entry)
    totals_store, daily_store, detail_store = usage._durable_usage_stores(
        hass, entry.entry_id, subentry.subentry_id
    )
    saved = _coverage_backup()
    date = "2026-09-13"
    for key in ("run_total_tokens", "run_request_count", "unattributed_run_count"):
        del saved["daily"][date][key]
    legacy = {"days": saved["daily"]}
    if combined:
        legacy["totals"] = saved["totals"]
    await totals_store.async_save(saved["totals"])
    await daily_store.async_save(legacy)
    await detail_store.async_save({"requests": saved["requests"], "runs": saved["runs"]})

    normal_save = usage._UsageStore.async_save
    attempted_writes = []

    async def reject_daily_save(store, data):
        if store.key == daily_store.key:
            attempted_writes.append(store.key)
            raise OSError("aggregate store is temporarily write-protected")
        await normal_save(store, data)

    with monkeypatch.context() as read_only:
        read_only.setattr(usage._UsageStore, "async_save", reject_daily_save)
        await _setup_entry(hass, entry)
        agent = conversation.async_get_agent(hass, entry.entry_id)
        assert agent._usage.persistence_status()["mode"] == "durable"
        command = await _management(hass, agent)
        summary = await command("summary", section="usage")
        daily = await command("daily", section="usage", start_date=date, end_date=date)
        assert summary["lifetime"]["total_tokens"] == 5
        assert summary["lifetime"]["conversation_count"] == 1
        assert daily["days"][0]["total_tokens"] == 5
        assert daily["days"][0]["average_tokens_per_completed_run"] is None
        assert not attempted_writes
        assert await daily_store.async_load() == legacy

    _provider(monkeypatch, agent, ["The assistant still works."])
    assert _speech(await _say(hass, agent, "What about the last turn?")) == (
        "The assistant still works."
    )
    await agent._usage.async_shutdown()
    summary = await command("summary", section="usage")
    invalidate_store_cache(daily_store)
    aggregate = await daily_store.async_load()
    assert summary["lifetime"]["conversation_count"] == 2
    assert aggregate["totals"]["conversation_count"] == 2
    assert aggregate["days"][date]["run_count"] == 2
    assert "unattributed_run_count" not in aggregate["days"][date]
    assert aggregate["run_attribution"][date]["unattributed_run_count"] == 1
    assert summary["today"]["average_tokens_per_completed_run"] is None


@pytest.mark.parametrize("restore", ["none", "dated", "legacy"])
@pytest.mark.usefixtures("real_store_io")
async def test_local_run_counts_use_recorded_days_after_timezone_change(
    hass, freezer, monkeypatch, restore
):
    from custom_components.extended_openai_conversation_responses import transfer
    from tests_real_ha.test_cross_feature_acceptance import (
        _action,
        _provider,
        _rule,
        _say,
        _speech,
    )
    from tests_real_ha.test_entry_point_contract_matrix import _management

    freezer.move_to("2026-09-13T02:00:00+00:00")
    await hass.config.async_set_time_zone("America/New_York")
    agent = await _sensor_agent(hass, memory_mode="off")
    command = await _management(hass, agent)

    async def turn_off(_call):
        pass

    hass.services.async_register("light", "turn_off", turn_off)
    hass.states.async_set("light.usage_calendar", "on")
    await agent._request_rules.async_create(
        _rule(
            "local_action",
            {
                "actions": [_action("light.usage_calendar")],
                "success_response": "Turned it off.",
                "failure_response": "Failed.",
            },
        )
    )
    calls = _provider(monkeypatch, agent, [])
    assert _speech(await _say(hass, agent, "good night")) == "Turned it off."
    assert not calls

    async def report(start, end):
        return await command("daily", section="usage", start_date=start, end_date=end)

    before = await report("2026-09-12", "2026-09-12")
    assert before["days"][0]["run_count"] == before["local_run_summary"]["count"] == 1
    saved = await agent._usage.async_backup_data()
    assert saved["runs"][0]["accounting_date"] == "2026-09-12"
    if restore == "legacy":
        del saved["runs"][0]["accounting_date"]
        for key in ("run_total_tokens", "run_request_count", "unattributed_run_count"):
            del saved["daily"]["2026-09-12"][key]

    freezer.move_to("2026-09-19T12:00:00+00:00")
    await hass.config.async_set_time_zone("UTC")
    if restore != "none":
        entry, subentry = agent.entry, agent.subentry
        document = transfer._new_transfer_document(entry, subentry, "custom")
        document["sections"][transfer.SECTION_USAGE] = saved
        result = await transfer.async_restore_transfer(hass, entry, subentry, document)
        assert result["status"] == "restored"

    selected = await report("2026-09-13", "2026-09-19")
    original_day = await report("2026-09-12", "2026-09-12")
    assert selected["days"] == []
    assert original_day["days"][0]["run_count"] == 1
    if restore == "legacy":
        assert selected["local_run_summary"] is None
        assert original_day["local_run_summary"] is None
    else:
        assert selected["local_run_summary"]["count"] == 0
        assert original_day["local_run_summary"]["count"] == 1
    whole_period = await report("2026-09-12", "2026-09-19")
    assert whole_period["local_run_summary"]["count"] == 1

    await command("clear_details", section="usage", confirm=True)
    after_clear = await report("2026-09-12", "2026-09-12")
    assert after_clear["days"][0]["run_count"] == 1
    assert after_clear["local_run_summary"]["count"] == 0


async def test_usage_sensors_rebind_from_volatile_to_durable_without_polling(hass):
    from custom_components.extended_openai_conversation_responses.sensor import (
        rebind_usage_sensors,
    )
    from custom_components.extended_openai_conversation_responses.usage import (
        UsageManager,
        _VolatileUsageStorage,
    )

    agent = await _sensor_agent(hass, memory_mode="off")
    entities = await _enabled_usage_entities(hass, agent)
    durable = entities["usage"]._usage
    volatile = UsageManager(
        _VolatileUsageStorage(), _VolatileUsageStorage(), _VolatileUsageStorage()
    )
    await volatile.async_initialize()
    async with volatile.async_run():
        await volatile.async_record_request(
            successful=True, usage=RequestUsage(total_tokens=10)
        )
    assert (
        rebind_usage_sensors(hass, agent.subentry.subentry_id, durable, volatile) == 4
    )
    assert hass.states.get(entities["last_response_usage"].entity_id).state == "10"
    assert all(
        hass.states.get(entities[key].entity_id).state == "unavailable"
        for key in ("usage", "usage_today", "usage_month")
    )
    async with durable.async_run():
        await durable.async_record_request(
            successful=True, usage=RequestUsage(total_tokens=100)
        )
    assert (
        rebind_usage_sensors(hass, agent.subentry.subentry_id, volatile, durable) == 4
    )
    assert not volatile._listeners
    assert all(
        hass.states.get(entity.entity_id).state == "100" for entity in entities.values()
    )
    await volatile.async_record_request(
        successful=True, usage=RequestUsage(total_tokens=7)
    )
    assert hass.states.get(entities["usage"].entity_id).state == "100"
    await durable.async_record_request(
        successful=True, usage=RequestUsage(total_tokens=5)
    )
    assert all(
        hass.states.get(entities[key].entity_id).state == "105"
        for key in ("usage", "usage_today", "usage_month")
    )
    assert await hass.config_entries.async_unload(agent.entry.entry_id)
    assert not durable._listeners


async def test_last_response_poll_hides_expired_detail_without_new_usage(hass, freezer):
    from homeassistant.const import STATE_UNKNOWN

    base = dt_util.parse_datetime("2026-11-16T12:00:00+00:00")
    freezer.move_to(base)
    agent = await _sensor_agent(hass, memory_mode="off", usage_run_retention_days=7)
    entities = await _enabled_usage_entities(hass, agent)
    usage = entities["usage"]._usage
    assert usage.run_retention_days == 7
    async with usage.async_run():
        await usage.async_record_request(
            successful=True, usage=RequestUsage(total_tokens=10)
        )
    await hass.async_block_till_done()
    # Isolate dynamic read expiry from daily pruning: the run stays in storage.
    usage._cancel_retention()
    usage._cancel_retention = None
    await hass.async_start()
    await hass.async_block_till_done()
    entity = entities["last_response_usage"]
    assert hass.states.get(entity.entity_id).state == "10"
    for extra, expected in ((0, "10"), (31, STATE_UNKNOWN)):
        now = base + timedelta(days=7, seconds=extra)
        freezer.move_to(now)
        async_fire_time_changed(hass, now)
        await hass.async_block_till_done()
        assert hass.states.get(entity.entity_id).state == expected, (
            dt_util.utcnow(),
            usage.run_retention_days,
            usage.latest_run,
            entity.should_poll,
            entity.platform.scan_interval,
        )
    assert len(usage.runs) == 1
    assert usage.latest_run is None
    assert entity.native_value is None
    assert entity.extra_state_attributes == {}
    assert usage.totals.total_tokens == 10
    assert await hass.config_entries.async_unload(agent.entry.entry_id)


@pytest.mark.parametrize("checkpoint_total", [0, 503])
@pytest.mark.parametrize("reload_before_restore", [False, True])
async def test_cumulative_restore_corrects_recorder_sum_without_new_meter_cycle(
    hass, freezer, checkpoint_total, reload_before_restore
):
    """Actual backup replacement/reload must not manufacture accumulated usage."""
    await hass.config.async_set_time_zone("UTC")
    start = dt_util.parse_datetime("2026-11-06T12:00:00+00:00")
    freezer.move_to(start)
    agent = await _sensor_agent(hass, memory_mode="off")
    entry, subentry = agent.entry, agent.subentry
    entity_id = next(
        row.entity_id
        for row in _registry_entries(hass, entry)
        if row.unique_id == f"{subentry.subentry_id}_usage"
    )
    er.async_get(hass).async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await hass.async_start()
    usage = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)
    slot = 0

    async def compile_sum(expected):
        nonlocal slot
        window = start + timedelta(minutes=5 * slot)
        slot += 1
        # Finish Recorder startup and persist this window's states before moving
        # its clock. Otherwise CompileMissingStatisticsTask can seal an empty
        # window before the queued sensor state events have been recorded.
        await async_wait_recording_done(hass)
        freezer.move_to(window + timedelta(minutes=5, seconds=1))
        await async_wait_recording_done(hass)
        do_adhoc_statistics(hass, start=window)
        await async_wait_recording_done(hass)
        rows = await hass.async_add_executor_job(
            partial(
                statistics_during_period,
                hass,
                start,
                statistic_ids={entity_id},
                period="5minute",
                types={"state", "sum", "last_reset"},
            )
        )
        latest = rows[entity_id][-1]
        assert latest["state"] == expected, latest
        assert latest["sum"] == expected, (
            f"Cumulative usage sum {latest['sum']} != restored counter {expected} at window {slot - 1}"
        )

    assert hass.states.get(entity_id).state == "0"
    await compile_sum(0)
    if checkpoint_total:
        await usage.async_record_request(
            successful=True, usage=RequestUsage(total_tokens=checkpoint_total)
        )
    # Exercise the real serialized Usage backup codec, not a fabricated statistic.
    checkpoint = json.dumps(await usage.async_backup_data())
    await usage.async_record_request(
        successful=True, usage=RequestUsage(total_tokens=520 - checkpoint_total)
    )
    await compile_sum(520)
    for _ in range(2):
        if reload_before_restore:
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            usage = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)
        prepared = usage.validate_backup_data(
            json.loads(checkpoint), subentry.subentry_id
        )
        await usage.async_replace_backup(*prepared)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        usage = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)
        assert usage.totals.total_tokens == checkpoint_total
        await compile_sum(checkpoint_total)
        await usage.async_record_request(
            successful=True, usage=RequestUsage(total_tokens=23)
        )
        await compile_sum(checkpoint_total + 23)


async def test_cumulative_restore_oracle_detects_increasing_meter_fault(
    hass, freezer, monkeypatch
):
    """Calibrate the real recorder oracle against the former sensor declaration."""
    monkeypatch.setattr(
        UsageSensor, "_attr_state_class", SensorStateClass.TOTAL_INCREASING
    )
    with pytest.raises(
        AssertionError,
        match=r"(?m)^Cumulative usage sum .*restored counter 0 at window 2$",
    ):
        await test_cumulative_restore_corrects_recorder_sum_without_new_meter_cycle(
            hass, freezer, 0, False
        )


async def test_guest_sensor_recovers_boundary_timer_after_uncertain_save(
    hass, freezer, monkeypatch
):
    from unittest.mock import AsyncMock

    from custom_components.extended_openai_conversation_responses.guest_mode import (
        GuestModeSchedule,
    )
    from homeassistant.const import STATE_UNAVAILABLE

    base = dt_util.parse_datetime("2026-11-16T12:00:00+00:00")
    freezer.move_to(base)
    agent = await _sensor_agent(hass, memory_mode="off")
    manager = agent._guest_mode
    entity_id = next(
        row.entity_id
        for row in _registry_entries(hass, agent.entry)
        if row.unique_id == f"{agent.subentry.subentry_id}_guest_mode"
    )
    await manager.async_replace_backup(
        GuestModeSchedule(
            (base + timedelta(seconds=5)).isoformat(),
            (base + timedelta(seconds=10)).isoformat(),
        )
    )
    assert hass.states.get(entity_id).state == "scheduled"
    with monkeypatch.context() as failing:
        failing.setattr(
            manager._store, "async_save", AsyncMock(side_effect=OSError("write failed"))
        )
        failing.setattr(
            manager._store,
            "async_load",
            AsyncMock(side_effect=OSError("readback failed")),
        )
        with pytest.raises(OSError, match="write failed"):
            await manager.async_disable_trusted()
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
        assert manager._cancel_transition is None
        now = base + timedelta(seconds=6)
        freezer.move_to(now)
        async_fire_time_changed(hass, now)
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    await manager.async_initialize()
    assert hass.states.get(entity_id).state == "active"
    assert manager._cancel_transition is not None
    now = base + timedelta(seconds=10)
    freezer.move_to(now)
    async_fire_time_changed(hass, now)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "inactive"
    assert manager._cancel_transition is None
    assert await hass.config_entries.async_unload(agent.entry.entry_id)


async def test_period_sensors_refresh_and_rearm_after_timezone_change(hass, freezer, monkeypatch):
    from unittest.mock import Mock
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-11-01T00:30:00+00:00")
    agent = await _sensor_agent(hass, memory_mode="off")
    entities = await _enabled_usage_entities(hass, agent)
    usage = entities["usage"]._usage
    await usage.async_record_request(successful=True, usage=RequestUsage(total_tokens=42))
    await hass.async_block_till_done()
    for key in ("usage_today", "usage_month"):
        assert hass.states.get(entities[key].entity_id).state == "42"
        assert hass.states.get(entities[key].entity_id).attributes["period_start"] == "2026-11-01"
    await hass.config.async_update(time_zone="America/Los_Angeles")
    await hass.async_block_till_done()
    # The current local date/month changed, without new usage or a poll.
    for key in ("usage_today", "usage_month"):
        assert entities[key].native_value == 0
        assert hass.states.get(entities[key].entity_id).state == "0"
    assert hass.states.get(entities["usage"].entity_id).state == "42"
    # November midnight in LA occurs before the obsolete UTC midnight timer.
    boundary = dt_util.parse_datetime("2026-11-01T07:00:01+00:00")
    freezer.move_to(boundary)
    async_fire_time_changed(hass, boundary)
    await hass.async_block_till_done()
    for key in ("usage_today", "usage_month"):
        assert hass.states.get(entities[key].entity_id).state == "42"
    assert await hass.config_entries.async_unload(agent.entry.entry_id)
    observers = []
    for key in ("usage_today", "usage_month"):
        assert entities[key]._cancel_period_rollover is None
        observer = Mock()
        monkeypatch.setattr(entities[key], "async_write_ha_state", observer)
        observers.append(observer)
    await hass.config.async_update(time_zone="UTC")
    await hass.async_block_till_done()
    assert all(not observer.called for observer in observers)


@pytest.mark.parametrize("marked", [False, True])
async def test_period_sensor_cutover_preserves_existing_statistics(hass, freezer, marked):
    """The same entities publish raw history after the last accumulated statistic."""
    from homeassistant.components.recorder import history, statistics

    await hass.config.async_set_time_zone("UTC")
    start = dt_util.parse_datetime("2026-06-15T12:00:00+00:00")
    freezer.move_to(start)
    agent = await _sensor_agent(hass, memory_mode="off")
    ids = {
        name: next(
            row.entity_id for row in _registry_entries(hass, agent.entry)
            if row.unique_id == f"{agent.subentry.subentry_id}_{name}"
        )
        for name in ("usage_today", "usage_month")
    }
    manager = await async_get_usage(hass, agent.entry.entry_id, agent.subentry.subentry_id)
    await hass.async_start()
    legacy = {"state_class": "total", "unit_of_measurement": "tokens"}
    if marked:
        legacy["last_reset"] = start.replace(hour=0).isoformat()
    for entity_id in ids.values():
        hass.states.async_set(entity_id, "0", legacy)
    await _latest_usage_statistics(hass, freezer, start, 0, ids)
    await manager.async_record_request(successful=True, usage=RequestUsage(total_tokens=85))
    for entity_id in ids.values():
        hass.states.async_set(entity_id, "85", legacy)
    before = await _latest_usage_statistics(hass, freezer, start, 5, ids)
    assert {row["sum"] for row in before.values()} == {85}
    for entity_id in ids.values():
        hass.states.async_remove(entity_id)
    await async_wait_recording_done(hass)

    entities = await _enabled_usage_entities(hass, agent)
    assert {name: entities[name].entity_id for name in ids} == ids
    manager = entities["usage"]._usage
    assert {hass.states.get(entity_id).state for entity_id in ids.values()} == {"85"}
    await manager.async_record_request(successful=True, usage=RequestUsage(total_tokens=20))
    assert await _latest_usage_statistics(hass, freezer, start, 10, ids) == before
    issues = await hass.async_add_executor_job(statistics.validate_statistics, hass)
    for entity_id in ids.values():
        current = hass.states.get(entity_id)
        assert current.state == "105"
        assert "state_class" not in current.attributes and "last_reset" not in current.attributes
        assert entity_id in issues
        raw = await hass.async_add_executor_job(history.get_last_state_changes, hass, 5, entity_id)
        assert raw[entity_id][-1].state == "105"


async def test_usage_fallback_does_not_rewrite_recorder_history(hass, freezer, monkeypatch):
    from custom_components.extended_openai_conversation_responses import backup, transfer, usage

    start = dt_util.parse_datetime("2026-06-30T23:40:00+00:00")
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(start)
    agent = await _sensor_agent(hass, memory_mode="off")
    entities = await _enabled_usage_entities(hass, agent)
    ids = {key: entities[key].entity_id for key in ("usage", "usage_today", "usage_month")}
    entry, subentry = agent.entry, agent.subentry
    manager = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)
    await hass.async_start()
    await _latest_usage_statistics(hass, freezer, start, 0, {"usage": ids["usage"]})
    await manager.async_record_request(successful=True, usage=RequestUsage(total_tokens=85))
    before = await _latest_usage_statistics(hass, freezer, start, 5, {"usage": ids["usage"]})
    assert {row["sum"] for row in before.values()} == {85}
    snapshot = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    document = backup.finalize_backup_snapshot(snapshot)["document"]

    async def temporarily_unavailable(*_):
        raise PermissionError("Usage file temporarily unreadable")

    with monkeypatch.context() as scoped:
        scoped.setattr(usage, "async_get_durable_usage", temporarily_unavailable)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    fallback = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)
    assert fallback.is_volatile
    await fallback.async_record_request(successful=True, usage=RequestUsage(total_tokens=7))
    assert {hass.states.get(entity_id).state for entity_id in ids.values()} == {"unavailable"}
    interim = await _latest_usage_statistics(hass, freezer, start, 10, {"usage": ids["usage"]})
    assert {row["sum"] for row in interim.values()} == {85}

    next_month = start + timedelta(minutes=20, seconds=1)
    freezer.move_to(next_month)
    async_fire_time_changed(hass, next_month)
    await hass.async_block_till_done()
    await _latest_usage_statistics(hass, freezer, start, 20, {"usage": ids["usage"]})
    result = await transfer.async_restore_transfer(
        hass, entry, subentry, document, sections=[transfer.SECTION_USAGE]
    )
    assert result["status"] == "restored"
    restored = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)
    assert not restored.is_volatile
    assert restored.totals.total_tokens == 85
    final = await _latest_usage_statistics(hass, freezer, start, 25, {"usage": ids["usage"]})
    assert final["usage"]["state"] == 85
    assert {hass.states.get(ids[name]).state for name in ("usage_today", "usage_month")} == {"0"}
    assert {row["sum"] for row in final.values()} == {85}


async def _unload_recorded_usage(hass, key, manager):
    """Detach live Usage and allow the next setup to read its real files."""
    from custom_components.extended_openai_conversation_responses import usage

    assert await hass.config_entries.async_unload(key[0])
    await hass.async_block_till_done()
    manager.dispose_maintenance()
    hass.data[usage._USAGE_MANAGERS].pop(key)


@pytest.mark.usefixtures("real_store_io")
@pytest.mark.parametrize("damaged_store", ["daily", "details"])
async def test_usage_aggregate_repair_preserves_recorder(hass, freezer, damaged_store):
    from custom_components.extended_openai_conversation_responses import usage

    start = dt_util.parse_datetime("2026-06-30T23:40:00+00:00")
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to(start)
    agent = await _sensor_agent(hass, memory_mode="off")
    entities = await _enabled_usage_entities(hass, agent)
    ids = {name: entities[name].entity_id for name in ("usage", "usage_today", "usage_month")}
    key = (agent.entry.entry_id, agent.subentry.subentry_id)
    manager = await async_get_usage(hass, *key)
    await hass.async_start()
    await _latest_usage_statistics(hass, freezer, start, 0, {"usage": ids["usage"]})
    await manager.async_record_request(successful=True, usage=RequestUsage(total_tokens=85))
    before = await _latest_usage_statistics(hass, freezer, start, 5, {"usage": ids["usage"]})
    assert {row["sum"] for row in before.values()} == {85}
    await _unload_recorded_usage(hass, key, manager)

    _, daily, details = usage._durable_usage_stores(hass, *key)
    store = daily if damaged_store == "daily" else details
    saved = await store.async_load()
    damaged = deepcopy(saved)
    if damaged_store == "daily":
        damaged["days"]["2026-06-30"]["total_tokens"] = "damaged"
    else:
        damaged["requests"] = {}
    await store.async_save(damaged)
    assert await hass.config_entries.async_setup(key[0])
    await hass.async_block_till_done()
    manager = await async_get_usage(hass, *key)
    assert manager.persistence_status()["storage_repair_required"]
    assert hass.states.get(ids["usage"]).state == "85"
    assert {hass.states.get(ids[name]).state for name in ("usage_today", "usage_month")} == {
        "unavailable" if damaged_store == "daily" else "85"
    }
    interim = await _latest_usage_statistics(hass, freezer, start, 10, {"usage": ids["usage"]})
    assert {row["sum"] for row in interim.values()} == {85}
    await manager.async_record_request(successful=True, usage=RequestUsage(total_tokens=20))
    expected = 85 if damaged_store == "daily" else 105
    assert hass.states.get(ids["usage"]).state == str(expected)
    await manager.async_shutdown()
    await _unload_recorded_usage(hass, key, manager)
    assert await store.async_load() == damaged
    assert await hass.config_entries.async_setup(key[0])
    await hass.async_block_till_done()
    manager = await async_get_usage(hass, *key)
    assert manager.totals.total_tokens == expected
    assert hass.states.get(ids["usage"]).state == str(expected)
    assert {hass.states.get(ids[name]).state for name in ("usage_today", "usage_month")} == {
        "unavailable" if damaged_store == "daily" else "105"
    }
    midnight = start + timedelta(minutes=20, seconds=1)
    freezer.move_to(midnight)
    async_fire_time_changed(hass, midnight)
    await hass.async_block_till_done()
    await _latest_usage_statistics(hass, freezer, start, 20, {"usage": ids["usage"]})

    await _unload_recorded_usage(hass, key, manager)
    await store.async_save(saved)
    assert await hass.config_entries.async_setup(key[0])
    await hass.async_block_till_done()
    after = await _latest_usage_statistics(hass, freezer, start, 25, {"usage": ids["usage"]})
    assert after["usage"]["state"] == expected
    assert {hass.states.get(ids[name]).state for name in ("usage_today", "usage_month")} == {"0"}
    assert {row["sum"] for row in after.values()} == {expected}
