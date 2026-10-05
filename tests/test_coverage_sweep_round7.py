"""Seventh residual coverage sweep: compact state machines and validators."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.exceptions import HomeAssistantError


def _valid_delayed_call():
    from custom_components.extended_openai_conversation_responses import delayed_tools

    return {
        "call_id": "call",
        "entry_id": "entry",
        "subentry_id": "agent",
        "tool_name": "tool",
        "arguments": {"value": 1},
        "due_at": "2099-01-01T00:00:00+00:00",
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": delayed_tools._PENDING,
        "retry_count": 0,
        "definition_fingerprint": "a" * 64,
        "user_id": "user",
        "device_id": "device",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.clear(), "not an object|invalid call_id"),
        (lambda value: value.__setitem__("call_id", ""), "invalid call_id"),
        (lambda value: value.__setitem__("arguments", []), "invalid arguments"),
        (lambda value: value.__setitem__("status", "done"), "invalid status"),
        (lambda value: value.__setitem__("retry_count", -1), "invalid retry_count"),
        (lambda value: value.__setitem__("due_at", "bad"), "invalid due_at"),
        (lambda value: value.__setitem__("created_at", "bad"), "invalid created_at"),
        (
            lambda value: value.__setitem__("definition_fingerprint", "bad"),
            "definition_fingerprint",
        ),
        (lambda value: value.__setitem__("user_id", 1), "invalid user_id"),
        (lambda value: value.__setitem__("device_id", 1), "invalid device_id"),
    ],
)
def test_delayed_tool_call_rejects_invalid_persisted_shapes(mutation, message) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    raw = _valid_delayed_call()
    mutation(raw)

    with pytest.raises(ValueError, match=message):
        delayed_tools.DelayedToolCall.from_dict(raw)


def test_delayed_tool_call_round_trip_isolated_arguments() -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    raw = _valid_delayed_call()
    record = delayed_tools.DelayedToolCall.from_dict(raw)

    assert record.as_dict() == raw
    raw["arguments"]["value"] = 2
    assert record.arguments == {"value": 1}


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        (0, 0),
        (5, 5),
        ("00:01:30", 90),
        ({"minutes": 2}, 120),
    ],
)
def test_delayed_tool_delay_normalization(value, seconds: int) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    assert delayed_tools._delay_as_timedelta(value).total_seconds() == seconds


@pytest.mark.parametrize("value", [-1, {"seconds": -1}, "not-a-delay"])
def test_delayed_tool_delay_rejects_negative_or_invalid(value) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    with pytest.raises(HomeAssistantError):
        delayed_tools._delay_as_timedelta(value)


@pytest.mark.asyncio
async def test_delayed_tool_reconcile_drops_interrupted_execution(hass) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    pending = _valid_delayed_call()
    executing = {**_valid_delayed_call(), "call_id": "executing", "status": delayed_tools._EXECUTING}
    manager = delayed_tools.DelayedToolManager(hass)
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value={"calls": [pending, executing]})
    )

    await manager._async_reconcile_failed_save()

    assert set(manager._records) == {"call"}


@pytest.mark.asyncio
async def test_delayed_tool_reconcile_rejects_non_list_calls(hass) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    manager = delayed_tools.DelayedToolManager(hass)
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value={"calls": {}})
    )

    with pytest.raises(ValueError, match="malformed"):
        await manager._async_reconcile_failed_save()


def test_delayed_tool_invalidate_cancels_tasks_and_resets_scheduler(hass) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    manager = delayed_tools.DelayedToolManager(hass)
    task = Mock()
    manager._tasks = {"call": task}
    manager._records = {"call": object()}
    manager._started = True
    manager._setup_complete = True

    manager._invalidate_after_unreadable_store()

    assert manager._records == {}
    assert manager._tasks == {}
    assert manager._started is False
    assert manager._setup_complete is False
    assert task in manager._invalidated_tasks
    task.cancel.assert_called_once()


def test_delayed_tool_handle_started_is_idempotent_and_arms_records(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    manager = delayed_tools.DelayedToolManager(hass)
    manager._records = {"one": object(), "two": object()}
    arm = Mock()
    monkeypatch.setattr(manager, "_arm", arm)

    manager._handle_started()
    manager._handle_started()

    assert manager._started is True
    assert [call.args[0] for call in arm.call_args_list] == ["one", "two"]


def test_delayed_tool_handle_stop_cancels_only_pending_or_missing(hass) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    manager = delayed_tools.DelayedToolManager(hass)
    pending_task = Mock()
    executing_task = Mock()
    missing_task = Mock()
    manager._started = True
    manager._records = {
        "pending": SimpleNamespace(status=delayed_tools._PENDING),
        "executing": SimpleNamespace(status=delayed_tools._EXECUTING),
    }
    manager._tasks = {
        "pending": pending_task,
        "executing": executing_task,
        "missing": missing_task,
    }

    manager._handle_stop()

    assert manager._started is False
    pending_task.cancel.assert_called_once()
    missing_task.cancel.assert_called_once()
    executing_task.cancel.assert_not_called()


def test_intercom_announce_capability_handles_missing_and_feature_bits(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import intercom

    monkeypatch.setattr(intercom.IntercomManager, "_refresh_state_listener", lambda self: None)
    manager = intercom.IntercomManager(hass)

    assert manager._state_announce_capable(None) is False
    assert manager._state_announce_capable(
        SimpleNamespace(attributes={intercom.ATTR_SUPPORTED_FEATURES: 0})
    ) is False
    assert manager._state_announce_capable(
        SimpleNamespace(attributes={intercom.ATTR_SUPPORTED_FEATURES: intercom.ANNOUNCE_FEATURE})
    ) is True


def test_intercom_refresh_state_listener_noops_when_closed_or_unchanged(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import intercom

    monkeypatch.setattr(intercom.IntercomManager, "_refresh_state_listener", lambda self: None)
    manager = intercom.IntercomManager(hass)
    tracker = Mock()
    monkeypatch.setattr(intercom, "async_track_state_change_event", tracker)
    monkeypatch.setattr(manager, "_satellite_entity_ids", Mock(return_value=["assist_satellite.one"]))

    manager._closed = True
    manager._refresh_state_listener()
    tracker.assert_not_called()

    manager._closed = False
    manager._tracked_entities = {"assist_satellite.one"}
    manager._refresh_state_listener()
    tracker.assert_not_called()


def test_intercom_refresh_state_listener_replaces_subscription(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import intercom

    monkeypatch.setattr(intercom.IntercomManager, "_refresh_state_listener", lambda self: None)
    manager = intercom.IntercomManager(hass)
    previous = Mock()
    replacement = Mock()
    manager._unsub_state = previous
    manager._tracked_entities = {"old"}
    monkeypatch.setattr(manager, "_satellite_entity_ids", Mock(return_value=["new"]))
    tracker = Mock(return_value=replacement)
    monkeypatch.setattr(intercom, "async_track_state_change_event", tracker)

    manager._refresh_state_listener()

    previous.assert_called_once()
    assert manager._tracked_entities == {"new"}
    assert manager._unsub_state is replacement
    tracker.assert_called_once()


@pytest.mark.asyncio
async def test_intercom_shutdown_unsubscribes_expires_and_cancels(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import intercom

    monkeypatch.setattr(intercom.IntercomManager, "_refresh_state_listener", lambda self: None)
    manager = intercom.IntercomManager(hass)
    unsub_state = Mock()
    unsub_expiry = Mock()
    manager._unsub_state = unsub_state
    manager._expiry_unsubscribers.add(unsub_expiry)

    delivery = SimpleNamespace(set=Mock())
    item = SimpleNamespace(deliveries={"assist_satellite.one": delivery})
    manager._queues = {"assist_satellite.one": deque([item])}

    task = Mock()
    task.cancel = Mock()
    manager._drain_tasks = {task}
    monkeypatch.setattr(intercom.asyncio, "gather", AsyncMock(return_value=[]))

    await manager.async_shutdown()

    unsub_state.assert_called_once()
    unsub_expiry.assert_called_once()
    delivery.set.assert_called_once_with("expired", "integration_removed")
    task.cancel.assert_called_once()
    assert manager._queues == {}
    assert manager._drain_tasks == set()
    assert manager._draining == set()


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("My Assistant", "my-assistant"),
        ("  !!!  ", "conversation-agent"),
        ("Kitchen / Voice #1", "kitchen-voice-1"),
    ],
)
def test_transfer_safe_title(title: str, expected: str) -> None:
    from custom_components.extended_openai_conversation_responses import transfer

    assert transfer._safe_title(title) == expected


def test_transfer_configuration_mapping_without_device_mapping_keeps_other_fields() -> None:
    from custom_components.extended_openai_conversation_responses import transfer

    config = {
        transfer.CONF_VOICE_DEFAULT_USER_ID: "alice",
        "other": {"nested": True},
    }

    mapped = transfer._map_configuration_users(config, {"alice": "bob"})

    assert mapped[transfer.CONF_VOICE_DEFAULT_USER_ID] == "bob"
    assert mapped["other"] == {"nested": True}
    assert config[transfer.CONF_VOICE_DEFAULT_USER_ID] == "alice"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "saved",
    [
        {"last_checked": 0, "etag": 123},
        {"last_checked": 0, "etag": "x\nmalicious"},
        {
            "last_checked": 0,
            "incompatible_catalog": {"schema_version": "bad", "catalog_version": 1},
        },
    ],
)
async def test_model_catalog_manager_rejects_invalid_saved_metadata(
    hass, monkeypatch, saved
) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    manager = model_catalog_manager.ModelCatalogManager(hass)
    manager.store = SimpleNamespace(async_load=AsyncMock(return_value=saved))
    log = Mock()
    monkeypatch.setattr(model_catalog_manager, "log_handled_failure", log)
    activate = Mock()
    monkeypatch.setattr(model_catalog_manager, "activate_catalog", activate)

    await manager.async_load()

    assert manager.catalog is None
    assert manager.available_catalog is None
    assert manager.last_checked == 0.0
    assert manager.last_error is not None
    log.assert_called()
    activate.assert_called_once_with(None)


@pytest.mark.asyncio
async def test_model_catalog_manager_recent_check_short_circuits(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    manager = model_catalog_manager.ModelCatalogManager(hass)
    manager.last_checked = 1000.0
    monkeypatch.setattr(model_catalog_manager.time, "time", Mock(return_value=1001.0))
    session = Mock()
    monkeypatch.setattr(model_catalog_manager, "async_get_clientsession", session)

    status = await manager.async_check(force=False)

    assert status == manager.status()
    session.assert_not_called()


@pytest.mark.asyncio
async def test_model_catalog_setup_is_idempotent(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    existing = object()
    hass.data[model_catalog_manager.DATA_MANAGER] = existing
    constructor = Mock()
    monkeypatch.setattr(model_catalog_manager, "ModelCatalogManager", constructor)

    await model_catalog_manager.async_setup_model_catalog(hass)

    constructor.assert_not_called()
