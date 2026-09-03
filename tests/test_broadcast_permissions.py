"""Tests for Broadcast Home Assistant permission enforcement."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import (
    intercom_panel,
    intercom_permissions,
    intercom_services,
)


@pytest.mark.asyncio
async def test_authorized_targets_resolve_once_and_check_exact_entities(
    hass, monkeypatch
) -> None:
    manager = SimpleNamespace(
        resolve_targets=MagicMock(
            return_value=["assist_satellite.kitchen", "assist_satellite.hall"]
        )
    )
    require_control = AsyncMock()
    monkeypatch.setattr(
        intercom_permissions,
        "async_require_control_permission",
        require_control,
    )
    context = Context(user_id="normal-user")

    targets = await intercom_permissions.async_authorized_broadcast_targets(
        hass,
        manager,
        context=context,
        whole_home=True,
        area_ids=["downstairs"],
        origin_device_id="origin-device",
    )

    assert targets == ["assist_satellite.kitchen", "assist_satellite.hall"]
    manager.resolve_targets.assert_called_once_with(
        whole_home=True,
        entity_ids=[],
        device_ids=[],
        area_ids=["downstairs"],
        floor_ids=[],
        label_ids=[],
        origin_entity_id=None,
        origin_device_id="origin-device",
    )
    require_control.assert_awaited_once_with(hass, targets, context=context)


@pytest.mark.asyncio
async def test_authorized_targets_reject_empty_resolution_before_permission_check(
    hass, monkeypatch
) -> None:
    manager = SimpleNamespace(resolve_targets=MagicMock(return_value=[]))
    require_control = AsyncMock()
    monkeypatch.setattr(
        intercom_permissions,
        "async_require_control_permission",
        require_control,
    )

    with pytest.raises(HomeAssistantError, match="No matching announcement-capable"):
        await intercom_permissions.async_authorized_broadcast_targets(
            hass,
            manager,
            context=Context(user_id="normal-user"),
            entity_ids=["assist_satellite.missing"],
        )

    require_control.assert_not_awaited()


@pytest.mark.asyncio
async def test_websocket_broadcast_queues_only_authorized_targets(hass, monkeypatch) -> None:
    manager = SimpleNamespace(async_send=AsyncMock(return_value={"id": "broadcast-1"}))
    monkeypatch.setattr(
        intercom_panel,
        "async_get_intercom",
        AsyncMock(return_value=manager),
    )
    authorized = AsyncMock(return_value=["assist_satellite.kitchen"])
    monkeypatch.setattr(
        intercom_panel,
        "async_authorized_broadcast_targets",
        authorized,
    )
    context = Context(user_id="normal-user")
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=False),
        context=lambda _msg: context,
        send_result=MagicMock(),
        send_error=MagicMock(),
    )
    message = {
        "id": 1,
        "action": "send",
        "message": "Dinner is ready",
        "whole_home": False,
        "entity_ids": ["assist_satellite.kitchen"],
    }

    await intercom_panel.websocket_broadcast.__wrapped__(hass, connection, message)

    authorized.assert_awaited_once_with(
        hass,
        manager,
        context=context,
        whole_home=False,
        entity_ids=["assist_satellite.kitchen"],
    )
    manager.async_send.assert_awaited_once_with(
        "Dinner is ready",
        entity_ids=["assist_satellite.kitchen"],
        source="frontend",
    )
    connection.send_result.assert_called_once_with(1, {"id": "broadcast-1"})
    connection.send_error.assert_not_called()


@pytest.mark.asyncio
async def test_broadcast_service_preserves_caller_context_for_authorization(
    hass, monkeypatch
) -> None:
    hass.services.has_service.return_value = False
    monkeypatch.setattr(
        intercom_services,
        "async_setup_broadcast_api",
        AsyncMock(),
    )
    manager = SimpleNamespace(async_send=AsyncMock(return_value={"id": "broadcast-2"}))
    monkeypatch.setattr(
        intercom_services,
        "async_get_intercom",
        AsyncMock(return_value=manager),
    )
    authorized = AsyncMock(return_value=["assist_satellite.kitchen"])
    monkeypatch.setattr(
        intercom_services,
        "async_authorized_broadcast_targets",
        authorized,
    )

    await intercom_services.async_setup_intercom_services(hass)
    handler = hass.services.async_register.call_args.args[2]
    context = Context(user_id="normal-user")
    call = SimpleNamespace(
        context=context,
        data={
            "message": "Dinner is ready",
            "whole_home": False,
            "entity_id": ["assist_satellite.kitchen"],
            "ttl_seconds": 120,
        },
    )

    result = await handler(call)

    assert result == {"id": "broadcast-2"}
    authorized.assert_awaited_once_with(
        hass,
        manager,
        context=context,
        whole_home=False,
        entity_ids=["assist_satellite.kitchen"],
        device_ids=None,
        area_ids=None,
        floor_ids=None,
        label_ids=None,
        origin_entity_id=None,
        origin_device_id=None,
    )
    manager.async_send.assert_awaited_once_with(
        "Dinner is ready",
        entity_ids=["assist_satellite.kitchen"],
        origin_entity_id=None,
        origin_device_id=None,
        source="service",
        ttl_seconds=120,
    )
