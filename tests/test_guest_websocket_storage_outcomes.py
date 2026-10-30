"""Guest storage outcomes remain truthful through the public response boundary."""

from copy import deepcopy
from inspect import unwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import management_ui as ui
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestModeManager,
    async_get_guest_mode,
)
from homeassistant.core import CoreState
from tests.test_save_validation_reuse import _hass_with_agent


async def send(hass, entry, subentry, action):
    connection = SimpleNamespace(
        user=SimpleNamespace(id="admin", is_admin=True),
        send_error=Mock(),
        send_result=Mock(),
    )
    await unwrap(ui.websocket_management)(
        hass,
        connection,
        {
            "id": 1,
            "section": "guest_mode",
            "action": action,
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "indefinite": True,
        },
    )
    return connection


@pytest.mark.parametrize("stopping", [False, True])
@pytest.mark.parametrize("after_write", [False, True])
@pytest.mark.parametrize("action", ["update", "disable"])
async def test_guest_write_failure_recovers_before_next_public_command(
    hass,
    monkeypatch,
    caplog,
    stopping,
    after_write,
    action,
):
    _, entry, subentry = _hass_with_agent()
    monkeypatch.setattr(ui, "entry_and_agent", lambda *_: (entry, subentry))
    manager = await async_get_guest_mode(hass, entry.entry_id, subentry.subentry_id)
    if action == "disable":
        await manager.async_update_trusted(indefinite=True)
    else:
        await manager.async_disable_trusted()
    original = deepcopy(await manager.async_backup_data())
    if stopping:
        hass.state = CoreState.stopping
    observed = []

    def failing_listener():
        raise ValueError("PRIVATE-GUEST-OBSERVER")

    manager.async_add_listener(failing_listener)
    manager.async_add_listener(lambda: observed.append(manager.is_active()))
    write = manager._store._write_data

    def fail(data):
        if after_write:
            write(data)
        raise OSError("disk settlement failed")

    monkeypatch.setattr(manager._store, "_write_data", fail)
    reply = await send(hass, entry, subentry, action)
    reply.send_result.assert_not_called()
    assert reply.send_error.call_args.args[1] == "eoai_mutation_uncertain"
    expected_active = (action == "update") if after_write else (action == "disable")
    fresh = GuestModeManager(hass, entry.entry_id, subentry.subentry_id)
    await fresh.async_initialize()
    assert fresh.is_active() is expected_active
    assert manager.is_active() is expected_active
    assert observed == ([expected_active] if after_write else [])
    if not after_write:
        assert await fresh.async_backup_data() == original
    monkeypatch.setattr(manager._store, "_write_data", write)
    next_reply = await send(hass, entry, subentry, action)
    next_reply.send_error.assert_not_called()
    assert next_reply.send_result.call_args.args[1]["status"]["currently_active"] is (
        action == "update"
    )
    restarted = GuestModeManager(hass, entry.entry_id, subentry.subentry_id)
    await restarted.async_initialize()
    assert restarted.is_active() is (action == "update")
    assert "PRIVATE-GUEST-OBSERVER" not in caplog.text


async def test_public_guest_disable_cannot_replace_damaged_schedule(hass, monkeypatch):
    _, entry, subentry = _hass_with_agent()
    monkeypatch.setattr(ui, "entry_and_agent", lambda *_: (entry, subentry))
    seed = GuestModeManager(hass, entry.entry_id, subentry.subentry_id)
    await seed._store.async_save(
        {"schedule": {"active_from": "PRIVATE-DAMAGED-SCHEDULE"}}
    )
    path = Path(seed._store.path)
    damaged = path.read_bytes()
    for _ in range(2):
        reply = await send(hass, entry, subentry, "disable")
        reply.send_result.assert_not_called()
        assert reply.send_error.call_args.args[1] == "eoai_mutation_rejected"
        assert "PRIVATE-DAMAGED-SCHEDULE" not in reply.send_error.call_args.args[2]
        assert path.read_bytes() == damaged
    # Explicit administrator repair, followed by the same public retry path.
    await seed._store.async_save({"schedule": None})
    recovered = await send(hass, entry, subentry, "update")
    recovered.send_error.assert_not_called()
    fresh = GuestModeManager(hass, entry.entry_id, subentry.subentry_id)
    await fresh.async_initialize()
    assert fresh.is_active()
