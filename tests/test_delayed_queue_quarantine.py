"""Oversized persisted queues disable scheduling without blocking EOAI startup."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import custom_components.extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import (
    delayed_tools as delayed,
    transfer_staging,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from homeassistant.core import CoreState
from homeassistant.exceptions import HomeAssistantError
from tests.test_audit_work_admission import agent, scheduler


async def oversized_manager(hass, monkeypatch, kind="global"):
    existing = scheduler(hass)
    record = await existing.async_schedule(agent(), "tool", {"delay": 60}, None)
    rows = [replace(record, call_id=str(index)).as_dict() for index in range(3)]
    if kind == "global":
        monkeypatch.setattr(delayed, "MAX_DELAYED_CALLS", 2)
    elif kind == "agent":
        monkeypatch.setattr(delayed, "MAX_DELAYED_CALLS_PER_AGENT", 2)
    else:
        rows[0]["arguments"]["text"] = "x" * delayed.MAX_DELAYED_ARGUMENT_BYTES
    data = {"calls": rows, "extra": "preserved"}
    saved = deepcopy(data)

    async def save(value):
        saved.clear()
        saved.update(deepcopy(value))

    store = SimpleNamespace(
        async_load=AsyncMock(side_effect=lambda: deepcopy(saved)),
        async_save=AsyncMock(side_effect=save),
    )
    monkeypatch.setattr(delayed, "PropagatingWriteStore", lambda *args, **kwargs: store)
    monkeypatch.setattr(delayed, "_async_repair_private_store_mode", AsyncMock())
    manager = delayed.DelayedToolManager(hass)
    hass.data.setdefault(DOMAIN, {})[delayed.DATA_DELAYED_TOOL_MANAGER] = manager
    return manager, saved, data


@pytest.mark.parametrize("kind", ["global", "agent", "arguments"])
@pytest.mark.parametrize("running", [True, False])
async def test_quarantine_preserves_store_and_rejects_all_scheduling(
    hass, monkeypatch, caplog, kind, running
):
    manager, saved, original = await oversized_manager(hass, monkeypatch, kind)
    hass.state = CoreState.running if running else CoreState.not_running
    await delayed.async_setup_delayed_tools(hass)
    assert manager._quarantined and manager._setup_complete
    assert not manager._started and not manager._records and not manager._tasks
    assert "Saved jobs are preserved" in caplog.text
    manager._handle_started()
    manager._arm("0")
    await manager.async_setup()
    with pytest.raises(HomeAssistantError, match="saved queue"):
        await manager.async_schedule(agent(), "tool", {"delay": 60}, None)
    assert not manager._started and not manager._tasks
    manager._store.async_save.assert_not_called()
    assert saved == original


async def test_management_deletion_preserves_siblings_and_requires_restart(
    hass, monkeypatch
):
    manager, saved, _ = await oversized_manager(hass, monkeypatch)
    saved["calls"][2]["subentry_id"] = "other"
    # Keep this agent over its own limit while allowing the sibling to survive.
    monkeypatch.setattr(delayed, "MAX_DELAYED_CALLS", 256)
    monkeypatch.setattr(delayed, "MAX_DELAYED_CALLS_PER_AGENT", 1)
    sibling = deepcopy(saved["calls"][2])
    await manager.async_setup()
    await delayed.async_remove_stored_agent_calls(hass, "entry", "one")
    assert saved == {"calls": [sibling], "extra": "preserved"}
    assert manager._quarantined and not manager._tasks
    await manager.async_setup()
    with pytest.raises(HomeAssistantError, match="saved queue"):
        await manager.async_schedule(agent(), "tool", {"delay": 60}, None)
    # A new process can recover the now admissible queue, without premature arming.
    hass.state = CoreState.not_running
    recovered = delayed.DelayedToolManager(hass)
    await recovered.async_setup()
    assert not recovered._quarantined
    assert list(recovered._records) == [sibling["call_id"]]
    assert not recovered._tasks


async def test_quarantine_allows_management_registration(hass, monkeypatch):
    manager, saved, original = await oversized_manager(hass, monkeypatch)
    for name in (
        "async_setup_model_catalog",
        "async_get_quiet_hours",
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
    integration.async_setup_management_ui.assert_awaited_once_with(hass)
    integration.async_setup_services.assert_awaited_once_with(hass, {})
    assert manager._quarantined and saved == original


async def test_unrelated_storage_errors_still_propagate(hass, monkeypatch):
    manager, _, _ = await oversized_manager(hass, monkeypatch)
    manager._store.async_load.side_effect = OSError("unreadable store")
    with pytest.raises(OSError, match="unreadable store"):
        await manager.async_setup()
    assert not manager._setup_complete and not manager._quarantined
