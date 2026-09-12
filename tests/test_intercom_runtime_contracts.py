"""Intercom public-boundary, cleanup, and lifecycle contracts."""

from __future__ import annotations

import asyncio
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import (
    intercom,
    intercom_panel,
    intercom_services,
)
from custom_components.extended_openai_conversation_responses.intercom import IntercomManager


@pytest.mark.asyncio
async def test_broadcast_api_registration_is_idempotent(hass) -> None:
    """Repeated setup registers the typed WebSocket command exactly once."""
    register = MagicMock()

    with patch.object(intercom_panel.websocket_api, "async_register_command", register):
        await intercom_panel.async_setup_broadcast_api(hass)
        await intercom_panel.async_setup_broadcast_api(hass)

    register.assert_called_once_with(hass, intercom_panel.websocket_broadcast)


@pytest.mark.asyncio
async def test_broadcast_service_registration_is_idempotent(hass) -> None:
    """Repeated integration setup never replaces or duplicates the service handler."""
    hass.services.has_service.side_effect = [False, True]
    panel_setup = AsyncMock()

    with patch.object(intercom_services, "async_setup_broadcast_api", panel_setup):
        await intercom_services.async_setup_intercom_services(hass)
        await intercom_services.async_setup_intercom_services(hass)

    assert hass.services.async_register.call_count == 1
    register_args = hass.services.async_register.call_args.args
    assert register_args[:2] == (intercom_services.DOMAIN, intercom_services.SERVICE_BROADCAST)
    assert callable(register_args[2])
    assert panel_setup.await_count == 2


@pytest.mark.asyncio
async def test_service_permission_denial_stops_before_send(hass) -> None:
    """A denied service caller cannot queue or mutate any Broadcast state."""
    hass.services.has_service.return_value = False
    manager = SimpleNamespace(async_send=AsyncMock())
    denied = AsyncMock(side_effect=HomeAssistantError("not authorized"))

    with (
        patch.object(intercom_services, "async_setup_broadcast_api", AsyncMock()),
        patch.object(intercom_services, "async_get_intercom", AsyncMock(return_value=manager)),
        patch.object(intercom_services, "async_authorized_broadcast_targets", denied),
    ):
        await intercom_services.async_setup_intercom_services(hass)
        handler = hass.services.async_register.call_args.args[2]
        service_call = SimpleNamespace(
            context=SimpleNamespace(user_id="restricted-user"),
            data={
                "message": "Dinner is ready",
                "whole_home": False,
                "entity_id": ["assist_satellite.kitchen"],
                "ttl_seconds": 120,
            },
        )

        with pytest.raises(HomeAssistantError, match="not authorized"):
            await handler(service_call)

    denied.assert_awaited_once()
    manager.async_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_websocket_permission_denial_returns_one_error_without_send(hass) -> None:
    """Frontend authorization failure yields one controlled error and no queue mutation."""
    manager = SimpleNamespace(async_send=AsyncMock())
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=False),
        context=MagicMock(return_value=SimpleNamespace(user_id="restricted-user")),
        send_result=MagicMock(),
        send_error=MagicMock(),
    )

    with (
        patch.object(intercom_panel, "async_get_intercom", AsyncMock(return_value=manager)),
        patch.object(
            intercom_panel,
            "async_authorized_broadcast_targets",
            AsyncMock(side_effect=HomeAssistantError("target permission denied")),
        ) as authorize,
    ):
        await unwrap(intercom_panel.websocket_broadcast)(
            hass,
            connection,
            {
                "id": 7,
                "type": intercom_panel.WS_BROADCAST,
                "action": "send",
                "message": "Dinner is ready",
                "whole_home": False,
                "entity_ids": ["assist_satellite.kitchen"],
            },
        )

    authorize.assert_awaited_once()
    manager.async_send.assert_not_awaited()
    connection.send_result.assert_not_called()
    connection.send_error.assert_called_once_with(
        7, "invalid_request", "target permission denied"
    )


