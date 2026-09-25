"""Ownership and time edges for real registry-backed Quiet Hours controls."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.extended_openai_conversation_responses.quiet_hours import (
    _config_from_data,
    async_get_quiet_hours,
)
from homeassistant.core import HomeAssistant
from tests_real_ha.test_quiet_hours_scheduling import (
    _install_control_services,
    _install_satellite_entities,
)
from tests_stress.conftest import record

DUBLIN = ZoneInfo("Europe/Dublin")


def _volume(hass: HomeAssistant, entity_id: str) -> float:
    state = hass.states.get(entity_id)
    assert state is not None
    return float(state.attributes["volume_level"])


@pytest.mark.asyncio
async def test_manual_control_changes_keep_newer_owner_state(
    hass: HomeAssistant,
    stress_trace: list[dict],
) -> None:
    await hass.config.async_set_time_zone("Europe/Dublin")
    _, owned_media, owned_wake = _install_satellite_entities(
        hass, slug="manual-owner", volume=0.60, wake="on"
    )
    _, untouched_media, untouched_wake = _install_satellite_entities(
        hass, slug="untouched-owner", volume=0.70, wake="on"
    )
    _, media_only, absent_wake = _install_satellite_entities(
        hass, slug="media-only-owner", volume=0.55, wake="on"
    )
    hass.states.async_remove(absent_wake)
    _install_control_services(hass)
    manager = await async_get_quiet_hours(hass)
    manager._config = _config_from_data(
        {
            "enabled": True,
            "start": "22:00",
            "end": "07:00",
            "max_volume": 0.20,
            "wake_sound": "off",
        }
    )
    try:
        await manager.async_reconcile(now=datetime(2026, 1, 10, 22, 0, tzinfo=DUBLIN))
        assert _volume(hass, owned_media) == pytest.approx(0.20)
        assert hass.states.get(owned_wake).state == "off"
        assert _volume(hass, media_only) == pytest.approx(0.20)

        # A manual automation now owns these two values. Reconciliation and the
        # end transition must not overwrite the newer state.
        current = hass.states.get(owned_media)
        hass.states.async_set(
            owned_media,
            current.state,
            {**current.attributes, "volume_level": 0.40},
        )
        hass.states.async_set(owned_wake, "on")
        await manager.async_reconcile(now=datetime(2026, 1, 10, 23, 0, tzinfo=DUBLIN))
        assert _volume(hass, owned_media) == pytest.approx(0.40)
        assert hass.states.get(owned_wake).state == "on"

        await manager.async_reconcile(now=datetime(2026, 1, 11, 7, 0, tzinfo=DUBLIN))
        assert _volume(hass, owned_media) == pytest.approx(0.40)
        assert hass.states.get(owned_wake).state == "on"
        assert _volume(hass, untouched_media) == pytest.approx(0.70)
        assert hass.states.get(untouched_wake).state == "on"
        assert _volume(hass, media_only) == pytest.approx(0.55)
        assert hass.states.get(absent_wake) is None
        assert manager.active is None
        record(
            stress_trace,
            "summary",
            layer="Real HA",
            quiet_ownership_cases=2,
            quiet_heterogeneous_devices=3,
        )
    finally:
        await manager.async_shutdown()


@pytest.mark.asyncio
async def test_exact_boundaries_dst_and_jumps_reconcile_multiple_satellites(
    hass: HomeAssistant,
    stress_trace: list[dict],
) -> None:
    await hass.config.async_set_time_zone("Europe/Dublin")
    controls = [
        _install_satellite_entities(
            hass, slug=f"time-{index}", volume=0.60 + index / 10
        )
        for index in range(2)
    ]
    _install_control_services(hass)
    manager = await async_get_quiet_hours(hass)
    manager._config = _config_from_data(
        {
            "enabled": True,
            "start": "22:00",
            "end": "07:00",
            "max_volume": 0.20,
            "wake_sound": "off",
        }
    )

    async def assert_at(
        year: int,
        month: int,
        day: int,
        hour: int,
        minute: int,
        active: bool,
        *,
        fold: int = 0,
    ) -> None:
        await manager.async_reconcile(
            now=datetime(year, month, day, hour, minute, tzinfo=DUBLIN, fold=fold)
        )
        assert (manager.active is not None) is active
        for _, media, wake in controls:
            assert _volume(hass, media) == pytest.approx(
                0.20 if active else (0.60 if media == controls[0][1] else 0.70)
            )
            assert hass.states.get(wake).state == ("off" if active else "on")

    try:
        await assert_at(2026, 1, 10, 21, 59, False)
        await assert_at(2026, 1, 10, 22, 0, True)
        await assert_at(2026, 1, 10, 23, 59, True)
        await assert_at(2026, 1, 11, 0, 0, True)
        await assert_at(2026, 1, 11, 7, 0, False)
        # Forward jump across an entire period must not invent side effects.
        await assert_at(2026, 1, 12, 21, 59, False)
        await assert_at(2026, 1, 13, 7, 0, False)
        # Backward jump crosses the start boundary and then leaves the period.
        await assert_at(2026, 1, 12, 22, 0, True)
        await assert_at(2026, 1, 12, 21, 59, False)
        await assert_at(2026, 3, 28, 22, 0, True)
        await assert_at(2026, 3, 29, 2, 0, True)
        await assert_at(2026, 3, 29, 7, 0, False)
        await assert_at(2026, 10, 24, 22, 0, True)
        await assert_at(2026, 10, 25, 1, 30, True, fold=1)
        await assert_at(2026, 10, 25, 7, 0, False)
        record(
            stress_trace,
            "summary",
            layer="Real HA",
            quiet_time_boundary_cases=15,
            quiet_dst_cases=2,
            quiet_heterogeneous_devices=2,
        )
    finally:
        await manager.async_shutdown()


@pytest.mark.asyncio
async def test_active_policy_change_and_new_control_keep_pre_quiet_baselines(
    hass: HomeAssistant,
    stress_trace: list[dict],
) -> None:
    await hass.config.async_set_time_zone("Europe/Dublin")
    _, first_media, first_wake = _install_satellite_entities(
        hass, slug="changing-policy", volume=0.60, wake="on"
    )
    _install_control_services(hass)
    manager = await async_get_quiet_hours(hass)
    manager._config = _config_from_data(
        {
            "enabled": True,
            "start": "22:00",
            "end": "07:00",
            "max_volume": 0.20,
            "wake_sound": "off",
        }
    )
    try:
        await manager.async_reconcile(now=datetime(2026, 1, 10, 22, 0, tzinfo=DUBLIN))
        assert _volume(hass, first_media) == pytest.approx(0.20)
        assert hass.states.get(first_wake).state == "off"

        # Moving the start while active replaces the period. Production must
        # restore the pre-quiet 0.60 before applying the new 0.10 policy.
        manager._config = _config_from_data(
            {
                "enabled": True,
                "start": "21:00",
                "end": "08:00",
                "max_volume": 0.10,
                "wake_sound": "unchanged",
            }
        )
        await manager.async_reconcile(now=datetime(2026, 1, 10, 23, 0, tzinfo=DUBLIN))
        assert _volume(hass, first_media) == pytest.approx(0.10)
        assert hass.states.get(first_wake).state == "on"
        assert manager.active is not None
        assert manager.active["controls"][first_media][
            "original_value"
        ] == pytest.approx(0.60)

        _, second_media, second_wake = _install_satellite_entities(
            hass, slug="new-active-control", volume=0.80, wake="on"
        )
        await manager.async_reconcile(now=datetime(2026, 1, 10, 23, 10, tzinfo=DUBLIN))
        assert _volume(hass, second_media) == pytest.approx(0.10)
        assert manager.active["controls"][second_media][
            "original_value"
        ] == pytest.approx(0.80)
        assert hass.states.get(second_wake).state == "on"

        manager._config = _config_from_data(
            {
                "enabled": False,
                "start": "21:00",
                "end": "08:00",
                "max_volume": 0.10,
                "wake_sound": "unchanged",
            }
        )
        await manager.async_reconcile(now=datetime(2026, 1, 10, 23, 20, tzinfo=DUBLIN))
        assert _volume(hass, first_media) == pytest.approx(0.60)
        assert _volume(hass, second_media) == pytest.approx(0.80)
        assert manager.active is None
        record(
            stress_trace,
            "summary",
            layer="Real HA",
            quiet_active_policy_mutations=2,
            quiet_heterogeneous_devices=2,
        )
    finally:
        await manager.async_shutdown()


@pytest.mark.asyncio
async def test_transient_control_service_failure_retries_without_losing_baseline(
    hass: HomeAssistant,
    stress_trace: list[dict],
) -> None:
    await hass.config.async_set_time_zone("Europe/Dublin")
    _, media, wake = _install_satellite_entities(
        hass, slug="retry-control", volume=0.60, wake="on"
    )
    _install_control_services(hass)
    failures = 0

    async def fail_once(_call) -> None:
        nonlocal failures
        failures += 1
        raise RuntimeError("deterministic first volume service failure")

    hass.services.async_register("media_player", "volume_set", fail_once)
    manager = await async_get_quiet_hours(hass)
    manager._config = _config_from_data(
        {
            "enabled": True,
            "start": "22:00",
            "end": "07:00",
            "max_volume": 0.20,
            "wake_sound": "off",
        }
    )
    try:
        await manager.async_reconcile(now=datetime(2026, 1, 10, 22, 0, tzinfo=DUBLIN))
        assert failures == 1
        assert _volume(hass, media) == pytest.approx(0.60)
        assert hass.states.get(wake).state == "off"
        _install_control_services(hass)
        await manager.async_reconcile(now=datetime(2026, 1, 10, 22, 1, tzinfo=DUBLIN))
        assert _volume(hass, media) == pytest.approx(0.20)
        await manager.async_reconcile(now=datetime(2026, 1, 11, 7, 0, tzinfo=DUBLIN))
        assert _volume(hass, media) == pytest.approx(0.60)
        assert hass.states.get(wake).state == "on"
        assert manager.active is None
        record(
            stress_trace,
            "summary",
            layer="Real HA",
            quiet_transient_service_failures=1,
        )
    finally:
        await manager.async_shutdown()
