"""Known native Guest Store corruption stays local to its assistant's sensors."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import guest_mode, sensor
from custom_components.extended_openai_conversation_responses.persistence_hardening import (
    StorageRepairRequired,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from tests.test_sensor_coverage import FakeUsage, _subentry


@pytest.mark.parametrize("wrapped", [False, True])
async def test_guest_io_failure_keeps_sibling_and_usage_sensors(tmp_path, monkeypatch, wrapped):
    hass = HomeAssistant(str(tmp_path))
    bad, good = _subentry(), _subentry()
    bad.subentry_id, good.subentry_id = "unavailable", "healthy"
    entry = SimpleNamespace(entry_id="provider", subentries={"bad": bad, "good": good})
    native_load = guest_mode.PropagatingWriteStore.async_load
    failed = False

    async def load(store):
        nonlocal failed
        if store.key.endswith(".unavailable") and not failed:
            failed = True
            io_error = OSError("temporary read I/O failure")
            if wrapped:
                raise HomeAssistantError("native storage read failed") from io_error
            raise io_error
        return await native_load(store)

    monkeypatch.setattr(guest_mode.PropagatingWriteStore, "async_load", load)
    monkeypatch.setattr(sensor, "async_get_usage", AsyncMock(return_value=FakeUsage()))
    additions = {}
    await sensor.async_setup_entry(hass, entry, lambda entities, **kw: additions.update({kw["config_subentry_id"]: entities}))
    assert len(additions["unavailable"]) == len(additions["healthy"]) == 5
    assert not additions["unavailable"][-1].available
    assert additions["healthy"][-1].available
    await guest_mode.async_get_guest_mode(hass, entry.entry_id, bad.subentry_id)
    assert additions["unavailable"][-1].available
    await hass.async_stop(force=True)


@pytest.mark.parametrize("damage", ["schedule", "json", "envelope", "version-type", "future-version"])
async def test_guest_store_corruption_does_not_remove_sibling_sensors(
    tmp_path, monkeypatch, damage, caplog
):
    if os.name == "nt":
        # HA's Linux corruption filenames contain colons; adapt only that native
        # rename filename on Windows, retaining real I/O and corruption handling.
        rename = os.rename

        def portable_rename(source, destination):
            dest = Path(destination)
            return rename(source, dest.with_name(dest.name.replace(":", "-")))

        monkeypatch.setattr(os, "rename", portable_rename)
    hass = HomeAssistant(str(tmp_path))
    bad, good = _subentry(), _subentry()
    bad.subentry_id = "damaged"
    good.subentry_id = "healthy"
    entry = SimpleNamespace(
        entry_id="provider", subentries={"damaged": bad, "healthy": good}
    )
    manager = guest_mode.GuestModeManager(hass, entry.entry_id, bad.subentry_id)
    path = Path(manager._store.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "version": guest_mode.GUEST_MODE_STORAGE_VERSION,
        "minor_version": 1,
        "key": path.name,
        "data": {"schedule": None},
    }
    if damage == "schedule":
        envelope["data"] = {"schedule": {"active_from": "invalid-time"}}
    elif damage == "envelope":
        del envelope["version"]
    elif damage == "version-type":
        envelope["version"] = "1"
    elif damage == "future-version":
        envelope["version"] = 999
    raw = '{"version":' if damage == "json" else json.dumps(envelope)
    path.write_text(raw, encoding="utf-8")
    monkeypatch.setattr(sensor, "async_get_usage", AsyncMock(return_value=FakeUsage()))
    additions = []
    await sensor.async_setup_entry(
        hass,
        entry,
        lambda entities, **kw: additions.append((kw["config_subentry_id"], entities)),
    )
    assert [(key, len(entities)) for key, entities in additions] == [
        ("damaged", 5),
        ("healthy", 5),
    ]
    assert not additions[0][1][-1].available
    assert additions[1][1][-1].available
    assert (
        await guest_mode.async_get_guest_mode(hass, "provider", "healthy")
    ).status()["state"] == "inactive"
    with pytest.raises(StorageRepairRequired):
        await guest_mode.async_get_guest_mode(hass, "provider", "damaged")
    assert not guest_mode.get_loaded_guest_mode(hass, "provider", "damaged").initialized
    evidence = (
        [path]
        if damage != "json"
        else list(path.parent.glob(path.name + ".corrupt*"))
    )
    assert evidence and any(
        item.read_text(encoding="utf-8") == raw for item in evidence
    )
    assert "requires repair" in caplog.text
    # Native corrupt-store preservation schedules delayed HA Store work. Flush
    # it through HA's normal stop lifecycle, including the healthy manager.
    await hass.async_stop(force=True)