@pytest.mark.asyncio
async def test_websocket_non_admin_cannot_change_enabled_state(hass) -> None:
    """Administrative gating occurs before the persisted enabled state is mutated."""
    manager = SimpleNamespace(enabled=True, async_set_enabled=AsyncMock())
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=False),
        send_result=MagicMock(),
        send_error=MagicMock(),
    )

    with patch.object(
        intercom_panel, "async_get_intercom", AsyncMock(return_value=manager)
    ):
        await unwrap(intercom_panel.websocket_broadcast)(
            hass,
            connection,
            {
                "id": 8,
                "type": intercom_panel.WS_BROADCAST,
                "action": "set_enabled",
                "enabled": False,
            },
        )

    manager.async_set_enabled.assert_not_awaited()
    connection.send_result.assert_not_called()
    connection.send_error.assert_called_once_with(
        8,
        "invalid_request",
        "Administrator permission is required to change Broadcast settings",
    )


@pytest.mark.asyncio
async def test_unknown_target_is_side_effect_free(hass, monkeypatch) -> None:
    """Target resolution failure cannot create history or queue entries."""
    manager = IntercomManager(hass)
    manager._enabled = True
    monkeypatch.setattr(manager, "resolve_targets", lambda **_kwargs: [])

    with pytest.raises(
        HomeAssistantError,
        match="No matching announcement-capable Assist satellites found",
    ):
        await manager.async_send(
            "Dinner is ready", entity_ids=["assist_satellite.missing"]
        )

    assert manager.history() == []
    assert manager._queues == {}
    assert manager._draining == set()


@pytest.mark.asyncio
async def test_announcement_failure_cleans_queue_and_marks_delivery_failed(
    hass, monkeypatch
) -> None:
    """A downstream announce failure is terminal and leaves no active queue state."""
    manager = IntercomManager(hass)
    manager._enabled = True
    monkeypatch.setattr(
        manager, "resolve_targets", lambda **_kwargs: ["assist_satellite.kitchen"]
    )
    monkeypatch.setattr(manager, "_schedule_drain", lambda _entity_id: None)
    hass.states.get.return_value = SimpleNamespace(state="idle")
    hass.services.async_call = AsyncMock(side_effect=RuntimeError("speaker failed"))
    monkeypatch.setattr(intercom.asyncio, "sleep", AsyncMock(return_value=None))

    result = await manager.async_send(
        "Dinner is ready", entity_ids=["assist_satellite.kitchen"]
    )
    await manager._async_drain("assist_satellite.kitchen")

    delivery = manager.history()[0]["deliveries"]["assist_satellite.kitchen"]
    assert result["id"] == manager.history()[0]["id"]
    assert delivery["status"] == "failed"
    assert delivery["detail"] == "RuntimeError"
    assert manager._queues == {}
    assert manager._draining == set()


@pytest.mark.asyncio
async def test_same_target_announcements_are_serialized_in_queue_order(
    hass, monkeypatch
) -> None:
    """Concurrent sends to one satellite are announced sequentially, never overlapped."""
    manager = IntercomManager(hass)
    manager._enabled = True
    monkeypatch.setattr(
        manager, "resolve_targets", lambda **_kwargs: ["assist_satellite.kitchen"]
    )
    monkeypatch.setattr(manager, "_schedule_drain", lambda _entity_id: None)
    hass.states.get.return_value = SimpleNamespace(state="idle")
    monkeypatch.setattr(intercom.asyncio, "sleep", AsyncMock(return_value=None))

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    active = 0
    maximum_active = 0
    announced: list[str] = []

    async def announce(_domain, _service, data, **_kwargs):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        announced.append(data["message"])
        if data["message"] == "First":
            first_started.set()
            await release_first.wait()
        active -= 1

    hass.services.async_call = AsyncMock(side_effect=announce)

    await manager.async_send("First", entity_ids=["assist_satellite.kitchen"])
    await manager.async_send("Second", entity_ids=["assist_satellite.kitchen"])

    drain = asyncio.create_task(manager._async_drain("assist_satellite.kitchen"))
    await first_started.wait()
    assert announced == ["First"]
    release_first.set()
    await drain

    assert announced == ["First", "Second"]
    assert maximum_active == 1
    assert manager._queues == {}
    assert manager._draining == set()
    assert [
        item["deliveries"]["assist_satellite.kitchen"]["status"]
        for item in manager.history()
    ] == ["delivered", "delivered"]
