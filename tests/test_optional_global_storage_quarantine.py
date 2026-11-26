"""Cold-start isolation must not turn damaged global storage into empty state."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    delayed_tools,
    quiet_hours_runtime,
)
from custom_components.extended_openai_conversation_responses.quiet_hours_runtime import (
    QuietHoursManager,
)
from homeassistant.exceptions import HomeAssistantError


async def test_failed_provider_setup_withdraws_runtime_without_closing_transport(
    hass, monkeypatch
):
    from custom_components import extended_openai_conversation_responses as integration
    from custom_components.extended_openai_conversation_responses import agent_deletion
    from custom_components.extended_openai_conversation_responses import (
        conversation_archive,
    )

    entry = SimpleNamespace(
        entry_id="provider", data={"api_key": "test"}, subentries={}
    )
    client = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(integration, "initialize_global_runtime", Mock())
    monkeypatch.setattr(integration, "async_start_model_catalog_refresh", Mock())
    monkeypatch.setattr(
        integration, "async_recover_pending_provider_restore", AsyncMock()
    )
    monkeypatch.setattr(
        integration, "get_authenticated_client", AsyncMock(return_value=client)
    )
    monkeypatch.setattr(integration, "_wrap_provider_client", lambda client, *_: client)
    monkeypatch.setattr(integration, "_async_release_global_effects", AsyncMock())
    monkeypatch.setattr(integration, "async_unload_templates", AsyncMock())
    monkeypatch.setattr(agent_deletion, "async_reconcile_with_retry", AsyncMock())
    monkeypatch.setattr(conversation_archive, "async_start_archive_retention", Mock())

    async def fail_forward(*_):
        assert entry.runtime_data is client
        raise RuntimeError("platform setup failed")

    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", fail_forward)
    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", AsyncMock())
    with pytest.raises(RuntimeError, match="platform setup failed"):
        await integration.async_setup_entry(hass, entry)
    assert not hasattr(entry, "runtime_data")
    client.close.assert_not_awaited()


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {"config": {"max_volume": 2}},
        {"config": {"max_volume": 10**400}},
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


def test_out_of_range_reported_volume_is_unknown(hass):
    hass.states.get.return_value = SimpleNamespace(
        state="idle", attributes={"volume_level": 10**400}
    )
    assert quiet_hours_runtime._current_volume(hass, "media_player.room") is None


@pytest.mark.parametrize("field", ["due_at", "created_at", "expires_at"])
async def test_delayed_invalid_utc_instant_is_dropped_with_valid_sibling(
    hass, monkeypatch, field
):
    from homeassistant.core import CoreState

    monkeypatch.setattr(delayed_tools, "_async_repair_private_store_mode", AsyncMock())
    hass.state = CoreState.not_running
    legitimate = {
        "call_id": "valid",
        "entry_id": "entry",
        "subentry_id": "assistant",
        "tool_name": "tool",
        "arguments": {},
        "due_at": "2026-11-25T12:00:00+00:00",
        "created_at": "2026-11-25T11:59:00+00:00",
    }
    invalid = {
        **legitimate,
        "call_id": "invalid",
        field: "0001-01-01T00:00:00+01:00",
    }
    manager = delayed_tools.DelayedToolManager(hass)
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value={"calls": [invalid, legitimate]}),
        async_save=AsyncMock(),
    )
    await manager.async_setup()
    assert not manager._quarantined
    assert list(manager._records) == ["valid"]
    manager._store.async_save.assert_awaited_once()
    assert [
        row["call_id"] for row in manager._store.async_save.call_args.args[0]["calls"]
    ] == ["valid"]
    assert not manager._tasks


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


@pytest.mark.parametrize("broken_frontend", [False, True])
async def test_optional_corruption_does_not_block_component_or_management_startup(
    hass, monkeypatch, broken_frontend
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
    if broken_frontend:
        integration.async_setup_debug_ui.side_effect = RuntimeError("missing assets")
        integration.async_setup_management_ui.side_effect = RuntimeError("missing assets")
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
    integration.async_setup_debug_ui.assert_awaited_once_with(hass)
    integration.async_setup_management_ui.assert_awaited_once_with(hass)
    quiet._store.async_save.assert_not_awaited()
    delayed._store.async_save.assert_not_awaited()


async def test_quiet_hours_first_reconcile_failure_fences_callbacks(hass, monkeypatch):
    import custom_components.extended_openai_conversation_responses as integration
    from custom_components.extended_openai_conversation_responses import quiet_hours
    from custom_components.extended_openai_conversation_responses.const import DOMAIN

    manager = quiet_hours.QuietHoursManager(hass)
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value={"config": {"enabled": False}}),
        async_save=AsyncMock(),
    )
    broken_unsubscribe = Mock(side_effect=RuntimeError("unsubscribe failed"))
    unsubscribe_remaining = Mock()
    manager._unsubscribers = [broken_unsubscribe, unsubscribe_remaining]
    manager._reschedule = Mock()
    manager.async_reconcile = AsyncMock(side_effect=OSError("reconcile failed"))
    hass.data.setdefault(DOMAIN, {})["quiet_hours_manager"] = manager
    intercom_setup = AsyncMock()
    monkeypatch.setattr(integration, "async_setup_intercom_services", intercom_setup)

    await integration._async_activate_global_effects(hass, "loaded-entry")
    assert await quiet_hours.async_get_quiet_hours(hass) is manager
    assert manager.snapshot()["available"] is False
    with pytest.raises(HomeAssistantError, match="could not finish initialization"):
        await manager.async_set_enabled(True)
    assert not manager._unsubscribers
    assert manager._timezone_unsubscribe is None
    assert manager._registry_unsubscribe is None
    unsubscribe_remaining.assert_called_once()
    manager.async_reconcile.assert_awaited_once()
    manager._store.async_save.assert_not_awaited()
    intercom_setup.assert_awaited_once()


async def test_quiet_hours_shutdown_failure_does_not_block_last_owner_retirement(hass):
    import custom_components.extended_openai_conversation_responses as integration
    from custom_components.extended_openai_conversation_responses.const import DOMAIN

    shutdown = AsyncMock(side_effect=OSError("restore failed"))
    hass.data.setdefault(DOMAIN, {})["quiet_hours_manager"] = SimpleNamespace(
        async_shutdown=shutdown
    )
    integration.initialize_global_runtime(hass).add("loaded-entry")
    await integration._async_release_global_effects(hass, "loaded-entry")

    shutdown.assert_awaited_once()
    assert not integration.initialize_global_runtime(hass)
    assert "quiet_hours_manager" not in hass.data[DOMAIN]


async def test_quiet_hours_getter_cannot_return_retired_generation(hass):
    from custom_components.extended_openai_conversation_responses import quiet_hours
    from custom_components.extended_openai_conversation_responses.const import DOMAIN

    entered = asyncio.Event()
    finish = asyncio.Event()

    async def setup():
        entered.set()
        await finish.wait()

    manager = quiet_hours.QuietHoursManager(hass)
    manager.async_setup = setup
    runtime = hass.data.setdefault(DOMAIN, {})
    runtime["quiet_hours_manager"] = manager
    getter = asyncio.create_task(quiet_hours.async_get_quiet_hours(hass))
    await entered.wait()
    runtime["quiet_hours_manager"] = quiet_hours.QuietHoursManager(hass)
    finish.set()

    with pytest.raises(HomeAssistantError, match="shutting down"):
        await getter
