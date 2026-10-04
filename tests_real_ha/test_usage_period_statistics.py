"""Period counters preserve accumulated usage in the real HA recorder."""

from copy import deepcopy
from datetime import timedelta
from functools import partial

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
    do_adhoc_statistics,
    statistics_during_period,
)

from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    async_get_usage,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from tests_real_ha.test_acceptance_lifecycle import _registry_entries
from tests_real_ha.test_entry_point_contract_matrix import _contract_agent


@pytest.mark.parametrize(
    "period,boundary",
    [
        ("today", "2026-03-29T23:00:00+00:00"),
        ("today", "2026-10-26T00:00:00+00:00"),
        ("month", "2026-03-31T23:00:00+00:00"),
        ("month", "2026-11-01T00:00:00+00:00"),
    ],
)
@pytest.mark.parametrize("reload_across_boundary", [False, True])
async def test_period_rollover_keeps_recorder_sum_and_allows_corrections(
    hass, freezer, period, boundary, reload_across_boundary
):
    hass.config.set_time_zone("Europe/Dublin")
    boundary = dt_util.parse_datetime(boundary)
    start = boundary - timedelta(minutes=20)
    freezer.move_to(start)
    agent = await _contract_agent(hass, memory_mode="off")
    entry = agent.entry
    subentry = agent.subentry
    row = next(
        row
        for row in _registry_entries(hass, entry)
        if row.unique_id == f"{subentry.subentry_id}_usage_{period}"
    )
    entity_id = row.entity_id
    er.async_get(hass).async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await hass.async_start()
    usage = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)

    async def compile_window(window, expected_state, expected_sum):
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
        assert entity_id in rows, rows
        latest = rows[entity_id][-1]
        assert latest["state"] == expected_state, latest
        assert latest["sum"] == expected_sum, latest
        return latest

    assert hass.states.get(entity_id).state == "0"
    original_reset = hass.states.get(entity_id).attributes["last_reset"]
    await compile_window(start, 0, 0)
    await usage.async_record_request(
        successful=True, usage=RequestUsage(total_tokens=1000)
    )
    await compile_window(start + timedelta(minutes=5), 1000, 1000)
    if reload_across_boundary:
        assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.move_to(boundary)
    async_fire_time_changed(hass, boundary)
    if reload_across_boundary:
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "0"  # No provider request is needed to publish the reset.
    new_reset = state.attributes["last_reset"]
    assert new_reset != original_reset
    expected = dt_util.as_local(boundary).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    assert dt_util.parse_datetime(new_reset) == expected
    # The previous period began before the local DST change.
    assert (
        dt_util.as_local(dt_util.parse_datetime(original_reset)).utcoffset()
        != expected.utcoffset()
    )
    await compile_window(boundary, 0, 1000)
    usage = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)
    await usage.async_record_request(
        successful=True, usage=RequestUsage(total_tokens=200)
    )
    await compile_window(boundary + timedelta(minutes=5), 200, 1200)

    # Reconcile a legitimate downward correction and recreate the sensor, as
    # restore does. The period marker must stay unchanged, reducing the sum by 50.
    daily = deepcopy(usage.daily)
    daily[dt_util.now().date().isoformat()]["total_tokens"] = 150
    await usage.async_replace_backup(usage.totals, daily, usage.requests, usage.runs)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["last_reset"] == new_reset
    await compile_window(boundary + timedelta(minutes=10), 150, 1150)
