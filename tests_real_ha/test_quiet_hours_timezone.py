"""Quiet Hours follows real HA timezone updates without policy rewrites."""

from datetime import UTC, datetime

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.extended_openai_conversation_responses.quiet_hours import (
    async_get_quiet_hours,
)
from tests_real_ha.test_quiet_hours_scheduling import (
    _install_satellite_entities,
    _install_control_services,
)


@pytest.mark.parametrize("starting_active", [False, True])
async def test_timezone_update_reconciles_public_state_controls_and_deadlines(
    hass, freezer, starting_active
):
    freezer.move_to(datetime(2026, 10, 30, 10, 0, 30, tzinfo=UTC))
    await hass.config.async_set_time_zone("UTC")
    _, media, wake = _install_satellite_entities(hass)
    calls = []
    _install_control_services(hass, calls)
    manager = await async_get_quiet_hours(hass)
    try:
        await manager.async_update_config(
            {
                "enabled": True,
                "start": "09:59" if starting_active else "11:00",
                "end": "10:02" if starting_active else "11:02",
                "max_volume": 0.2,
                "wake_sound": "off",
            }
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        entity_id = manager.snapshot()["state_entity_id"]
        assert hass.states.get(entity_id).state == ("on" if starting_active else "off")
        revision = manager.revision()
        calls.clear()
        # Use the native runtime update, including its real core-config event.
        # No EOAI getter, policy update or private reconciliation follows it.
        await hass.config.async_update(time_zone="Europe/Paris")
        await hass.async_block_till_done(wait_background_tasks=True)
        assert hass.states.get(entity_id).state == ("off" if starting_active else "on")
        assert manager.snapshot()["active"] is not starting_active
        assert manager.revision() == revision
        assert hass.states.get(media).attributes["volume_level"] == pytest.approx(
            0.6 if starting_active else 0.2
        )
        assert hass.states.get(wake).state == ("on" if starting_active else "off")
        assert calls == [
            ("volume_set", media),
            ("turn_on" if starting_active else "turn_off", wake),
        ]
        if not starting_active:
            assert manager.snapshot()["period_ends_at"] == "2026-10-30T11:02:00+01:00"
            calls.clear()
            # Before discovery or the old UTC deadlines, real HA scheduling must end it.
            end = datetime(2026, 10, 30, 10, 2, 2, tzinfo=UTC)
            freezer.move_to(end)
            async_fire_time_changed(hass, end)
            await hass.async_block_till_done(wait_background_tasks=True)
            assert hass.states.get(entity_id).state == "off"
            assert hass.states.get(media).attributes["volume_level"] == pytest.approx(
                0.6
            )
            assert hass.states.get(wake).state == "on"
            assert calls == [("volume_set", media), ("turn_on", wake)]
    finally:
        await manager.async_shutdown()


@pytest.mark.parametrize("enabled", [True, False])
async def test_timezone_listener_preserves_overlapping_ownership_and_retires_on_shutdown(
    hass, freezer, enabled
):
    freezer.move_to(datetime(2026, 10, 30, 10, 0, 30, tzinfo=UTC))
    await hass.config.async_set_time_zone("UTC")
    _, media, wake = _install_satellite_entities(hass)
    calls = []
    _install_control_services(hass, calls)
    manager = await async_get_quiet_hours(hass)
    await manager.async_update_config(
        {
            "enabled": enabled,
            "start": "09:00",
            "end": "12:00",
            "max_volume": 0.2,
            "wake_sound": "off",
        }
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    entity_id = manager.snapshot()["state_entity_id"]
    listener = manager._timezone_unsubscribe
    callbacks = tuple(manager._unsubscribers)
    await manager.async_setup()
    assert manager._timezone_unsubscribe is listener
    calls.clear()
    try:
        await hass.config.async_update(elevation=10)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert tuple(manager._unsubscribers) == callbacks
        await hass.config.async_update(time_zone="Europe/Paris")
        await hass.async_block_till_done(wait_background_tasks=True)
        assert not calls, (
            "an overlapping period retains its controls without restore/reapply"
        )
        assert hass.states.get(entity_id).state == ("on" if enabled else "off")
        assert hass.states.get(media).attributes["volume_level"] == pytest.approx(
            0.2 if enabled else 0.6
        )
        assert hass.states.get(wake).state == ("off" if enabled else "on")
        if enabled:
            assert manager.snapshot()["period_ends_at"] == "2026-10-30T12:00:00+01:00"
    finally:
        await manager.async_shutdown()
    assert manager._timezone_unsubscribe is None
    assert manager._unsubscribers == []
    calls.clear()
    await hass.config.async_update(time_zone="UTC")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not calls
    assert hass.states.get(entity_id) is None
