"""Nightly persistence compatibility and wall-clock discontinuity acceptance."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

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
import custom_components.extended_openai_conversation_responses.usage as usage_module
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    UsageManager,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.util import dt as dt_util
from tests.test_usage_accounting_recovery import FakeStorage
from tests_stress.conftest import record
from tests_stress.test_temporary_memory_scale import MemoryStore
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


async def test_usage_buckets_survive_dst_folds_and_backward_clock_jump(
    monkeypatch, stress_trace
) -> None:
    """Each request is counted once on its HA-local date across clock discontinuities."""
    dublin = ZoneInfo("Europe/Dublin")
    now = datetime(2026, 3, 29, 0, 30, tzinfo=UTC)
    monkeypatch.setattr(usage_module.dt_util, "utcnow", lambda: now)
    monkeypatch.setattr(
        usage_module.dt_util, "as_local", lambda value: value.astimezone(dublin)
    )
    primary, daily, details = FakeStorage(), FakeStorage(), FakeStorage()
    manager = UsageManager(primary, daily, details, agent_subentry_id="dst-agent")
    await manager.async_initialize()
    instants = (
        datetime(2026, 3, 29, 0, 30, tzinfo=UTC),
        datetime(2026, 3, 29, 1, 30, tzinfo=UTC),
        datetime(2026, 3, 28, 23, 30, tzinfo=UTC),
        datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
        datetime(2026, 10, 25, 1, 30, tzinfo=UTC),
    )
    for index, instant in enumerate(instants):
        now = instant
        await manager.async_record_request(
            successful=True, usage=RequestUsage(total_tokens=index + 1)
        )
    assert manager.totals.api_request_count == len(instants)
    assert manager.summary_for_date("2026-03-28")["api_request_count"] == 1
    assert manager.summary_for_date("2026-03-29")["api_request_count"] == 2
    assert manager.summary_for_date("2026-10-25")["api_request_count"] == 2
    restarted = UsageManager(primary, daily, details, agent_subentry_id="dst-agent")
    await restarted.async_initialize()
    assert restarted.totals.api_request_count == len(instants)
    assert restarted.summary_for_date("2026-10-25")["api_request_count"] == 2
    record(stress_trace, "usage_dst_discontinuity", requests=len(instants), days=3)


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
    await hass.async_block_till_done()
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


@pytest.mark.parametrize("day", ["2026-03-29", "2026-10-25"])
@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
async def test_populated_local_calendar_journey_preserves_visible_expiry_and_recovery(
    hass, monkeypatch, freezer, real_store_io, stress_trace, day, api_mode
):
    """One native runtime crosses DST, expiry, pruning and reload with visible effects."""
    import json
    from collections import Counter
    from pathlib import Path
    from custom_components.extended_openai_conversation_responses.const import (
        DEFAULT_CONF_FUNCTION_TOOLS,
    )
    from custom_components.extended_openai_conversation_responses.delayed_tools import (
        async_setup_delayed_tools,
    )
    from custom_components.extended_openai_conversation_responses.quiet_hours import (
        async_get_quiet_hours,
    )
    from homeassistant.components import conversation
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
    )
    from homeassistant.core import Context
    from tests_real_ha.test_cross_feature_acceptance import _agent
    from tests_real_ha.test_delayed_tool_due_reauthorization import (
        _schedule_delayed_call,
        _ENTITY_ID,
    )
    from tests_real_ha.test_provider_wire_e2e import (
        _install_wire,
        _chat_sse_text,
        _responses_sse_text,
        _speech,
    )
    from tests_real_ha.test_quiet_hours_scheduling import (
        _install_satellite_entities,
        _install_control_services,
    )

    base = datetime.fromisoformat(day).replace(tzinfo=UTC) + timedelta(minutes=15)
    freezer.move_to(base)
    await hass.config.async_set_time_zone("Europe/Dublin")
    user = await hass.auth.async_create_user(
        "Calendar journey owner", group_ids=["system-admin"]
    )
    agent = await _agent(
        hass,
        api_mode=api_mode,
        functions=[deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])],
        archive_enabled=True,
        temporary_memory="balanced",
        memory_mode="manual",
        memory_auto_retrieve_limit=0,
    )
    scope = f"user:{user.id}"
    _, media, wake = _install_satellite_entities(
        hass, slug="integrated-calendar", volume=0.71, wake="on"
    )
    _install_control_services(hass)
    quiet = await async_get_quiet_hours(hass)
    await quiet.async_update_config(
        {
            "enabled": True,
            "start": "00:00",
            "end": "04:00",
            "max_volume": 0.2,
            "wake_sound": "off",
        }
    )
    assert hass.states.get(media).attributes["volume_level"] == pytest.approx(0.2)
    assert hass.states.get(wake).state == "off"
    hass.states.async_set(_ENTITY_ID, "on")
    async_expose_entity(hass, "conversation", _ENTITY_ID, True)
    effects = []
    service_entered, release_service = asyncio.Event(), asyncio.Event()

    async def turn_off(call):
        effects.append(dt_util.utcnow().isoformat())
        hass.states.async_set(_ENTITY_ID, "off", context=call.context)
        service_entered.set()
        await release_service.wait()

    hass.services.async_register("light", "turn_off", turn_off)
    delayed = await async_setup_delayed_tools(hass)
    call_id = await _schedule_delayed_call(agent, delayed, user, "calendar-delayed")
    scheduled_worker = delayed._tasks[call_id]
    assert not effects and hass.states.get(_ENTITY_ID).state == "on"
    expires = base + timedelta(hours=1)
    await agent._temporary_memory.async_add_owned(
        scope, "CALENDAR TEMPORARY MARKER", expires.isoformat()
    )
    await agent._guest_mode.async_restrict(active_until=expires.isoformat())
    reply = _responses_sse_text if api_mode == "responses" else _chat_sse_text
    days = Counter()
    visible = []

    async def say(marker):
        wire = _install_wire(monkeypatch, agent, [reply(marker)])
        result = await conversation.async_converse(
            hass=hass,
            text=marker,
            conversation_id=None,
            context=Context(user_id=user.id),
            language="en",
            agent_id=agent.entry.entry_id,
        )
        assert _speech(result) == marker
        assert len(wire.requests) == 1
        days[dt_util.as_local(dt_util.utcnow()).date().isoformat()] += 1
        return json.dumps(wire.requests[0]["body"])

    try:
        freezer.move_to(base - timedelta(hours=2))
        await quiet.async_reconcile(now=dt_util.utcnow())
        assert hass.states.get(media).attributes["volume_level"] == pytest.approx(0.71)
        await say("Calendar previous local date")
        freezer.move_to(base)
        await quiet.async_reconcile(now=base)
        first = await say("Calendar guest before expiry")
        assert agent._guest_mode.is_active()
        assert "CALENDAR TEMPORARY MARKER" not in first
        assert (
            len(await agent._temporary_memory.async_active(scope, owner_scope_id=scope))
            == 1
        )
        for instant in (expires - timedelta(microseconds=1), expires):
            freezer.move_to(instant)
            expected_active = instant < expires
            assert agent._guest_mode.is_active() is expected_active
            records = await agent._temporary_memory.async_active(
                scope, owner_scope_id=scope
            )
            assert bool(records) is expected_active
            await quiet.async_reconcile(now=instant)
            assert hass.states.get(media).attributes["volume_level"] == pytest.approx(
                0.2
            )
            visible.append(
                {
                    "utc": instant.isoformat(),
                    "local": dt_util.as_local(instant).isoformat(),
                    "guest": expected_active,
                    "temporary": len(records),
                    "volume": 0.2,
                }
            )
        if agent._temporary_memory._prune_save_task is not None:
            await agent._temporary_memory._prune_save_task
        await asyncio.wait_for(service_entered.wait(), 10)
        assert not scheduled_worker.done()
        assert delayed._records[call_id].status == "executing"
        durable = json.loads(Path(delayed._store.path).read_text())["data"]["calls"]
        assert len(durable) == 1 and durable[0]["call_id"] == call_id
        assert durable[0]["status"] == "executing"
        assert len(effects) == 1 and hass.states.get(_ENTITY_ID).state == "off"
        # The service effect precedes acknowledgement and durable finalization.
        # This native asyncio owner is not drained by HA's tracked-job helper.
        release_service.set()
        await asyncio.wait_for(asyncio.shield(scheduled_worker), 10)
        await hass.async_block_till_done()
        assert call_id not in delayed._records
        assert json.loads(Path(delayed._store.path).read_text())["data"]["calls"] == []
        assert len(effects) == 1 and hass.states.get(_ENTITY_ID).state == "off", effects
        second = await say("Calendar owner after expiry")
        assert "CALENDAR TEMPORARY MARKER" not in second
        assert (await agent._archive.async_list_sessions(scope))["sessions"]
        noon = base.replace(hour=12, minute=0)
        freezer.move_to(noon)
        await quiet.async_reconcile(now=noon)
        assert hass.states.get(media).attributes["volume_level"] == pytest.approx(0.71)
        assert hass.states.get(wake).state == "on"
        await say("Calendar daylight request")
        usage = agent._usage
        for local_day, count in days.items():
            assert usage.summary_for_date(local_day)["api_request_count"] == count
        assert usage.totals.api_request_count == 4
        assert len(days) == 2
        usage.request_retention_days = usage.run_retention_days = 1
        freezer.move_to(noon + timedelta(days=2))
        await usage.async_prune_details()
        await usage._async_save_details()
        pruned = await agent._archive.async_prune(1)
        assert pruned["deleted_sessions"] >= 1
        assert not usage.requests and not usage.runs
        assert (await agent._archive.async_list_sessions(scope))["sessions"] == []
        assert await hass.config_entries.async_reload(agent.entry.entry_id)
        await hass.async_block_till_done()
        agent = conversation.async_get_agent(hass, agent.entry.entry_id)
        assert agent._usage.totals.api_request_count == 4
        assert (
            await agent._temporary_memory.async_active(scope, owner_scope_id=scope)
            == []
        )
        assert (await agent._archive.async_list_sessions(scope))["sessions"] == []
        assert not agent._guest_mode.is_active()
        assert not delayed._records and len(effects) == 1
        await say("Calendar recovered after reload")
        assert agent._usage.totals.api_request_count == 5
        record(
            stress_trace,
            "summary",
            integrated_calendar_journeys=1,
            delayed_worker_settlement_checks=1,
            api=api_mode,
            day=day,
            visible_boundaries=visible,
            delayed_effects=effects,
            local_usage=dict(days),
            pruned_archive=pruned,
        )
    finally:
        release_service.set()
        await asyncio.gather(scheduled_worker, return_exceptions=True)
        await quiet.async_shutdown()
        assert await hass.config_entries.async_unload(agent.entry.entry_id)
