"""Repeated Quiet Hours periods across many real registry-backed satellites."""

from __future__ import annotations

from datetime import datetime, timedelta
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


@pytest.mark.asyncio
async def test_repeated_multi_satellite_quiet_periods_restore_every_owner(
    hass: HomeAssistant,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    await hass.config.async_set_time_zone("Europe/Dublin")
    satellites = 12 * stress_scale
    periods = 5 * stress_scale
    controls = []
    for index in range(satellites):
        volume = round(0.50 + (index % 5) * 0.08, 2)
        _, media, wake = _install_satellite_entities(
            hass,
            slug=f"stress-{index}",
            name=f"Stress satellite {index}",
            volume=volume,
            wake="on",
        )
        controls.append((media, wake, volume))
    _install_control_services(hass)
    manager = await async_get_quiet_hours(hass)
    manager._config = _config_from_data(
        {
            "enabled": True,
            "start": "22:00",
            "end": "07:00",
            "max_volume": 0.20,
            "wake_sound": "off",
            "overrides": {},
        }
    )
    zone = ZoneInfo("Europe/Dublin")
    try:
        for cycle in range(periods):
            day = datetime(2026, 1, 10, 22, 5, tzinfo=zone) + timedelta(days=cycle)
            record(stress_trace, "quiet_start", cycle=cycle, satellites=satellites)
            await manager.async_reconcile(now=day)
            for media, wake, _volume in controls:
                assert hass.states.get(media).attributes[
                    "volume_level"
                ] == pytest.approx(0.20)
                assert hass.states.get(wake).state == "off"
            assert manager.active is not None

            record(stress_trace, "quiet_end", cycle=cycle)
            await manager.async_reconcile(now=day + timedelta(hours=9))
            for media, wake, volume in controls:
                assert hass.states.get(media).attributes[
                    "volume_level"
                ] == pytest.approx(volume)
                assert hass.states.get(wake).state == "on"
            assert manager.active is None
        record(
            stress_trace,
            "summary",
            quiet_periods=periods,
            satellites=satellites,
            control_transitions=periods * satellites * 4,
        )
    finally:
        await manager.async_shutdown()
