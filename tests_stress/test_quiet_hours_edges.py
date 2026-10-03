"""Deterministic real HA proofs for repeated hours and ambiguous control failures."""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    QuietHoursManager,
    _config_from_data,
    async_get_quiet_hours,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _entry,
    _management_call,
    _setup_entry,
)
from tests_real_ha.test_quiet_hours_scheduling import (
    _install_control_services,
    _install_satellite_entities,
)
from tests_stress.conftest import record
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


def policy(start="22:00", end="07:00"):
    return _config_from_data(
        {
            "enabled": True,
            "start": start,
            "end": end,
            "max_volume": 0.2,
            "wake_sound": "off",
        }
    )


@pytest.mark.parametrize(
    ("start", "end", "first", "last"),
    [
        (
            "01:30",
            "03:00",
            datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
            datetime(2026, 10, 25, 3, tzinfo=UTC),
        ),
        (
            "23:00",
            "01:30",
            datetime(2026, 10, 24, 22, tzinfo=UTC),
            datetime(2026, 10, 25, 1, 30, tzinfo=UTC),
        ),
        (
            "01:15",
            "01:45",
            datetime(2026, 10, 25, 0, 15, tzinfo=UTC),
            datetime(2026, 10, 25, 1, 45, tzinfo=UTC),
        ),
    ],
    ids=["start-repeated", "end-repeated", "both-repeated"],
)
async def test_fall_back_entire_interval_has_one_owned_period(
    hass, stress_trace, start, end, first, last
):
    """Drive UTC forward every minute through both folds and count every service call."""
    await hass.config.async_set_time_zone("Europe/Dublin")
    _, media, wake = _install_satellite_entities(
        hass, slug="repeated-hour", volume=0.73
    )
    calls = []
    _install_control_services(hass, calls)
    manager = await async_get_quiet_hours(hass)
    manager._config = policy(start, end)
    identities = set()
    checks = 0
    instant = min(
        first - timedelta(minutes=1), datetime(2026, 10, 24, 23, 59, tzinfo=UTC)
    )
    finish = max(last + timedelta(minutes=1), datetime(2026, 10, 25, 2, 1, tzinfo=UTC))
    try:
        while instant <= finish:
            await manager.async_reconcile(now=instant)
            active = first <= instant < last
            assert (manager.active is not None) is active, instant
            assert hass.states.get(media).attributes["volume_level"] == pytest.approx(
                0.2 if active else 0.73
            ), instant
            assert hass.states.get(wake).state == ("off" if active else "on"), instant
            if active:
                identities.add(
                    (
                        manager.active["period_started_at"],
                        manager.active["period_ends_at"],
                    )
                )
            expected = (
                [] if instant < first else [("volume_set", media), ("turn_off", wake)]
            )
            if instant >= last:
                expected += [("volume_set", media), ("turn_on", wake)]
            assert calls == expected, instant
            checks += 1
            instant += timedelta(minutes=1)
        assert len(identities) == 1
        record(
            stress_trace,
            "summary",
            quiet_repeated_hour_cases=1,
            quiet_monotonic_utc_checks=checks,
            quiet_exact_transition_checks=2,
        )
    finally:
        await manager.async_shutdown()


