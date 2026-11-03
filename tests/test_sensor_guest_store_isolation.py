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
from tests.test_sensor_coverage import FakeUsage, _subentry


@pytest.mark.parametrize("damage", ["schedule", "json"])
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
    raw = (
        json.dumps(
            {
                "version": guest_mode.GUEST_MODE_STORAGE_VERSION,
                "minor_version": 1,
                "key": path.name,
                "data": {"schedule": {"active_from": "invalid-time"}},
            }
        )
        if damage == "schedule"
        else '{"version":'
    )
    path.write_text(raw, encoding="utf-8")
    monkeypatch.setattr(sensor, "async_get_usage", AsyncMock(return_value=FakeUsage()))
    additions = []
    await sensor.async_setup_entry(
        hass,
        entry,
        lambda entities, **kw: additions.append((kw["config_subentry_id"], entities)),
    )
    assert [(key, len(entities)) for key, entities in additions] == [("healthy", 5)]
    assert (
        await guest_mode.async_get_guest_mode(hass, "provider", "healthy")
    ).status()["state"] == "inactive"
    with pytest.raises(StorageRepairRequired):
        await guest_mode.async_get_guest_mode(hass, "provider", "damaged")
    assert not guest_mode.get_loaded_guest_mode(hass, "provider", "damaged").initialized
    evidence = (
        [path]
        if damage == "schedule"
        else list(path.parent.glob(path.name + ".corrupt*"))
    )
    assert evidence and any(
        item.read_text(encoding="utf-8") == raw for item in evidence
    )
    assert "requires repair" in caplog.text
    # Native corrupt-store preservation schedules delayed HA Store work. Flush
    # it through HA's normal stop lifecycle, including the healthy manager.
    await hass.async_stop(force=True)
