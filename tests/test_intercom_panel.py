"""Tests for the Broadcast WebSocket API."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import intercom_panel


def _connection(*, is_admin: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(
            is_admin=is_admin,
            permissions=SimpleNamespace(check_entity=Mock(return_value=True)),
        ),
        send_result=Mock(),
        send_error=Mock(),
        context=Mock(return_value="context"),
    )


async def _call_handler(hass, connection, message: dict) -> None:
    handler = intercom_panel.websocket_broadcast.__wrapped__
    await handler(hass, connection, message)


@pytest.mark.asyncio
async def test_websocket_broadcast_snapshot_returns_manager_state(
    hass, monkeypatch
) -> None:
    """Snapshot exposes the current catalog, history, and permission state."""
    manager = SimpleNamespace(
        enabled=True,
        pending=Mock(return_value=[]),
        catalog=Mock(return_value={"satellites": [{"id": "assist_satellite.kitchen"}]}),
        history=Mock(
            return_value=[
                {
                    "id": "message-1",
                    "targets": ["assist_satellite.kitchen"],
                    "origin_device_id": None,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        intercom_panel, "async_get_intercom", AsyncMock(return_value=manager)
    )
    connection = _connection(is_admin=False)

    await _call_handler(hass, connection, {"id": 1, "action": "snapshot"})

    connection.send_result.assert_called_once_with(
        1,
        {
            "enabled": True,
            "can_manage": False,
            "pending": [],
            "catalog": {"satellites": [{"id": "assist_satellite.kitchen"}]},
            "history": [
                {
                    "id": "message-1",
                    "targets": ["assist_satellite.kitchen"],
                    "origin_device_id": None,
                }
            ],
        },
    )
    connection.send_error.assert_not_called()


@pytest.mark.asyncio
async def test_websocket_broadcast_set_enabled_updates_manager(
    hass, monkeypatch
) -> None:
    """Administrators can change the Broadcast enabled state."""
    manager = SimpleNamespace(enabled=False, async_set_enabled=AsyncMock())
    monkeypatch.setattr(
        intercom_panel, "async_get_intercom", AsyncMock(return_value=manager)
    )
    connection = _connection()

    await _call_handler(
        hass,
        connection,
        {"id": 2, "action": "set_enabled", "enabled": False},
    )

    manager.async_set_enabled.assert_awaited_once_with(False)
    connection.send_result.assert_called_once_with(2, {"enabled": False})
    connection.send_error.assert_not_called()


@pytest.mark.asyncio
async def test_websocket_broadcast_set_enabled_requires_boolean(
    hass, monkeypatch
) -> None:
    """Reject malformed enabled values before mutating the manager."""
    manager = SimpleNamespace(enabled=True, async_set_enabled=AsyncMock())
    monkeypatch.setattr(
        intercom_panel, "async_get_intercom", AsyncMock(return_value=manager)
    )
    connection = _connection()

    await _call_handler(
        hass,
        connection,
        {"id": 3, "action": "set_enabled", "enabled": "yes"},
    )

    manager.async_set_enabled.assert_not_awaited()
    connection.send_error.assert_called_once_with(
        3, "invalid_request", "enabled must be true or false"
    )


@pytest.mark.asyncio
async def test_websocket_broadcast_set_enabled_requires_admin(
    hass, monkeypatch
) -> None:
    """Reject settings changes from non-administrators."""
    manager = SimpleNamespace(enabled=True, async_set_enabled=AsyncMock())
    monkeypatch.setattr(
        intercom_panel, "async_get_intercom", AsyncMock(return_value=manager)
    )
    connection = _connection(is_admin=False)

    await _call_handler(
        hass,
        connection,
        {"id": 4, "action": "set_enabled", "enabled": False},
    )

    manager.async_set_enabled.assert_not_awaited()
    connection.send_error.assert_called_once_with(
        4,
        "invalid_request",
        "Administrator permission is required to change Broadcast settings",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "error"),
    [
        ({"id": 5, "action": "send", "message": "   "}, "Message cannot be empty"),
        (
            {"id": 6, "action": "send", "message": "Dinner is ready"},
            "Choose at least one Assist satellite or Whole home",
        ),
    ],
)
async def test_websocket_broadcast_rejects_invalid_send_requests(
    hass, monkeypatch, message, error
) -> None:
    """Reject invalid send payloads before resolving or queueing targets."""
    manager = SimpleNamespace()
    monkeypatch.setattr(
        intercom_panel, "async_get_intercom", AsyncMock(return_value=manager)
    )
    authorized_targets = AsyncMock()
    monkeypatch.setattr(
        intercom_panel,
        "async_authorized_broadcast_targets",
        authorized_targets,
    )
    connection = _connection()

    await _call_handler(hass, connection, message)

    authorized_targets.assert_not_awaited()
    connection.send_error.assert_called_once_with(
        message["id"], "invalid_request", error
    )


@pytest.mark.asyncio
async def test_websocket_broadcast_sends_to_whole_home_targets(
    hass, monkeypatch
) -> None:
    """Whole-home sends use the authorized resolved targets and caller context."""
    manager = SimpleNamespace(async_send=AsyncMock(return_value={"id": "broadcast-1"}))
    monkeypatch.setattr(
        intercom_panel, "async_get_intercom", AsyncMock(return_value=manager)
    )
    authorized_targets = AsyncMock(return_value=["assist_satellite.kitchen"])
    monkeypatch.setattr(
        intercom_panel,
        "async_authorized_broadcast_targets",
        authorized_targets,
    )
    connection = _connection()

    await _call_handler(
        hass,
        connection,
        {
            "id": 7,
            "action": "send",
            "message": " Dinner is ready ",
            "whole_home": True,
        },
    )

    connection.context.assert_called_once_with(
        {
            "id": 7,
            "action": "send",
            "message": " Dinner is ready ",
            "whole_home": True,
        }
    )
    authorized_targets.assert_awaited_once_with(
        hass,
        manager,
        context="context",
        whole_home=True,
        entity_ids=[],
    )
    manager.async_send.assert_awaited_once_with(
        "Dinner is ready",
        entity_ids=["assist_satellite.kitchen"],
        source="frontend",
        context="context",
    )
    connection.send_result.assert_called_once_with(7, {"id": "broadcast-1"})


@pytest.mark.parametrize("is_admin", [False, True])
async def test_pending_snapshot_uses_same_privacy_boundary_as_history(
    hass, monkeypatch, is_admin
):
    visible = {
        "id": "visible",
        "targets": ["assist_satellite.hall"],
        "deliveries": {"assist_satellite.hall": {"status": "queued_busy"}},
        "origin_device_id": "private-device",
    }
    hidden = {
        "id": "hidden",
        "targets": ["assist_satellite.private"],
        "deliveries": {"assist_satellite.private": {"status": "delivering"}},
    }
    hidden_origin = {
        **visible,
        "id": "hidden-origin",
        "origin_entity_id": "assist_satellite.private",
    }
    items = [visible, hidden, hidden_origin]
    manager = SimpleNamespace(
        enabled=True,
        catalog=Mock(return_value={"satellites": []}),
        history=lambda: items,
        pending=lambda: items,
    )
    monkeypatch.setattr(
        intercom_panel, "async_get_intercom", AsyncMock(return_value=manager)
    )
    connection = _connection(is_admin=is_admin)
    connection.user.permissions.check_entity.side_effect = lambda entity, _: (
        entity != "assist_satellite.private"
    )
    await _call_handler(hass, connection, {"id": 1, "action": "snapshot"})
    snapshot = connection.send_result.call_args.args[1]
    expected = items if is_admin else [{**visible, "origin_device_id": None}]
    assert snapshot["history"] == snapshot["pending"] == expected
    assert visible["origin_device_id"] == "private-device"
