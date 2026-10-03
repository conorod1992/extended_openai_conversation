"""Boot native automations in a fresh process from their settled YAML file."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import yaml

from homeassistant import bootstrap, loader
from homeassistant.core import HomeAssistant


async def main():
    source, config = map(Path, sys.argv[1:3])
    config.mkdir()
    (config / "automations.yaml").write_bytes(source.read_bytes())
    (config / "configuration.yaml").write_text(
        "automation: !include automations.yaml\n", encoding="utf-8"
    )
    payload = yaml.safe_load((config / "automations.yaml").read_text(encoding="utf-8"))
    hass = HomeAssistant(str(config))
    hass.config.skip_pip = True
    loader.async_setup(hass)
    effects = []

    async def record(call):
        effects.append(call.data["marker"])

    hass.services.async_register("acceptance_probe", "record", record)
    try:
        assert (
            await bootstrap.async_from_config_dict({"automation": payload}, hass)
            is hass
        )
        assert "automation" in hass.config.components
        await hass.async_start()
        await hass.async_block_till_done(wait_background_tasks=True)
        for event in ("audit_original", "audit_older", "audit_newer"):
            hass.bus.async_fire(event)
        await hass.async_block_till_done()
        print("AUTOMATION_RECOVERY_PROBE=" + json.dumps(sorted(effects)), flush=True)
    finally:
        await hass.async_stop()


if __name__ == "__main__":
    asyncio.run(main())
