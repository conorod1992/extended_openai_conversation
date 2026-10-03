"""Ownership and time edges for real registry-backed Quiet Hours controls."""

from __future__ import annotations

from datetime import UTC, datetime
import errno
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from custom_components.extended_openai_conversation_responses.quiet_hours import (
    QuietHoursManager,
    _config_from_data,
    async_get_quiet_hours,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
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
    """Fault the real Store writer before commit and the corresponding HA action.

    Quiet Hours uses HA's ordinary non-atomic writer. Gate its prepared-data
    boundary, delegating every unaffected write to the actual native writer.
    """
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
    write_prepared = Store._write_prepared_data
    faults = []
    attempted_writes = []

    def fail_target_write(store, mode, json_data):
        if Path(store.path) == path and not faults:
            payload = json.loads(json_data)["data"]
            active = payload.get("active") or {}
            attempted_writes.append({"destination": str(store.path), "active": active})
            observed = target in active.get("observed_controls", [])
            owned = target in active.get("controls", {})
            if observed and owned == (stage == "ownership"):
                faults.append({"observed": observed, "owned": owned})
                assert all(entity_id != target for _, entity_id in calls)
                raise WriteError("Injected target control writer failure") from OSError(
                    errno.EIO, "Target control write failed before commit"
                )
        return write_prepared(store, mode, json_data)

    try:
        now[0] = datetime(2026, 1, 10, 22, 5, tzinfo=DUBLIN)
        freezer.move_to(now[0])
        assert (
            module.quiet_period_for(now[0], manager.config.start, manager.config.end)
            is not None
        )
        with monkeypatch.context() as fault:
            fault.setattr(Store, "_write_prepared_data", fail_target_write)
            with pytest.raises(OSError, match="Private storage write failed"):
                await manager.async_reconcile(now=now[0])
                pytest.fail(
                    f"Target fault not reached: {attempted_writes=}, {manager.active=}, {calls=}"
                )
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
            layer="Real HA controls and native Store writer",
            quiet_storage_failure_cases=1,
            quiet_storage_retry_checks=1,
            quiet_storage_restoration_checks=1,
        )
    finally:
        await manager.async_shutdown()


