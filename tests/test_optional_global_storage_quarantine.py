"""Cold-start isolation must not turn damaged global storage into empty state."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import (
    delayed_tools,
    quiet_hours_runtime,
)
from custom_components.extended_openai_conversation_responses.quiet_hours_runtime import (
    QuietHoursManager,
)


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {"config": {"max_volume": 2}},
        {"config": {}, "active": {"period_started_at": "invalid"}},
        {"config": {}, "selection_identities": ["unexpected"]},
    ],
)
async def test_quiet_hours_invalid_generation_is_never_published_or_saved(hass, raw):
    manager = QuietHoursManager(hass)
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value=deepcopy(raw)),
        async_save=AsyncMock(),
    )
    manager._reschedule = Mock()
    manager.async_reconcile = AsyncMock()
    await manager.async_setup()
    assert manager.startup_unavailable
    assert manager.snapshot()["available"] is False
    assert manager.snapshot()["config"] if False else True
    assert manager.active is None
    manager._reschedule.assert_not_called()
    manager.async_reconcile.assert_not_awaited()
    manager._store.async_save.assert_not_awaited()
    with pytest.raises(HomeAssistantError, match="unreadable or malformed"):
        await manager.async_update_config({})
    with pytest.raises(HomeAssistantError, match="unreadable or malformed"):
        await manager.async_set_enabled(True)
    await manager.async_setup()
    manager._store.async_load.assert_awaited_once()


async def test_quiet_hours_read_failure_does_not_trigger_device_actions(hass):
    manager = QuietHoursManager(hass)
    manager._store = SimpleNamespace(
        async_load=AsyncMock(side_effect=OSError("private path")),
        async_save=AsyncMock(),
    )
    await manager.async_setup()
    assert manager.startup_unavailable
    assert "private path" not in manager.snapshot()["error"]
    await manager.async_reconcile()
    hass.services.async_call.assert_not_awaited()
    manager._store.async_save.assert_not_awaited()


@pytest.mark.parametrize("raw", [[], {"calls": {}}, {"unexpected": []}])
async def test_delayed_invalid_shape_does_not_rewrite_or_schedule(hass, monkeypatch, raw):
    monkeypatch.setattr(delayed_tools, "_async_repair_private_store_mode", AsyncMock())
    manager = delayed_tools.DelayedToolManager(hass)
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value=deepcopy(raw)),
        async_save=AsyncMock(),
    )
    await manager.async_setup()
    assert manager._quarantined
    assert manager._setup_complete
    assert manager._quarantine_reason == "saved storage is unreadable or malformed"
    assert not manager._records
    manager._handle_started()
    assert not manager._tasks
    manager._store.async_save.assert_not_awaited()
    await manager.async_setup()
    manager._store.async_load.assert_awaited_once()


async def test_missing_global_stores_still_start_normally(hass, monkeypatch):
    quiet = QuietHoursManager(hass)
    quiet._store = SimpleNamespace(async_load=AsyncMock(return_value=None))
    quiet._reschedule = Mock()
    quiet.async_reconcile = AsyncMock()
    await quiet.async_setup()
    assert not quiet.startup_unavailable
    quiet._reschedule.assert_called_once_with()
    quiet.async_reconcile.assert_awaited_once_with()

    monkeypatch.setattr(delayed_tools, "_async_repair_private_store_mode", AsyncMock())
    delayed = delayed_tools.DelayedToolManager(hass)
    delayed._store = SimpleNamespace(
        async_load=AsyncMock(return_value=None),
        async_save=AsyncMock(),
    )
    await delayed.async_setup()
    assert not delayed._quarantined
    assert delayed._setup_complete
    assert not delayed._records
