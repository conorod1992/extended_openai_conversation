"""Loaded entries, rather than persisted entry count, own global effects."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components import extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import (
    intercom,
    quiet_hours,
    quiet_hours_runtime,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from tests.test_management_single_agent_prewarm import _Entry, _hass, _stub_setup

OWNERS = f"{DOMAIN}.loaded_entries"


@pytest.mark.parametrize(
    "getter",
    [
        quiet_hours.async_get_quiet_hours,
        quiet_hours_runtime.async_get_quiet_hours,
        intercom.async_get_intercom,
    ],
)
async def test_suspended_getters_cannot_resurrect_managers(hass, getter):
    hass.data[OWNERS] = set()
    with pytest.raises(HomeAssistantError, match="loaded"):
        await getter(hass)
    assert "quiet_hours_manager" not in hass.data.get(DOMAIN, {})
    assert intercom.DATA_KEY not in hass.data


async def test_last_successful_unload_stops_globals_without_deleting_settings(
    hass, monkeypatch
):
    hass.data[OWNERS] = {"one", "two"}
    quiet = SimpleNamespace(async_shutdown=AsyncMock())
    broadcast = SimpleNamespace(async_shutdown=AsyncMock())
    hass.data.setdefault(DOMAIN, {})["quiet_hours_manager"] = quiet
    hass.data[intercom.DATA_KEY] = broadcast
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    monkeypatch.setattr(integration, "async_unload_templates", AsyncMock())
    for identity in ["one", "two"]:
        entry = SimpleNamespace(
            entry_id=identity, state=ConfigEntryState.UNLOAD_IN_PROGRESS, subentries={}
        )
        assert await integration.async_unload_entry(hass, entry)
        if identity == "one":
            quiet.async_shutdown.assert_not_awaited()
            broadcast.async_shutdown.assert_not_awaited()
    quiet.async_shutdown.assert_awaited_once()
    broadcast.async_shutdown.assert_awaited_once()
    assert hass.data[OWNERS] == set()
    assert "quiet_hours_manager" not in hass.data[DOMAIN]
    assert intercom.DATA_KEY not in hass.data
    hass.services.async_remove.assert_not_called()


async def test_failed_unload_keeps_owner_and_globals(hass):
    hass.data[OWNERS] = {"one"}
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
    assert not await integration.async_unload_entry(
        hass, SimpleNamespace(entry_id="one")
    )
    assert hass.data[OWNERS] == {"one"}


async def test_reload_and_failed_setup_have_no_stale_owner(monkeypatch):
    _stub_setup(monkeypatch)
    entry = _Entry("entry", 0)
    hass = _hass(entry)
    assert await integration.async_setup_entry(hass, entry)
    assert hass.data[OWNERS] == {"entry"}
    entry.state = ConfigEntryState.UNLOAD_IN_PROGRESS
    assert await integration.async_unload_entry(hass, entry)
    assert hass.data[OWNERS] == set()
    hass.config_entries.async_forward_entry_setups.side_effect = HomeAssistantError(
        "failed platform"
    )
    with pytest.raises(HomeAssistantError, match="failed platform"):
        await integration.async_setup_entry(hass, entry)
    assert hass.data[OWNERS] == set()
    hass.config_entries.async_forward_entry_setups.side_effect = None
    assert await integration.async_setup_entry(hass, entry)
    assert hass.data[OWNERS] == {"entry"}


async def test_cold_setup_with_only_disabled_entries_never_starts_effects(
    hass, monkeypatch
):
    from custom_components.extended_openai_conversation_responses import (
        agent_deletion,
        transfer_staging,
    )

    hass.config_entries.async_entries.return_value = [
        SimpleNamespace(disabled_by="user")
    ]
    for name in [
        "async_setup_model_catalog",
        "async_migrate_integration",
        "async_setup_delayed_tools",
        "async_recover_pending_restores",
        "async_setup_ha_permissions",
        "async_setup_services",
        "async_setup_intercom_services",
        "async_setup_debug_ui",
        "async_setup_management_ui",
    ]:
        monkeypatch.setattr(integration, name, AsyncMock())
    monkeypatch.setattr(agent_deletion, "async_setup_agent_deletion", lambda *_: None)
    monkeypatch.setattr(agent_deletion, "async_reconcile_agent_storage", AsyncMock())
    monkeypatch.setattr(transfer_staging, "async_get_transfer_staging", AsyncMock())
    monkeypatch.setattr(integration, "setup_backup_transfer_websocket", lambda *_: None)
    monkeypatch.setattr(
        integration, "setup_provider_credentials_websocket", lambda *_: None
    )
    quiet = AsyncMock()
    monkeypatch.setattr(integration, "async_get_quiet_hours", quiet)
    assert await integration.async_setup(hass, {})
    assert hass.data[OWNERS] == set()
    quiet.assert_not_awaited()
    with pytest.raises(HomeAssistantError):
        await intercom.async_get_intercom(hass)


@pytest.mark.parametrize("offline", [False, True])
async def test_quiet_suspend_restore_and_resume_keep_true_original(
    hass, monkeypatch, offline
):
    from copy import deepcopy
    from datetime import UTC, datetime

    from tests.test_quiet_hours_runtime import (
        _config,
        _install_registry,
        _install_services,
        _seed_room,
        _stateful_public_manager,
    )

    _install_registry(monkeypatch, "bedroom")
    manager = _stateful_public_manager(hass)
    _seed_room(hass, "bedroom", volume=0.8)
    manager._config = _config()
    now = datetime(2026, 11, 11, 23, tzinfo=UTC)
    monkeypatch.setattr(quiet_hours_runtime.dt_util, "now", lambda: now)
    await _install_services(hass)
    hass.data[OWNERS] = {"entry"}
    hass.data.setdefault(DOMAIN, {})["quiet_hours_manager"] = manager
    await manager.async_reconcile(now=now)
    assert hass.states.get("media_player.bedroom").attributes["volume_level"] == 0.2
    identity = manager._state_entity_id()
    if offline:
        hass.states.async_set(
            "media_player.bedroom", "unavailable", {"volume_level": 0.2}
        )
    await integration._async_release_global_effects(hass, "entry")
    saved = deepcopy(manager._store.async_save.call_args.args[0])
    assert hass.states.get(identity) is None
    if offline:
        control = saved["active"]["controls"]["media_player.bedroom"]
        assert control["original_value"] == 0.8 and control["restoration_pending"]
        hass.states.async_set("media_player.bedroom", "idle", {"volume_level": 0.2})
    else:
        assert hass.states.get("media_player.bedroom").attributes["volume_level"] == 0.8
    assert manager._stopping
    assert not manager._unsubscribers
    replacement = _stateful_public_manager(hass)
    replacement._config = quiet_hours_runtime._config_from_data(saved["config"])
    replacement._active = replacement._normalize_active(saved["active"])
    hass.data[OWNERS].add("entry")
    await replacement.async_reconcile(now=now)
    assert replacement._state_entity_id() == identity
    assert (
        replacement.active["controls"]["media_player.bedroom"]["original_value"] == 0.8
    )
    assert hass.states.get("media_player.bedroom").attributes["volume_level"] == 0.2


async def test_broadcast_suspend_cancels_volatile_work_and_preserves_preferences(
    hass, monkeypatch
):
    from homeassistant.core import Context

    store = SimpleNamespace(
        async_load=AsyncMock(
            return_value={"enabled": True, "builtin_voice_command": False}
        ),
        async_save=AsyncMock(),
    )
    monkeypatch.setattr(intercom, "PropagatingWriteStore", lambda *_: store)
    monkeypatch.setattr(
        hass, "async_create_task", lambda coro: asyncio.create_task(coro)
    )
    hass.data[OWNERS] = {"entry"}
    manager = await intercom.async_get_intercom(hass)
    monkeypatch.setattr(
        manager, "resolve_targets", lambda **_: ["assist_satellite.kitchen"]
    )
    hass.states.get.return_value = SimpleNamespace(state="idle")
    await manager.async_send(
        "Dinner", entity_ids=["assist_satellite.kitchen"], context=Context()
    )
    await asyncio.sleep(0)
    assert manager._drain_tasks
    await integration._async_release_global_effects(hass, "entry")
    assert not manager._drain_tasks and not manager._queues
    assert not manager._expiry_unsubscribers
    assert (
        manager.history()[0]["deliveries"]["assist_satellite.kitchen"]["status"]
        == "expired"
    )
    store.async_save.assert_not_awaited()
    with pytest.raises(HomeAssistantError):
        await manager.async_send(
            "Late", entity_ids=["assist_satellite.kitchen"], context=Context()
        )
    with pytest.raises(HomeAssistantError):
        await manager.async_send_once(
            "Retry",
            operation_id=manager.retry_token() + ":wrong",
            whole_home=False,
            entity_ids=["assist_satellite.kitchen"],
            context=Context(),
        )
    hass.data[OWNERS].add("entry")
    resumed = await intercom.async_get_intercom(hass)
    assert resumed is not manager
    assert resumed.enabled and not resumed.builtin_voice_command
    assert resumed.pending() == [] and resumed.history() == []
    await resumed.async_shutdown()


async def test_public_services_websocket_and_management_stay_suspended(
    hass, monkeypatch
):
    from custom_components.extended_openai_conversation_responses import (
        intercom_services,
        management_permissions,
    )
    from homeassistant.core import Context
    from tests.test_intercom_panel import _call_handler, _connection

    hass.data[OWNERS] = set()
    handlers = {}
    hass.services.has_service.return_value = False
    hass.services.async_register.side_effect = lambda _domain, name, handler, **_: (
        handlers.update({name: handler})
    )
    monkeypatch.setattr(intercom_services, "async_setup_broadcast_api", AsyncMock())
    await intercom_services.async_setup_intercom_services(hass)
    quiet_hours._register_quiet_hours_actions(hass)
    call = SimpleNamespace(
        context=Context(user_id="admin"),
        data={"message": "Late", "whole_home": True, "ttl_seconds": 120},
    )
    hass.auth.async_get_user.return_value = SimpleNamespace(is_admin=True)
    for handler in handlers.values():
        with pytest.raises(HomeAssistantError, match="loaded"):
            await handler(call)
    connection = _connection()
    await _call_handler(
        hass,
        connection,
        {"id": 1, "action": "send", "whole_home": True, "message": "Late"},
    )
    connection.send_error.assert_called_once()
    with pytest.raises(HomeAssistantError, match="loaded"):
        await management_permissions.async_quiet_hours_command(
            hass, True, {"action": "get"}
        )
    assert intercom.DATA_KEY not in hass.data
    assert "quiet_hours_manager" not in hass.data.get(DOMAIN, {})


async def test_cancelled_unload_settles_global_shutdown(hass, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def stop():
        entered.set()
        await release.wait()

    quiet = SimpleNamespace(async_shutdown=stop)
    broadcast = SimpleNamespace(async_shutdown=AsyncMock())
    hass.data[OWNERS] = {"entry"}
    hass.data.setdefault(DOMAIN, {})["quiet_hours_manager"] = quiet
    hass.data[intercom.DATA_KEY] = broadcast
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    monkeypatch.setattr(integration, "async_unload_templates", AsyncMock())
    task = asyncio.create_task(
        integration.async_unload_entry(
            hass, SimpleNamespace(entry_id="entry", subentries={})
        )
    )
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    broadcast.async_shutdown.assert_awaited_once()
    assert intercom.DATA_KEY not in hass.data
    assert hass.data[OWNERS] == set()


async def test_suspension_cancels_running_broadcast_call(hass, monkeypatch):
    from homeassistant.core import Context

    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def announce(*_args, **_kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(
        hass, "async_create_task", lambda coro: asyncio.create_task(coro)
    )
    monkeypatch.setattr(intercom, "IDLE_STABILITY_SECONDS", 0)
    hass.data[OWNERS] = {"entry"}
    manager = intercom.IntercomManager(hass)
    manager._enabled = True
    hass.data[intercom.DATA_KEY] = manager
    hass.states.get.return_value = SimpleNamespace(state="idle")
    hass.services.async_call = announce
    monkeypatch.setattr(
        manager, "resolve_targets", lambda **_: ["assist_satellite.kitchen"]
    )
    await manager.async_send(
        "Dinner", entity_ids=["assist_satellite.kitchen"], context=Context()
    )
    await entered.wait()
    await integration._async_release_global_effects(hass, "entry")
    assert cancelled.is_set()
    assert not manager._drain_tasks and not manager._queues
