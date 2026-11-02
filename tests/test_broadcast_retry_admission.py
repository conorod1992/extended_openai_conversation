"""Retry the actual queue admission after transport loss, without replaying it."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import intercom, intercom_panel
from tests.test_broadcast_admission_contract import admission  # noqa: F401


def operation(manager, suffix="a"):
    return f"{manager.retry_token()}:{suffix * 32}"


async def send(manager, key, text="Dinner", user="owner", **kwargs):
    return await manager.async_send_once(text, operation_id=key, whole_home=kwargs.pop("whole_home", False), entity_ids=kwargs.pop("entity_ids", ["assist_satellite.kitchen"]), context=Context(user_id=user), **kwargs)


async def test_lost_websocket_ack_retries_actual_admission_once(hass, admission, monkeypatch):
    manager, *_ = admission
    monkeypatch.setattr(intercom_panel, "async_get_intercom", AsyncMock(return_value=manager))
    connection = SimpleNamespace(context=lambda _: Context(user_id="owner"), send_result=Mock(side_effect=RuntimeError("socket lost")), send_error=Mock())
    message = {"id": 1, "action": "send", "message": "Dinner", "entity_ids": ["assist_satellite.kitchen"], "operation_id": operation(manager)}
    with pytest.raises(RuntimeError, match="socket lost"):
        await intercom_panel.websocket_broadcast.__wrapped__(hass, connection, message)
    connection.send_result.side_effect = None
    await intercom_panel.websocket_broadcast.__wrapped__(hass, connection, message)
    result = connection.send_result.call_args.args[1]
    assert result == {"id": manager.history()[0]["id"], "targets": ["assist_satellite.kitchen"]}
    assert len(manager.history()) == len(manager._queues["assist_satellite.kitchen"]) == 1


async def test_concurrent_retry_and_intentional_repeat(admission):
    manager, *_ = admission
    key = operation(manager)
    first, second = await asyncio.gather(send(manager, key), send(manager, key))
    assert first == second
    first["targets"].clear()
    assert (await send(manager, key)) == second
    assert (await send(manager, operation(manager, "b")))["id"] != second["id"]
    assert len(manager.history()) == 2


async def test_binding_and_owner_isolation(admission):
    manager, *_ = admission
    key = operation(manager)
    await send(manager, key)
    for changed in ({"text": "Other"}, {"whole_home": True}, {"entity_ids": ["assist_satellite.hall"]}):
        with pytest.raises(HomeAssistantError, match="different"):
            await send(manager, key, **changed)
    await send(manager, key, user="other")
    assert len(manager.history()) == 2


async def test_live_capacity_never_evicts_an_accepted_receipt(admission, monkeypatch):
    manager, *_ = admission
    monkeypatch.setattr(intercom, "MAX_RETRY_ADMISSIONS", 1)
    key = operation(manager)
    original = await send(manager, key)
    with pytest.raises(HomeAssistantError, match="capacity"):
        await send(manager, operation(manager, "b"))
    manager._history.clear()
    manager._enabled = False
    assert await send(manager, key) == original


async def test_expired_or_restarted_attempt_cannot_become_new_admission(hass, admission, monkeypatch):
    manager, *_ = admission
    monkeypatch.setattr(intercom.time, "monotonic", lambda: 1000)
    key = operation(manager)
    await send(manager, key)
    monkeypatch.setattr(intercom.time, "monotonic", lambda: 1601)
    manager._retry_admissions.clear()
    with pytest.raises(intercom.BroadcastRetryExpired):
        await send(manager, key)
    other = intercom.IntercomManager(hass)
    with pytest.raises(intercom.BroadcastRetryExpired):
        await send(other, operation(manager))
    assert len(manager.history()) == 1


async def test_preflight_refusal_does_not_consume_receipt_capacity(admission):
    manager, _, _, check = admission
    key = operation(manager)
    check.return_value = False
    with pytest.raises(HomeAssistantError):
        await send(manager, key)
    assert manager._retry_admissions == {}
    check.return_value = True
    await send(manager, key)
    check.return_value = False
    with pytest.raises(HomeAssistantError):
        await send(manager, key)
    assert len(manager.history()) == 1


async def test_post_insertion_failure_retains_uncertainty_without_replay(admission, monkeypatch):
    manager, *_ = admission
    key = operation(manager)
    monkeypatch.setattr(manager, "_schedule_drain", Mock(side_effect=RuntimeError("scheduler unavailable")))
    with pytest.raises(intercom.BroadcastRetryError):
        await send(manager, key)
    with pytest.raises(intercom.BroadcastRetryError):
        await send(manager, key)
    assert len(manager.history()) == 1


@pytest.mark.parametrize("destinations", [["x" * 256], ["assist_satellite.kitchen"] * 33])
async def test_retry_metadata_is_bounded_before_retention(admission, destinations):
    manager, *_ = admission
    with pytest.raises(HomeAssistantError, match="destination limit"):
        await send(manager, operation(manager), entity_ids=destinations)
    assert manager._retry_admissions == {}
    assert manager.history() == []
