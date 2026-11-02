"""Independent fault witnesses for the protected Broadcast admission boundary."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import intercom
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from tests.test_broadcast_admission_contract import admission  # noqa: F401


KITCHEN = "assist_satellite.kitchen"
HALL = "assist_satellite.hall"


def operation(manager, suffix="c"):
    return f"{manager.retry_token()}:{suffix * 32}"


async def send(manager, key, *, whole_home=False):
    return await manager.async_send_once(
        "Dinner",
        operation_id=key,
        whole_home=whole_home,
        entity_ids=[] if whole_home else [KITCHEN],
        context=Context(user_id="owner"),
    )


async def test_cancelled_post_insertion_request_cannot_replay(admission, monkeypatch):
    manager, *_ = admission
    key = operation(manager)
    monkeypatch.setattr(
        manager, "_schedule_drain", Mock(side_effect=asyncio.CancelledError())
    )
    with pytest.raises(asyncio.CancelledError):
        await send(manager, key)
    monkeypatch.setattr(manager, "_schedule_drain", lambda _: None)

    with pytest.raises(intercom.BroadcastRetryError):
        await send(manager, key)
    assert len(manager.history()) == len(manager._queues[KITCHEN]) == 1


async def test_expiry_before_insertion_cannot_consume_or_replay_admission(
    hass, admission, monkeypatch
):
    manager, _, user, _ = admission
    clock = [1000]
    monkeypatch.setattr(intercom.time, "monotonic", lambda: clock[0])
    key = operation(manager)

    async def authorize_after_expiry(_):
        clock[0] = 1601
        return user

    hass.auth.async_get_user.side_effect = authorize_after_expiry
    with pytest.raises(intercom.BroadcastRetryExpired):
        await send(manager, key)
    assert manager.history() == []
    assert manager._queues == {}
    assert manager._retry_admissions == {}

    hass.auth.async_get_user.side_effect = None
    admitted = await send(manager, operation(manager, "d"))
    with pytest.raises(intercom.BroadcastRetryExpired):
        await send(manager, key)
    assert [item["id"] for item in manager.history()] == [admitted["id"]]


async def test_retry_ack_keeps_original_whole_home_recipients_after_catalog_change(
    admission,
):
    manager, states, _, check = admission
    key = operation(manager)
    admitted = await send(manager, key, whole_home=True)
    states.append(
        SimpleNamespace(
            entity_id=HALL,
            attributes={"supported_features": intercom.ANNOUNCE_FEATURE},
        )
    )
    check.return_value = False
    with pytest.raises(HomeAssistantError):
        await send(manager, key, whole_home=True)
    check.return_value = True

    assert await send(manager, key, whole_home=True) == admitted
    assert admitted["targets"] == [KITCHEN]
    assert set(manager._queues) == {KITCHEN}
    assert len(manager.history()) == len(manager._queues[KITCHEN]) == 1