@pytest.mark.parametrize("kind", ["volume", "wake_sound"])
@pytest.mark.usefixtures("real_store_io")
async def test_pending_quiet_control_preserves_manual_change_after_storage_failure(
    hass,
    monkeypatch,
    freezer,
    stress_trace,
    kind,
):
    """A user change after a failed ownership write must not be claimed on retry."""
    await hass.config.async_set_time_zone("Europe/Dublin")
    freezer.move_to(datetime(2026, 1, 10, 12, 0, tzinfo=DUBLIN))
    _, media, wake = _install_satellite_entities(
        hass,
        slug="pending-manual-alpha",
        name="Pending manual Alpha",
        volume=0.77 if kind == "volume" else 0.08,
        wake="on" if kind == "wake_sound" else "off",
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
    target = media if kind == "volume" else wake
    path = Path(manager._store.path)
    native_write = Store._write_prepared_data
    faults = []

    def fail_ownership(store, mode, json_data):
        if Path(store.path) == path and not faults:
            active = json.loads(json_data)["data"].get("active") or {}
            if target in active.get("controls", {}):
                assert not calls
                faults.append(True)
                raise WriteError("Injected ownership write failure") from OSError(
                    errno.EIO, "Ownership write failed before commit"
                )
        return native_write(store, mode, json_data)

    try:
        freezer.move_to(datetime(2026, 1, 10, 22, 5, tzinfo=DUBLIN))
        with monkeypatch.context() as fault:
            fault.setattr(Store, "_write_prepared_data", fail_ownership)
            with pytest.raises(OSError):
                await manager.async_reconcile()
        assert len(faults) == 1 and not calls
        pending = json.loads(path.read_text())["data"]["active"]["pending_controls"][
            target
        ]
        assert pending["original_value"] == (0.77 if kind == "volume" else True)
        # Use the actual HA control service to establish the user's changed value.
        await hass.services.async_call(
            "media_player" if kind == "volume" else "switch",
            "volume_set" if kind == "volume" else "turn_off",
            {
                "entity_id": target,
                **({"volume_level": 0.64} if kind == "volume" else {}),
            },
            blocking=True,
        )
        assert len(calls) == 1
        await manager.async_shutdown()
        manager = QuietHoursManager(hass)
        await manager.async_setup()
        assert target in manager.active["observed_controls"]
        assert target not in manager.active["controls"]
        assert target not in manager.active["pending_controls"]
        assert len(calls) == 1
        durable = json.loads(path.read_text())["data"]["active"]
        assert (
            target not in durable["controls"]
            and target not in durable["pending_controls"]
        )
        freezer.move_to(datetime(2026, 1, 11, 7, 0, tzinfo=DUBLIN))
        await manager.async_reconcile()
        assert manager.active is None and len(calls) == 1
        assert _volume(hass, media) == pytest.approx(0.64 if kind == "volume" else 0.08)
        assert hass.states.get(wake).state == "off"
        record(
            stress_trace,
            "summary",
            layer="Real HA control and Store recovery",
            quiet_pending_manual_override_checks=1,
        )
    finally:
        await manager.async_shutdown()


async def _application_probe(hass, freezer, kind):
    await hass.config.async_set_time_zone("Europe/Dublin")
    freezer.move_to(datetime(2026, 1, 10, 12, 0, tzinfo=DUBLIN))
    satellite, media, wake = _install_satellite_entities(
        hass,
        slug="application-alpha",
        volume=0.77 if kind == "volume" else 0.08,
        wake="on" if kind == "wake_sound" else "off",
    )
    _, beta_media, _ = _install_satellite_entities(
        hass, slug="application-beta", volume=0.65, wake="off"
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
    freezer.move_to(datetime(2026, 1, 10, 22, 5, tzinfo=DUBLIN))
    return manager, media if kind == "volume" else wake, beta_media, calls


@pytest.mark.parametrize("kind", ["volume", "wake_sound"])
@pytest.mark.usefixtures("real_store_io")
async def test_quiet_control_lost_service_acknowledgement_does_not_replay_effect(
    hass, monkeypatch, stress_trace, freezer, kind
):
    from homeassistant.exceptions import HomeAssistantError

    manager, target, beta, calls = await _application_probe(hass, freezer, kind)
    registry_type = type(hass.services)
    native = registry_type.async_call
    faults = []

    async def lost_ack(registry, domain, service, data, **kwargs):
        result = await native(registry, domain, service, data, **kwargs)
        if data.get("entity_id") == target and not faults:
            faults.append(True)
            raise HomeAssistantError("Device effect completed; acknowledgement lost")
        return result

    try:
        with monkeypatch.context() as fault:
            fault.setattr(registry_type, "async_call", lost_ack)
            await manager.async_reconcile()
        assert faults and sum(entity == target for _, entity in calls) == 1
        assert manager.active["controls"][target]["application_state"] == "prepared"
        for _ in range(3):
            await manager.async_reconcile()
        assert sum(entity == target for _, entity in calls) == 1
        assert manager.active["controls"][target]["application_state"] == "applied"
        assert _volume(hass, beta) == pytest.approx(0.20)
        await manager.async_reconcile(now=datetime(2026, 1, 11, 8, 0, tzinfo=DUBLIN))
        assert sum(entity == target for _, entity in calls) == 2
        record(stress_trace, "summary", quiet_service_acknowledgement_recoveries=1)
    finally:
        await manager.async_shutdown()


@pytest.mark.parametrize("kind", ["volume", "wake_sound"])
@pytest.mark.parametrize("recovery", ["live", "reload"])
@pytest.mark.parametrize("end_immediately", [False, True])
@pytest.mark.usefixtures("real_store_io")
async def test_unacknowledged_quiet_intent_does_not_claim_independent_goal(
    hass, monkeypatch, stress_trace, freezer, kind, recovery, end_immediately
):
    manager, target, beta, calls = await _application_probe(hass, freezer, kind)
    path = Path(manager._store.path)
    native = Store._write_prepared_data
    faults = []

    def failed_write(store, mode, data):
        if Path(store.path) == path and not faults:
            active = json.loads(data)["data"].get("active") or {}
            if (
                active.get("controls", {}).get(target, {}).get("application_state")
                == "prepared"
            ):
                faults.append(True)
                native(store, mode, data)
                raise WriteError("Prepared acknowledgement lost") from OSError(
                    errno.EIO, "Lost acknowledgement"
                )
        return native(store, mode, data)

    try:
        with monkeypatch.context() as fault:
            fault.setattr(Store, "_write_prepared_data", failed_write)
            with pytest.raises(OSError):
                await manager.async_reconcile()
        assert faults and not any(entity == target for _, entity in calls)
        if kind == "volume":
            await hass.services.async_call(
                "media_player",
                "volume_set",
                {"entity_id": target, "volume_level": 0.20},
                blocking=True,
            )
        else:
            await hass.services.async_call(
                "switch", "turn_off", {"entity_id": target}, blocking=True
            )
        ending = datetime(2026, 1, 11, 8, 0, tzinfo=DUBLIN)
        if end_immediately:
            freezer.move_to(ending)
        if recovery == "reload":
            await manager.async_shutdown()
            manager = QuietHoursManager(hass)
            await manager.async_setup()
        else:
            await manager.async_reconcile()
        if not end_immediately:
            assert target not in manager.active["controls"]
            assert _volume(hass, beta) == pytest.approx(0.20)
            for _ in range(3):
                await manager.async_reconcile()
            await manager.async_reconcile(now=ending)
        assert sum(entity == target for _, entity in calls) == 1
        if kind == "volume":
            assert _volume(hass, target) == pytest.approx(0.20)
        else:
            assert hass.states.get(target).state == "off"
        record(stress_trace, "summary", quiet_independent_goal_preservations=1)
    finally:
        await manager.async_shutdown()


@pytest.mark.parametrize(
    "kind,new_value", [("volume", 0.05), ("volume", 0.60), ("wake_sound", False)]
)
@pytest.mark.parametrize("stage", ["observation", "ownership"])
@pytest.mark.usefixtures("real_store_io")
async def test_successful_quiet_control_save_preserves_newer_independent_value(
    hass, monkeypatch, stress_trace, freezer, kind, new_value, stage
):
    import asyncio
    import threading

    manager, target, beta, calls = await _application_probe(hass, freezer, kind)
    path = Path(manager._store.path)
    entered = asyncio.Event()
    release = threading.Event()
    native = Store._write_prepared_data
    loop = asyncio.get_running_loop()

    def held_write(store, mode, data):
        if Path(store.path) == path and not entered.is_set():
            active = json.loads(data)["data"].get("active") or {}
            if target in active.get("observed_controls", []) and (
                target in active.get("controls", {})
            ) == (stage == "ownership"):
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(15), "Control write was not released"
        return native(store, mode, data)

    caller = None
    try:
        with monkeypatch.context() as gate:
            gate.setattr(Store, "_write_prepared_data", held_write)
            caller = asyncio.create_task(manager.async_reconcile())
            await asyncio.wait_for(entered.wait(), 10)
            if kind == "volume":
                await hass.services.async_call(
                    "media_player",
                    "volume_set",
                    {"entity_id": target, "volume_level": new_value},
                    blocking=True,
                )
            else:
                await hass.services.async_call(
                    "switch", "turn_off", {"entity_id": target}, blocking=True
                )
            release.set()
            await caller
        for _ in range(3):
            await manager.async_reconcile()
        assert sum(entity == target for _, entity in calls) == 1, (
            "Quiet Hours overwrote the independent service action"
        )
        assert target not in manager.active["controls"]
        assert target not in json.loads(path.read_text())["data"]["active"]["controls"]
        assert _volume(hass, beta) == pytest.approx(0.20)
        await manager.async_reconcile(now=datetime(2026, 1, 11, 8, 0, tzinfo=DUBLIN))
        assert sum(entity == target for _, entity in calls) == 1
        if kind == "volume":
            assert _volume(hass, target) == pytest.approx(new_value)
        else:
            assert hass.states.get(target).state == "off"
        assert _volume(hass, beta) == pytest.approx(0.65)
        record(stress_trace, "summary", quiet_saved_control_manual_changes=1)
    finally:
        release.set()
        if caller is not None:
            await asyncio.gather(caller, return_exceptions=True)
        await manager.async_shutdown()


@pytest.mark.parametrize("kind", ["volume", "wake_sound"])
@pytest.mark.parametrize("boundary", ["prepared_ack", "applied_before", "applied_ack"])
@pytest.mark.parametrize("recovery", ["live", "reload"])
@pytest.mark.usefixtures("real_store_io")
async def test_quiet_control_reconciles_prepared_and_applied_write_outcomes(
    hass, monkeypatch, stress_trace, freezer, kind, boundary, recovery
):
    manager, target, beta, calls = await _application_probe(hass, freezer, kind)
    path = Path(manager._store.path)
    native = Store._write_prepared_data
    faults = []

    def failed_write(store, mode, data):
        if Path(store.path) == path and not faults:
            active = json.loads(data)["data"].get("active") or {}
            phase = active.get("controls", {}).get(target, {}).get("application_state")
            if phase == ("prepared" if boundary == "prepared_ack" else "applied"):
                faults.append(phase)
                if boundary != "applied_before":
                    native(store, mode, data)
                raise WriteError(
                    "Injected control acknowledgement failure"
                ) from OSError(errno.EIO, "Control write outcome failure")
        return native(store, mode, data)

    try:
        with monkeypatch.context() as fault:
            fault.setattr(Store, "_write_prepared_data", failed_write)
            with pytest.raises(OSError):
                await manager.async_reconcile()
        assert len(faults) == 1
        assert sum(entity == target for _, entity in calls) == int(
            boundary != "prepared_ack"
        )
        durable = json.loads(path.read_text())["data"]["active"]["controls"][target]
        assert durable["application_state"] == (
            "applied" if boundary == "applied_ack" else "prepared"
        )
        if recovery == "reload":
            await manager.async_shutdown()
            manager = QuietHoursManager(hass)
            await manager.async_setup()
        else:
            await manager.async_reconcile()
        for _ in range(3):
            await manager.async_reconcile()
        assert sum(entity == target for _, entity in calls) == 1
        assert manager.active["controls"][target]["application_state"] == "applied"
        assert (
            json.loads(path.read_text())["data"]["active"]["controls"][target][
                "application_state"
            ]
            == "applied"
        )
        if kind == "volume":
            assert _volume(hass, target) == pytest.approx(0.20)
        else:
            assert hass.states.get(target).state == "off"
        assert _volume(hass, beta) == pytest.approx(0.20)
        await manager.async_reconcile(now=datetime(2026, 1, 11, 8, 0, tzinfo=DUBLIN))
        assert sum(entity == target for _, entity in calls) == 2
        if kind == "volume":
            assert _volume(hass, target) == pytest.approx(0.77)
        else:
            assert hass.states.get(target).state == "on"
        assert _volume(hass, beta) == pytest.approx(0.65)
        record(stress_trace, "summary", quiet_prepared_recovery_cases=1)
    finally:
        await manager.async_shutdown()


@pytest.mark.parametrize("kind", ["volume", "wake_sound"])
@pytest.mark.parametrize("boundary", ["before_action", "after_action"])
@pytest.mark.parametrize(
    "retain_context", [False, True], ids=["indeterminate-context", "retained-context"]
)
async def test_quiet_control_prepared_intent_recovers_in_fresh_process(
    tmp_path, stress_trace, kind, boundary, retain_context
):
    import asyncio
    import os
    import sys

    helper = Path(__file__).with_name("quiet_control_fresh_process.py")
    root = helper.parent.parent
    environment = {**os.environ, "PYTHONPATH": str(root)}

    async def run(phase):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(helper),
            str(tmp_path),
            kind,
            boundary,
            phase,
            str(int(retain_context)),
            cwd=root,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 40)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        return process.returncode, output.decode()

    status, output = await run("interrupt")
    assert status == 74, output
    device = json.loads((tmp_path / "device.json").read_text())
    assert device["calls"] == int(boundary == "after_action")
    status, output = await run("recover")
    assert status == 0, output
    result = json.loads(output.split("QUIET_APPLICATION_RECOVERY=")[-1].splitlines()[0])
    indeterminate = boundary == "after_action" and not retain_context
    assert result == {
        "applied_calls": 1,
        "restored_calls": 1 if indeterminate else 2,
        "phase": "unowned" if indeterminate else "applied",
    }
    record(
        stress_trace,
        "summary",
        quiet_process_application_recoveries=1,
        quiet_process_indeterminate_controls=int(indeterminate),
    )
