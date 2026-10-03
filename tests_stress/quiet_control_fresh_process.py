"""Genuine HA control application interrupted between persistence and service effects."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sys

from custom_components.extended_openai_conversation_responses.quiet_hours import (
    QuietHoursManager,
    _config_from_data,
)
from homeassistant import bootstrap, loader
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util


async def main():
    directory = Path(sys.argv[1])
    kind, boundary, phase, retain_context = sys.argv[2:]
    device_path = directory / "device.json"
    device = (
        json.loads(device_path.read_text())
        if device_path.exists()
        else {
            "volume": 0.77 if kind == "volume" else 0.08,
            "wake": "on" if kind == "wake_sound" else "off",
            "calls": 0,
        }
    )

    def save_device():
        with device_path.open("w", encoding="utf-8") as handle:
            json.dump(device, handle)
            handle.flush()
            os.fsync(handle.fileno())

    save_device()
    hass = HomeAssistant(str(directory))
    hass.config.skip_pip = True
    loader.async_setup(hass)
    assert (
        await bootstrap.async_from_config_dict(
            {
                "homeassistant": {
                    "latitude": 0,
                    "longitude": 0,
                    "unit_system": "metric",
                    "time_zone": "UTC",
                }
            },
            hass,
        )
        is hass
    )
    satellite, media, wake = (
        "assist_satellite.process_probe",
        "media_player.process_probe",
        "switch.process_probe",
    )
    hass.states.async_set(satellite, "idle")
    context = (
        Context(id=device["context_id"])
        if retain_context == "1" and device.get("context_id")
        else None
    )
    hass.states.async_set(
        media, "idle", {"volume_level": device["volume"]}, context=context
    )
    hass.states.async_set(wake, device["wake"], context=context)

    async def volume_set(call):
        device["volume"] = call.data["volume_level"]
        device["calls"] += 1
        device["context_id"] = call.context.id
        hass.states.async_set(
            media, "idle", {"volume_level": device["volume"]}, context=call.context
        )
        save_device()

    async def switch_set(call):
        device["wake"] = "on" if call.service == "turn_on" else "off"
        device["calls"] += 1
        device["context_id"] = call.context.id
        hass.states.async_set(wake, device["wake"], context=call.context)
        save_device()

    hass.services.async_register("media_player", "volume_set", volume_set)
    hass.services.async_register("switch", "turn_on", switch_set)
    hass.services.async_register("switch", "turn_off", switch_set)
    manager = QuietHoursManager(hass)
    target = media if kind == "volume" else wake
    if phase == "interrupt":
        await manager.async_setup()
        now = dt_util.now()
        manager._config = _config_from_data(
            {
                "enabled": True,
                "start": (now - timedelta(minutes=2)).strftime("%H:%M"),
                "end": (now + timedelta(minutes=10)).strftime("%H:%M"),
                "max_volume": 0.20,
                "wake_sound": "off",
                "overrides": {
                    satellite: {
                        "media_player_entity_id": media,
                        "wake_sound_entity_id": wake,
                    }
                },
            }
        )
        await manager._async_save_locked()
        if boundary == "before_action":

            async def interrupt(*args):
                os._exit(74)

            if kind == "volume":
                manager._async_set_volume = interrupt
            else:
                manager._async_set_switch = interrupt
        else:
            save = manager._async_save_control_state_locked

            async def interrupt():
                if (manager.active or {}).get("controls", {}).get(target, {}).get(
                    "application_state"
                ) == "applied":
                    os._exit(74)
                await save()

            manager._async_save_control_state_locked = interrupt
        await manager.async_reconcile()
        raise AssertionError("The requested interruption was not reached")
    try:
        await manager.async_setup()
        for _ in range(3):
            await manager.async_reconcile()
        assert device["calls"] == 1
        indeterminate = boundary == "after_action" and retain_context == "0"
        applied = (
            manager.active["controls"]
            .get(target, {})
            .get("application_state", "unowned")
        )
        assert applied == ("unowned" if indeterminate else "applied")
        await manager.async_reconcile(now=dt_util.now() + timedelta(minutes=20))
        assert device["calls"] == (1 if indeterminate else 2)
        assert device["volume"] == (
            (0.20 if indeterminate else 0.77) if kind == "volume" else 0.08
        )
        assert device["wake"] == (
            "on" if kind == "wake_sound" and not indeterminate else "off"
        )
        print(
            "QUIET_APPLICATION_RECOVERY="
            + json.dumps(
                {
                    "applied_calls": 1,
                    "restored_calls": device["calls"],
                    "phase": applied,
                }
            ),
            flush=True,
        )
    finally:
        await manager.async_shutdown()
        await hass.async_stop()


if __name__ == "__main__":
    asyncio.run(main())
