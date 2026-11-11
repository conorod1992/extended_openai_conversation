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
    assert "config" not in manager.snapshot()
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


async def test_optional_corruption_does_not_block_component_or_management_startup(
    hass, monkeypatch
):
    """Actual global manager getters stay callable after a quarantined load."""
    from unittest.mock import Mock

    import custom_components.extended_openai_conversation_responses as integration
    from custom_components.extended_openai_conversation_responses import (
        quiet_hours,
        transfer_staging,
    )
    from custom_components.extended_openai_conversation_responses.const import DOMAIN

    unavailable = OSError("private file details must never be published")
    quiet = quiet_hours.QuietHoursManager(hass)
    quiet._store = SimpleNamespace(
        async_load=AsyncMock(side_effect=unavailable),
        async_save=AsyncMock(),
    )
    delayed = delayed_tools.DelayedToolManager(hass)
    delayed._store = SimpleNamespace(
        async_load=AsyncMock(side_effect=unavailable),
        async_save=AsyncMock(),
    )
    hass.data.setdefault(DOMAIN, {})["quiet_hours_manager"] = quiet
    hass.data[DOMAIN][delayed_tools.DATA_DELAYED_TOOL_MANAGER] = delayed
    monkeypatch.setattr(delayed_tools, "_async_repair_private_store_mode", AsyncMock())
    for name in (
        "async_setup_model_catalog",
        "async_migrate_integration",
        "async_recover_pending_restores",
        "async_setup_ha_permissions",
        "async_setup_services",
        "async_setup_intercom_services",
        "async_setup_debug_ui",
        "async_setup_management_ui",
    ):
        monkeypatch.setattr(integration, name, AsyncMock())
    for name in (
        "apply_openai_compatibility",
        "setup_provider_credentials_websocket",
        "setup_backup_transfer_websocket",
    ):
        monkeypatch.setattr(integration, name, Mock())
    monkeypatch.setattr(transfer_staging, "async_get_transfer_staging", AsyncMock())

    assert await integration.async_setup(hass, {}) is True
    quiet._store.async_load.assert_not_awaited()
    await integration._async_activate_global_effects(hass, "loaded-entry")
    assert quiet.startup_unavailable and delayed._quarantined
    integration.async_setup_services.assert_awaited_once_with(hass, {})
    integration.async_setup_management_ui.assert_awaited_once_with(hass)
    quiet._store.async_save.assert_not_awaited()
    delayed._store.async_save.assert_not_awaited()
