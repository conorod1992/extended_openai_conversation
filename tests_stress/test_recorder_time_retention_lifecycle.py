"""Recorder, time, retention, and diagnostic lifecycle hardening."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import os

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.extended_openai_conversation_responses.conversation_archive import (
    async_get_archive,
)
from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    async_get_usage,
)
from homeassistant.components import recorder
from homeassistant.components.recorder import statistics
from homeassistant.components.recorder.models.statistics import StatisticMeanType
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from tests_real_ha.test_entry_point_contract_matrix import _contract_agent
from tests_real_ha.test_native_recorder_acceptance import _EXPOSED
from tests_stress.conftest import record


async def _setup_recorder(hass: HomeAssistant, db_url: str) -> None:
    assert await async_setup_component(hass, "recorder", {"recorder": {"db_url": db_url}})
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_usage_detail_pruning_never_rewrites_recorder_aggregate_history(
    hass: HomeAssistant,
    tmp_path,
    stress_trace: list[dict],
) -> None:
    """EOAI detail retention is independent from already-recorded HA statistics."""
    await _setup_recorder(hass, f"sqlite:///{tmp_path / 'usage-retention.db'}")
    agent = await _contract_agent(hass, memory_mode="off")
    entry, subentry = agent.entry, agent.subentry
    usage = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)

    await usage.async_record_request(
        successful=True,
        usage=RequestUsage(total_tokens=1200, input_tokens=900, output_tokens=300),
    )
    await usage.async_record_request(
        successful=True,
        usage=RequestUsage(total_tokens=300, input_tokens=200, output_tokens=100),
    )
    await hass.async_block_till_done()
    await recorder.get_instance(hass).async_block_till_done()

    lifetime_before = usage.totals.total_tokens
    daily_before = dict(usage.daily)
    assert lifetime_before == 1500
    assert len(usage.requests) == 2

    cleared = await usage.async_clear_details(confirm=True)
    assert cleared["deleted_requests"] == 2
    assert usage.requests == []
    assert usage.totals.total_tokens == lifetime_before
    assert usage.daily == daily_before

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    reloaded = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)
    assert reloaded.requests == []
    assert reloaded.totals.total_tokens == lifetime_before
    assert reloaded.daily == daily_before
    record(
        stress_trace,
        "summary",
        layer="EOAI Usage + HA Recorder",
        usage_detail_resets=1,
        aggregate_tokens_preserved=lifetime_before,
        recorder_independent_retention=True,
    )


@pytest.mark.asyncio
async def test_runtime_timezone_change_updates_archive_date_interpretation_and_usage_day(
    hass: HomeAssistant,
    freezer,
    stress_trace: list[dict],
) -> None:
    """Existing runtime state must use HA's current timezone at each date boundary."""
    instant = dt_util.parse_datetime("2026-01-01T00:30:00+00:00")
    freezer.move_to(instant)
    await hass.config.async_set_time_zone("Europe/Dublin")
    agent = await _contract_agent(hass, memory_mode="off")
    entry, subentry = agent.entry, agent.subentry
    usage = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)
    archive = await async_get_archive(hass, entry.entry_id, subentry.subentry_id)

    await usage.async_record_request(successful=True, usage=RequestUsage(total_tokens=7))
    session = await archive.async_begin_session(
        "timezone-session",
        user_scope("timezone-owner", source="test"),
        "timezone-conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    assert session is not None
    await archive.async_record_turn(
        session.session_id,
        run_id="timezone-run",
        user_text="timezone searchable marker",
        assistant_text="timezone reply",
        successful=True,
    )

    assert usage.today_summary()["total_tokens"] == 7
    before = await archive.async_search(
        "user:timezone-owner",
        "timezone searchable marker",
        start_date="2026-01-01",
        end_date="2026-01-01",
    )
    assert len(before["results"]) == 1

    await hass.config.async_set_time_zone("America/Los_Angeles")
    # The same UTC instant is still Dec 31 locally after the runtime timezone switch.
    assert dt_util.now().date().isoformat() == "2025-12-31"
    assert usage.today_summary()["total_tokens"] == 0
    old_day = await archive.async_search(
        "user:timezone-owner",
        "timezone searchable marker",
        start_date="2025-12-31",
        end_date="2025-12-31",
    )
    new_day = await archive.async_search(
        "user:timezone-owner",
        "timezone searchable marker",
        start_date="2026-01-01",
        end_date="2026-01-01",
    )
    assert len(old_day["results"]) == 1
    assert new_day["results"] == []
    record(
        stress_trace,
        "summary",
        runtime_timezone_changes=1,
        archive_date_boundary_recomputations=2,
        usage_day_boundary_recomputations=1,
    )


@pytest.mark.asyncio
async def test_recorder_purge_can_overlap_native_history_query_and_recover(
    hass: HomeAssistant,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    """A purge racing an EOAI history read must not corrupt the query/session lifecycle."""
    from custom_components.extended_openai_conversation_responses.functions import native

    await _setup_recorder(hass, f"sqlite:///{tmp_path / 'purge-race.db'}")
    started = dt_util.utcnow() - timedelta(seconds=2)
    hass.states.async_set("sensor.recorder_acceptance", "before-purge")
    await hass.async_block_till_done()
    instance = recorder.get_instance(hass)
    await instance.async_block_till_done()

    entered = asyncio.Event()
    release = asyncio.Event()
    original = native.recorder_history.get_significant_states_with_session

    def gated(*args, **kwargs):
        hass.loop.call_soon_threadsafe(entered.set)
        while not release.is_set():
            import time
            time.sleep(0.01)
        return original(*args, **kwargs)

    monkeypatch.setattr(native.recorder_history, "get_significant_states_with_session", gated)
    query = asyncio.create_task(
        NativeFunction().get_history(
            hass,
            {},
            {
                "entity_ids": ["sensor.recorder_acceptance"],
                "start_time": started.isoformat(),
                "end_time": dt_util.utcnow().isoformat(),
                "significant_changes_only": False,
                "include_start_time_state": True,
            },
            None,
            _EXPOSED,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=10)

    purge = asyncio.create_task(
        hass.services.async_call(
            "recorder",
            "purge",
            {"keep_days": 0, "repack": False, "apply_filter": False},
            blocking=True,
        )
    )
    await asyncio.sleep(0)
    release.set()
    rows = await asyncio.wait_for(query, timeout=20)
    await asyncio.wait_for(purge, timeout=30)
    assert rows

    hass.states.async_set("sensor.recorder_acceptance", "after-purge")
    await hass.async_block_till_done()
    await instance.async_block_till_done()
    healthy = await NativeFunction().get_history(
        hass,
        {},
        {"entity_ids": ["sensor.recorder_acceptance"], "significant_changes_only": False},
        None,
        _EXPOSED,
    )
    assert healthy
    record(
        stress_trace,
        "summary",
        recorder_purge_query_races=1,
        post_purge_history_recoveries=1,
    )


@pytest.mark.parametrize(
    ("env_name", "backend"),
    [
        ("EOAI_TEST_MARIADB_URL", "mariadb"),
        ("EOAI_TEST_POSTGRES_URL", "postgresql"),
    ],
)
@pytest.mark.asyncio
async def test_native_history_against_external_recorder_database(
    hass: HomeAssistant,
    env_name: str,
    backend: str,
    stress_trace: list[dict],
) -> None:
    """Enhanced/manual environments can certify EOAI against real external Recorder DBs."""
    db_url = os.getenv(env_name)
    if not db_url:
        if os.getenv("EOAI_REQUIRE_EXTERNAL_RECORDER") == "1":
            pytest.fail(f"{env_name} is required for external Recorder certification")
        pytest.skip(f"{env_name} is not configured")
    await _setup_recorder(hass, db_url)
    hass.states.async_set("sensor.recorder_acceptance", f"{backend}-recorded")
    await hass.async_block_till_done()
    await recorder.get_instance(hass).async_block_till_done()
    rows = await NativeFunction().get_history(
        hass,
        {},
        {"entity_ids": ["sensor.recorder_acceptance"], "significant_changes_only": False},
        None,
        _EXPOSED,
    )
    assert any(
        item.get("state") == f"{backend}-recorded"
        for group in rows
        for item in group
    )
    record(
        stress_trace,
        "summary",
        external_recorder_backend=backend,
        external_recorder_history_queries=1,
    )


@pytest.mark.parametrize(
    ("env_name", "backend"),
    [
        ("EOAI_TEST_MARIADB_URL", "mariadb"),
        ("EOAI_TEST_POSTGRES_URL", "postgresql"),
    ],
)
@pytest.mark.asyncio
async def test_recorder_backend_switch_preserves_history_and_statistics_semantics(
    hass: HomeAssistant,
    env_name: str,
    backend: str,
    stress_trace: list[dict],
) -> None:
    """Migrated-style Recorder data keeps EOAI semantics after leaving SQLite."""
    db_url = os.getenv(env_name)
    if not db_url:
        if os.getenv("EOAI_REQUIRE_EXTERNAL_RECORDER") == "1":
            pytest.fail(f"{env_name} is required for external Recorder certification")
        pytest.skip(f"{env_name} is not configured")

    await _setup_recorder(hass, db_url)

    # This state sequence mirrors the boundary-sensitive SQLite acceptance case.
    # The migration itself belongs to Home Assistant; EOAI's contract is that the
    # same Recorder data has the same visible meaning once the backend changes.
    hass.states.async_set("sensor.recorder_acceptance", "before")
    await hass.async_block_till_done()
    before = hass.states.get("sensor.recorder_acceptance").last_changed
    hass.states.async_set("sensor.recorder_acceptance", "inside")
    await hass.async_block_till_done()
    inside = hass.states.get("sensor.recorder_acceptance").last_changed
    hass.states.async_set("sensor.recorder_acceptance", "later")
    await hass.async_block_till_done()
    later = hass.states.get("sensor.recorder_acceptance").last_changed
    instance = recorder.get_instance(hass)
    await instance.async_block_till_done()

    between = inside + (later - inside) / 2
    history = await NativeFunction().get_history(
        hass,
        {},
        {
            "entity_ids": ["sensor.recorder_acceptance"],
            "start_time": (before + timedelta(microseconds=1)).isoformat(),
            "end_time": between.isoformat(),
            "include_start_time_state": False,
            "significant_changes_only": False,
        },
        None,
        _EXPOSED,
    )
    assert [[item["state"] for item in group] for group in history] == [["inside"]]

    with_boundary = await NativeFunction().get_history(
        hass,
        {},
        {
            "entity_ids": ["sensor.recorder_acceptance"],
            "start_time": (inside + timedelta(microseconds=1)).isoformat(),
            "include_start_time_state": True,
            "significant_changes_only": False,
        },
        None,
        _EXPOSED,
    )
    assert with_boundary
    assert with_boundary[0][0]["state"] == "inside"

    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=2
    )
    metadata = {
        "statistic_id": "sensor.recorder_acceptance",
        "source": "recorder",
        "name": "Recorder backend switch",
        "unit_of_measurement": "°C",
        "unit_class": "temperature",
        "has_mean": True,
        "mean_type": StatisticMeanType.ARITHMETIC,
        "has_sum": False,
    }
    statistics.async_import_statistics(
        hass,
        metadata,
        [
            {"start": start, "mean": 21.5, "min": 20.0, "max": 23.0},
            {
                "start": start + timedelta(hours=1),
                "mean": 22.5,
                "min": 21.0,
                "max": 24.0,
            },
        ],
    )
    await instance.async_block_till_done()
    stats = await NativeFunction().get_statistics(
        hass,
        {},
        {
            "statistic_ids": ["sensor.recorder_acceptance"],
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(hours=2)).isoformat(),
            "period": "hour",
            "types": {"mean", "min", "max"},
            "units": {"temperature": "°F"},
        },
        None,
        _EXPOSED,
    )
    rows = stats["sensor.recorder_acceptance"]
    assert len(rows) == 2
    assert rows[0]["mean"] == pytest.approx(70.7)
    assert rows[0]["min"] == pytest.approx(68.0)
    assert rows[0]["max"] == pytest.approx(73.4)
    assert [row["start"] for row in rows] == [
        start.timestamp(),
        (start + timedelta(hours=1)).timestamp(),
    ]

    record(
        stress_trace,
        "summary",
        recorder_backend_switch_target=backend,
        backend_switch_history_semantics=1,
        backend_switch_statistics_semantics=1,
    )


@pytest.mark.asyncio
async def test_archive_prune_invalidates_open_detail_without_phantom_reappearance(
    hass: HomeAssistant,
    freezer,
    stress_trace: list[dict],
) -> None:
    """A session viewed before retention runs disappears authoritatively afterwards."""
    freezer.move_to("2026-10-05T12:00:00+00:00")
    agent = await _contract_agent(hass, memory_mode="off")
    archive = await async_get_archive(
        hass, agent.entry.entry_id, agent.subentry.subentry_id
    )
    session = await archive.async_begin_session(
        "retention-open-detail",
        user_scope("retention-owner", source="test"),
        "retention-conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    assert session is not None
    await archive.async_record_turn(
        session.session_id,
        run_id="retention-run",
        user_text="retention viewed marker",
        assistant_text="retention viewed reply",
        successful=True,
    )
    viewed = await archive.async_get(
        "user:retention-owner", session.session_id, 0, 20
    )
    assert viewed["turns"][0]["user_text"] == "retention viewed marker"

    # Advance beyond retention while the caller still holds its already-rendered copy.
    freezer.move_to("2026-11-20T12:00:00+00:00")
    pruned = await archive.async_prune(30)
    assert pruned == {"deleted_sessions": 1, "deleted_turns": 1}
    assert (
        await archive.async_list_sessions("user:retention-owner")
    )["sessions"] == []
    with pytest.raises(ValueError):
        await archive.async_get("user:retention-owner", session.session_id, 0, 20)

    # A detached previously viewed payload remains only a caller snapshot; it cannot
    # make the deleted session visible again in any authoritative query.
    assert viewed["session"]["session_id"] == session.session_id
    assert (
        await archive.async_search(
            "user:retention-owner", "retention viewed marker"
        )
    )["results"] == []
    record(
        stress_trace,
        "summary",
        archive_open_detail_prunes=1,
        stale_archive_snapshots_detached=1,
        phantom_archive_records=0,
    )


def test_request_debug_clear_invalidates_current_detail_and_export_source(
    hass: HomeAssistant,
    stress_trace: list[dict],
) -> None:
    """Clearing diagnostics while a detail is viewed leaves no live/exportable record."""
    from custom_components.extended_openai_conversation_responses.debug import (
        get_debug_manager,
    )
    from custom_components.extended_openai_conversation_responses.debug_management_projection import (
        debug_trace_page,
    )

    manager = get_debug_manager(hass, "debug-entry", "debug-agent")
    manager.configure(enabled=True, limit=5)
    trace = manager.begin(
        entry_id="debug-entry",
        subentry_id="debug-agent",
        user_input={"text": "currently viewed debug marker"},
        incoming_conversation_id="debug-conversation",
    )
    manager.finish(trace, successful=True, result={"speech": "debug result"})

    viewed = debug_trace_page(
        manager, trace.debug_id, provider_offset=0, provider_limit=20
    )
    assert viewed is not None
    assert viewed["debug_id"] == trace.debug_id
    assert manager.clear() == 1
    assert manager.get(trace.debug_id) is None
    assert (
        debug_trace_page(
            manager, trace.debug_id, provider_offset=0, provider_limit=20
        )
        is None
    )
    assert manager.summaries() == []

    # The browser may still hold already-rendered JSON, but a new get/export source
    # is gone immediately and cannot resurrect the cleared run.
    assert viewed["debug_id"] == trace.debug_id
    record(
        stress_trace,
        "summary",
        debug_open_detail_clears=1,
        stale_debug_snapshots_detached=1,
        exportable_cleared_debug_records=0,
    )
