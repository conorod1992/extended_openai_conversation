"""Intercom public-boundary, cleanup, and lifecycle contracts."""

from __future__ import annotations

import asyncio
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import Context

import pytest

from custom_components.extended_openai_conversation_responses import (
    intercom,
    intercom_panel,
    intercom_services,
)
from custom_components.extended_openai_conversation_responses.intercom import (
    IntercomManager,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.asyncio
async def test_broadcast_api_registration_is_idempotent(hass) -> None:
    """Repeated setup registers the typed WebSocket command exactly once."""
    register = MagicMock()

    with patch.object(intercom_panel.websocket_api, "async_register_command", register):
        await intercom_panel.async_setup_broadcast_api(hass)
        await intercom_panel.async_setup_broadcast_api(hass)

    register.assert_called_once_with(hass, intercom_panel.websocket_broadcast)


@pytest.mark.asyncio
async def test_broadcast_api_failed_registration_remains_retryable(hass) -> None:
    register = MagicMock(side_effect=[RuntimeError("registration failed"), None])
    with patch.object(intercom_panel.websocket_api, "async_register_command", register):
        with pytest.raises(RuntimeError, match="registration failed"):
            await intercom_panel.async_setup_broadcast_api(hass)
        assert not hass.data.get(intercom_panel._SETUP_KEY)
        await asyncio.gather(
            *(intercom_panel.async_setup_broadcast_api(hass) for _ in range(3))
        )
    assert hass.data[intercom_panel._SETUP_KEY]
    assert register.call_count == 2
    assert all(
        call.args == (hass, intercom_panel.websocket_broadcast)
        for call in register.call_args_list
    )


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
    assert register_args[:2] == (
        intercom_services.DOMAIN,
        intercom_services.SERVICE_BROADCAST,
    )
    assert callable(register_args[2])
    assert panel_setup.await_count == 2


@pytest.mark.asyncio
async def test_service_propagates_queue_admission_denial(hass) -> None:
    """A denied service caller cannot queue or mutate any Broadcast state."""
    hass.services.has_service.return_value = False
    manager = SimpleNamespace(
        async_send=AsyncMock(side_effect=HomeAssistantError("not authorized"))
    )

    with (
        patch.object(intercom_services, "async_setup_broadcast_api", AsyncMock()),
        patch.object(
            intercom_services, "async_get_intercom", AsyncMock(return_value=manager)
        ),
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

    manager.async_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_websocket_queue_admission_denial_returns_one_error(hass) -> None:
    """Frontend authorization failure yields one controlled error and no queue mutation."""
    manager = SimpleNamespace(
        async_send=AsyncMock(side_effect=HomeAssistantError("target permission denied"))
    )
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=False),
        context=MagicMock(return_value=SimpleNamespace(user_id="restricted-user")),
        send_result=MagicMock(),
        send_error=MagicMock(),
    )

    with (
        patch.object(
            intercom_panel, "async_get_intercom", AsyncMock(return_value=manager)
        ),
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

    manager.async_send.assert_awaited_once()
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
        "Dinner is ready", entity_ids=["assist_satellite.kitchen"], context=Context()
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

    await manager.async_send("First", entity_ids=["assist_satellite.kitchen"], context=Context())
    await manager.async_send("Second", entity_ids=["assist_satellite.kitchen"], context=Context())

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


@pytest.mark.asyncio
async def test_stalled_announcement_times_out_and_releases_target_queue(
    hass, monkeypatch
) -> None:
    """A never-returning satellite handler cannot block later queued messages."""
    manager = IntercomManager(hass)
    manager._enabled = True
    monkeypatch.setattr(
        manager, "resolve_targets", lambda **_kwargs: ["assist_satellite.kitchen"]
    )
    monkeypatch.setattr(manager, "_schedule_drain", lambda _entity_id: None)
    monkeypatch.setattr(intercom, "ANNOUNCE_TIMEOUT_SECONDS", 0.01)
    hass.states.get.return_value = SimpleNamespace(state="idle")
    monkeypatch.setattr(intercom.asyncio, "sleep", AsyncMock(return_value=None))
    announced: list[str] = []

    async def announce(_domain, _service, data, **_kwargs):
        announced.append(data["message"])
        if data["message"] == "Stalled":
            await asyncio.Event().wait()

    hass.services.async_call = AsyncMock(side_effect=announce)
    await manager.async_send("Stalled", entity_ids=["assist_satellite.kitchen"], context=Context())
    await manager.async_send("Next", entity_ids=["assist_satellite.kitchen"], context=Context())

    await asyncio.wait_for(manager._async_drain("assist_satellite.kitchen"), timeout=1)

    deliveries = [
        item["deliveries"]["assist_satellite.kitchen"] for item in manager.history()
    ]
    assert announced == ["Stalled", "Next"]
    assert deliveries[1]["status"] == "failed"
    assert deliveries[1]["detail"] == "TimeoutError"
    assert deliveries[0]["status"] == "delivered"
    assert manager._queues == {}


@pytest.mark.asyncio
async def test_queued_announcements_keep_each_initiating_context(
    hass, monkeypatch
) -> None:
    """Messages from different users retain their own HA context in queue order."""
    manager = IntercomManager(hass)
    manager._enabled = True
    monkeypatch.setattr(
        manager, "resolve_targets", lambda **_kwargs: ["assist_satellite.kitchen"]
    )
    monkeypatch.setattr(manager, "_schedule_drain", lambda _entity_id: None)
    hass.states.get.return_value = SimpleNamespace(state="idle")
    monkeypatch.setattr(intercom.asyncio, "sleep", AsyncMock(return_value=None))
    first_context = SimpleNamespace(user_id="first-user")
    second_context = SimpleNamespace(user_id="second-user")

    await manager.async_send(
        "First", entity_ids=["assist_satellite.kitchen"], context=first_context
    )
    await manager.async_send(
        "Second", entity_ids=["assist_satellite.kitchen"], context=second_context
    )
    await manager._async_drain("assist_satellite.kitchen")

    assert [
        call.kwargs["context"] for call in hass.services.async_call.await_args_list
    ] == [
        first_context,
        second_context,
    ]