@pytest.mark.parametrize("kind", ["volume", "switch"])
@pytest.mark.parametrize(
    "manual", [False, True], ids=["restore-baseline", "manual-change"]
)
@pytest.mark.usefixtures("real_store_io")
async def test_control_changes_then_raises_preserves_durable_ownership(
    hass, freezer, stress_trace, kind, manual
):
    """A real failing HA service mutates state before returning its error."""
    await hass.config.async_set_time_zone("Europe/Dublin")
    freezer.move_to(datetime(2026, 1, 10, 12, tzinfo=UTC))
    _, media, wake = _install_satellite_entities(
        hass,
        slug="post-change-failure",
        volume=0.73 if kind == "volume" else 0.08,
        wake="on" if kind == "switch" else "off",
    )
    target = media if kind == "volume" else wake
    calls = []
    faults = []

    async def control(call):
        entity_id = call.data["entity_id"]
        current = hass.states.get(entity_id)
        attributes = dict(current.attributes)
        if call.domain == "media_player":
            attributes["volume_level"] = call.data["volume_level"]
            state = current.state
            value = call.data["volume_level"]
        else:
            state = "on" if call.service == "turn_on" else "off"
            value = state == "on"
        hass.states.async_set(entity_id, state, attributes)
        calls.append((entity_id, value))
        if entity_id == target and not faults:
            faults.append((entity_id, value))
            raise HomeAssistantError(
                "device changed state before acknowledgement failed"
            )

    for domain, service in [
        ("media_player", "volume_set"),
        ("switch", "turn_on"),
        ("switch", "turn_off"),
    ]:
        hass.services.async_register(domain, service, control)
    manager = await async_get_quiet_hours(hass)
    manager._config = policy()
    await manager._async_save_locked()
    original = 0.73 if kind == "volume" else True
    desired = 0.2 if kind == "volume" else False
    try:
        freezer.move_to(datetime(2026, 1, 10, 22, 5, tzinfo=UTC))
        await manager.async_reconcile()
        assert faults == [(target, desired)] and calls == [(target, desired)]
        owned = manager.active["controls"][target]
        assert owned["original_value"] == original and owned["quiet_value"] == desired
        assert (
            json.loads(Path(manager._store.path).read_text())["data"]["active"][
                "controls"
            ][target]
            == owned
        )
        if manual:
            await hass.services.async_call(
                "media_player" if kind == "volume" else "switch",
                "volume_set" if kind == "volume" else "turn_on",
                {
                    "entity_id": target,
                    **({"volume_level": 0.51} if kind == "volume" else {}),
                },
                blocking=True,
            )
        before_restart = list(calls)
        await manager.async_shutdown()
        manager = QuietHoursManager(hass)
        await manager.async_setup()
        assert manager.active["controls"][target] == owned and calls == before_restart
        await manager.async_reconcile()
        assert calls == before_restart
        freezer.move_to(datetime(2026, 1, 11, 7, tzinfo=UTC))
        await manager.async_reconcile()
        assert (
            manager.active is None
            and json.loads(Path(manager._store.path).read_text())["data"]["active"]
            is None
        )
        expected = 0.51 if manual and kind == "volume" else original
        current = hass.states.get(target)
        assert (
            current.attributes["volume_level"]
            if kind == "volume"
            else current.state == "on"
        ) == expected
        assert calls == before_restart + ([] if manual else [(target, original)])
        record(
            stress_trace,
            "summary",
            quiet_post_change_failure_cases=1,
            quiet_durable_baseline_checks=2,
            quiet_manual_failure_checks=int(manual),
        )
    finally:
        await manager.async_shutdown()


@pytest.mark.usefixtures("real_store_io")
async def test_loaded_status_registry_rename_tracks_transitions_and_restart(
    hass, freezer, stress_trace, hass_ws_client
):
    """Rename the genuine HA registry entry while its publishing manager stays loaded."""
    await hass.config.async_set_time_zone("Europe/Dublin")
    freezer.move_to(datetime(2026, 1, 10, 12, tzinfo=UTC))
    entry = _entry(title="Quiet Hours renamed status")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)

    async def management_identity():
        result = await _management_call(
            client, entry=entry, section="quiet_hours", action="get"
        )
        return result["state_entity_id"]

    _, media, wake = _install_satellite_entities(
        hass, slug="renamed-status", volume=0.73
    )
    calls = []
    _install_control_services(hass, calls)
    manager = await async_get_quiet_hours(hass)
    await manager.async_update_config(policy().as_dict())
    old = manager.snapshot()["state_entity_id"]
    new = "binary_sensor.user_named_quiet_hours"
    try:
        registry = er.async_get(hass)
        registry.async_update_entity(old, new_entity_id=new)
        await hass.async_block_till_done()
        assert registry.async_get(old) is None and registry.async_get(new) is not None
        assert manager.snapshot()["state_entity_id"] == new
        assert await management_identity() == new
        for instant, active in [
            (datetime(2026, 1, 10, 22, tzinfo=UTC), True),
            (datetime(2026, 1, 11, 7, tzinfo=UTC), False),
        ]:
            freezer.move_to(instant)
            await manager.async_reconcile()
            await hass.async_block_till_done()
            assert hass.states.get(new).state == ("on" if active else "off")
            assert hass.states.get(old) is None and registry.async_get(old) is None
            assert manager.snapshot()["state_entity_id"] == new
            assert await management_identity() == new
            assert hass.states.get(media).attributes["volume_level"] == pytest.approx(
                0.2 if active else 0.73
            )
            assert hass.states.get(wake).state == ("off" if active else "on")
        assert calls == [
            ("volume_set", media),
            ("turn_off", wake),
            ("volume_set", media),
            ("turn_on", wake),
        ]
        await manager.async_shutdown()
        manager = QuietHoursManager(hass)
        hass.data[DOMAIN]["quiet_hours_manager"] = manager
        await manager.async_setup()
        assert manager.snapshot()["state_entity_id"] == new
        assert await management_identity() == new
        assert hass.states.get(new).state == "off" and hass.states.get(old) is None
        freezer.move_to(datetime(2026, 1, 11, 22, tzinfo=UTC))
        await manager.async_reconcile()
        assert hass.states.get(new).state == "on" and hass.states.get(old) is None
        assert registry.async_get(old) is None
        record(
            stress_trace,
            "summary",
            quiet_registry_rename_cases=1,
            quiet_renamed_transition_checks=3,
            quiet_registry_restart_checks=1,
        )
    finally:
        await manager.async_shutdown()
