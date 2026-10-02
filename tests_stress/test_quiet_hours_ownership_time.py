"""Ownership and time edges for real registry-backed Quiet Hours controls."""

from __future__ import annotations

from datetime import UTC, datetime
import errno
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import atomicwrites
import pytest

from custom_components.extended_openai_conversation_responses.quiet_hours import (
    QuietHoursManager,
    _config_from_data,
    async_get_quiet_hours,
)
from homeassistant.core import HomeAssistant
from tests_real_ha.test_quiet_hours_scheduling import (
    _install_control_services,
    _install_satellite_entities,
)
from tests_stress.conftest import record
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401

DUBLIN = ZoneInfo("Europe/Dublin")


def _volume(hass: HomeAssistant, entity_id: str) -> float:
    state = hass.states.get(entity_id)
    assert state is not None
    return float(state.attributes["volume_level"])


@pytest.mark.asyncio
async def test_runtime_timezone_switch_reconciles_the_same_instant(
    hass: HomeAssistant,
    stress_trace: list[dict],
) -> None:
    """An in-place HA timezone change must close and reopen the correct period."""
    await hass.config.async_set_time_zone("Europe/Dublin")
    _, media, wake = _install_satellite_entities(
        hass, slug="live-timezone", volume=0.65, wake="on"
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
        instant = datetime(2026, 1, 10, 23, 0, tzinfo=UTC)
        await manager.async_reconcile(now=instant)
        assert manager.active is not None
        assert _volume(hass, media) == pytest.approx(0.20)
        assert hass.states.get(wake).state == "off"

        await hass.config.async_set_time_zone("America/New_York")
        await manager.async_reconcile(now=instant)
        assert manager.active is None
        assert _volume(hass, media) == pytest.approx(0.65)
        assert hass.states.get(wake).state == "on"

        await manager.async_reconcile(now=datetime(2026, 1, 11, 3, 0, tzinfo=UTC))
        assert manager.active is not None
        assert _volume(hass, media) == pytest.approx(0.20)
        assert hass.states.get(wake).state == "off"

        await manager.async_reconcile(now=datetime(2026, 1, 11, 12, 0, tzinfo=UTC))
        assert manager.active is None
        assert _volume(hass, media) == pytest.approx(0.65)
        assert hass.states.get(wake).state == "on"
        record(
            stress_trace,
            "summary",
            layer="Real HA",
            runtime_timezone_switches=1,
            quiet_time_boundary_cases=4,
        )
    finally:
        await manager.async_shutdown()


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


@pytest.mark.parametrize("kind", ["volume", "wake_sound"])
@pytest.mark.parametrize("stage", ["observation", "ownership"])
@pytest.mark.parametrize("recovery", ["live", "reload"])
@pytest.mark.usefixtures("real_store_io")
async def test_quiet_control_store_failure_retries_without_losing_original_value(
    hass,
    monkeypatch,
    stress_trace,
    freezer,
    kind,
    stage,
    recovery,
):
    """Fault the actual target control writes before any corresponding HA action."""
    await hass.config.async_set_time_zone("Europe/Dublin")
    now = [datetime(2026, 1, 10, 12, 0, tzinfo=DUBLIN)]
    from custom_components.extended_openai_conversation_responses import (
        quiet_hours as module,
    )

    freezer.move_to(now[0])
    satellite, media, wake = _install_satellite_entities(
        hass,
        slug="failure-alpha",
        name="Storage failure Alpha",
        volume=0.77 if kind == "volume" else 0.08,
        wake="on" if kind == "wake_sound" else "off",
    )
    _, beta_media, beta_wake = _install_satellite_entities(
        hass, slug="unchanged-beta", name="Unaffected Beta", volume=0.09, wake="off"
    )
    calls = []
    _install_control_services(hass, calls)
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
    await manager._async_save_locked()
    assert manager._initialized
    discovered = next(
        item
        for item in manager.discovery_snapshot()
        if item["satellite_entity_id"] == satellite
    )
    assert discovered["media_player_entity_id"] == media
    assert discovered["wake_sound_entity_id"] == wake
    target = media if kind == "volume" else wake
    path = Path(manager._store.path)
    replace = atomicwrites._replace_atomic
    faults = []

    def fail_target_write(source, destination):
        if Path(destination) == path and not faults:
            payload = json.loads(Path(source).read_text())["data"]
            active = payload.get("active") or {}
            observed = target in active.get("observed_controls", [])
            owned = target in active.get("controls", {})
            if observed and owned == (stage == "ownership"):
                faults.append({"observed": observed, "owned": owned})
                assert all(entity_id != target for _, entity_id in calls)
                raise OSError(
                    errno.EIO,
                    "Injected target control Store failure before replacement",
                )
        return replace(source, destination)

    try:
        now[0] = datetime(2026, 1, 10, 22, 5, tzinfo=DUBLIN)
        freezer.move_to(now[0])
        assert (
            module.quiet_period_for(now[0], manager.config.start, manager.config.end)
            is not None
        )
        with monkeypatch.context() as fault:
            fault.setattr(atomicwrites, "_replace_atomic", fail_target_write)
            with pytest.raises(OSError):
                await manager.async_reconcile()
        assert len(faults) == 1
        assert all(entity_id != target for _, entity_id in calls)
        assert _volume(hass, media) == pytest.approx(0.77 if kind == "volume" else 0.08)
        assert hass.states.get(wake).state == ("on" if kind == "wake_sound" else "off")
        durable_before = json.loads(path.read_text())["data"]["active"]
        assert target not in (durable_before or {}).get("controls", {})
        record(
            stress_trace,
            "quiet_control_storage_fault",
            stage=stage,
            kind=kind,
            recovery=recovery,
            failed_before_service=True,
            live_observation=target
            in (manager.active or {}).get("observed_controls", []),
            live_ownership=target in (manager.active or {}).get("controls", {}),
            durable_observation=target
            in (durable_before or {}).get("observed_controls", []),
        )
        if recovery == "reload":
            await manager.async_shutdown()
            manager = QuietHoursManager(hass)
            await manager.async_setup()
        else:
            await manager.async_reconcile()
        assert _volume(hass, media) == pytest.approx(
            0.20 if kind == "volume" else 0.08
        ), "Failed Store write permanently suppressed the intended volume action"
        assert hass.states.get(wake).state == "off", (
            "Failed Store write permanently suppressed the intended wake-sound action"
        )
        assert sum(entity_id == target for _, entity_id in calls) == 1
        control = manager.active["controls"][target]
        assert control["satellite_entity_id"] == satellite
        assert control["original_value"] == (0.77 if kind == "volume" else True)
        durable_after = json.loads(path.read_text())["data"]["active"]
        assert durable_after["controls"][target] == control
        assert target not in durable_after.get("pending_controls", {})
        assert _volume(hass, beta_media) == pytest.approx(0.09)
        assert hass.states.get(beta_wake).state == "off"
        # A second reload preserves the original baseline and does not duplicate action.
        await manager.async_shutdown()
        manager = QuietHoursManager(hass)
        await manager.async_setup()
        assert (
            manager.active["controls"][target]["original_value"]
            == control["original_value"]
        )
        assert sum(entity_id == target for _, entity_id in calls) == 1
        now[0] = datetime(2026, 1, 11, 7, 0, tzinfo=DUBLIN)
        freezer.move_to(now[0])
        await manager.async_reconcile()
        assert manager.active is None
        assert json.loads(path.read_text())["data"]["active"] is None
        assert _volume(hass, media) == pytest.approx(0.77 if kind == "volume" else 0.08)
        assert hass.states.get(wake).state == ("on" if kind == "wake_sound" else "off")
        assert sum(entity_id == target for _, entity_id in calls) == 2
        assert _volume(hass, beta_media) == pytest.approx(0.09)
        assert hass.states.get(beta_wake).state == "off"
        record(
            stress_trace,
            "summary",
            layer="Real HA controls and atomic Store",
            quiet_storage_failure_cases=1,
            quiet_storage_retry_checks=1,
            quiet_storage_restoration_checks=1,
        )
    finally:
        await manager.async_shutdown()
