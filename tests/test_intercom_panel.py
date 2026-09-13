"""Tests for the Broadcast WebSocket API."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import intercom_panel


def _connection(*, is_admin: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(is_admin=is_admin),
        send_result=Mock(),
        send_error=Mock(),
        context=Mock(return_value="context"),
    )


async def _call_handler(hass, connection, message: dict) -> None:
    handler = getattr(intercom_panel.websocket_broadcast, "__wrapped__")
    await handler(hass, connection, message)


@pytest.mark.asyncio
async def test_websocket_broadcast_snapshot_returns_manager_state(hass, monkeypatch) -> None:
    """Snapshot exposes the current catalog, history, and permission state."""
    manager = SimpleNamespace(
        enabled=True,
        catalog=Mock(return_value={"satellites": ["kitchen"]}),
        history=Mock(return_value=[{"id": "message-1"}]),
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
            "catalog": {"satellites": ["kitchen"]},
            "history": [{"id": "message-1"}],
        },
    )
    connection.send_error.assert_not_called()


@pytest.mark.asyncio
async def test_websocket_broadcast_set_enabled_updates_manager(hass, monkeypatch) -> None:
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
async def test_websocket_broadcast_set_enabled_requires_boolean(hass, monkeypatch) -> None:
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
async def test_websocket_broadcast_set_enabled_requires_admin(hass, monkeypatch) -> None:
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
    connection.send_error.assert_called_once_with(message["id"], "invalid_request", error)


@pytest.mark.asyncio
async def test_websocket_broadcast_sends_to_whole_home_targets(hass, monkeypatch) -> None:
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
    )
    connection.send_result.assert_called_once_with(7, {"id": "broadcast-1"})
