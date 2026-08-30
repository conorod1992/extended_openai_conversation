"""Regression tests for durable delayed Function Tools."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DelayedToolCall,
    DelayedToolManager,
    _DELAYED_EXECUTION_MARKER,
    _EXECUTING,
    _delay_as_timedelta,
)


def _entity(hass):
    return SimpleNamespace(
        hass=hass,
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent"),
    )


def _record(*, status: str = "pending", retry_count: int = 0) -> DelayedToolCall:
    now = dt_util.utcnow()
    return DelayedToolCall(
        call_id="call-1",
        entry_id="entry",
        subentry_id="agent",
        tool_name="control_light",
        arguments={"delay": {"seconds": 5}, "value": 1},
        due_at=(now - timedelta(seconds=1)).isoformat(),
        created_at=(now - timedelta(seconds=6)).isoformat(),
        user_id="user-1",
        device_id="device-1",
        status=status,
        retry_count=retry_count,
    )


def test_delay_normalization_accepts_ha_time_period_shapes() -> None:
    """Delay persistence uses the same shapes accepted by HA script delays."""
    assert _delay_as_timedelta({"minutes": 1, "seconds": 5}) == timedelta(seconds=65)
    assert _delay_as_timedelta("00:00:03") == timedelta(seconds=3)

    with pytest.raises(HomeAssistantError, match="Invalid Function Tool delay"):
        _delay_as_timedelta("not-a-duration")


async def test_schedule_is_persisted_before_becoming_live(hass) -> None:
    """A failed Store write must not leave a volatile scheduled action behind."""
    manager = DelayedToolManager(hass)
    manager._setup_complete = True
    manager._store = SimpleNamespace(async_save=AsyncMock())
    context = SimpleNamespace(
        context=Context(user_id="user-1"), device_id="device-1"
    )

    record = await manager.async_schedule(
        _entity(hass),
        "control_light",
        {"delay": {"seconds": 30}, "value": 1},
        context,
    )

    manager._store.async_save.assert_awaited_once()
    assert manager._records[record.call_id] == record
    assert record.user_id == "user-1"
    assert record.device_id == "device-1"

    failing = DelayedToolManager(hass)
    failing._setup_complete = True
    failing._store = SimpleNamespace(
        async_save=AsyncMock(side_effect=OSError("storage unavailable"))
    )
    with pytest.raises(OSError, match="storage unavailable"):
        await failing.async_schedule(
            _entity(hass),
            "control_light",
            {"delay": {"seconds": 30}},
            context,
        )
    assert failing._records == {}


async def test_due_call_uses_current_tool_and_current_exposure(hass, monkeypatch) -> None:
    """Execution re-resolves the tool and exposure instead of stale snapshots."""
    manager = DelayedToolManager(hass)
    manager._setup_complete = True
    record = _record()
    manager._records = {record.call_id: record}
    manager._store = SimpleNamespace(async_save=AsyncMock())

    latest_subentry = SimpleNamespace(subentry_type="conversation", data={})
    latest_entry = SimpleNamespace(
        disabled_by=None, subentries={"agent": latest_subentry}
    )
    hass.config_entries.async_get_entry = MagicMock(return_value=latest_entry)
    hass.auth.async_get_user = AsyncMock(return_value=SimpleNamespace(is_active=True))

    current_tool = {
        "enabled": True,
        "spec": {"name": "control_light"},
        "function": {"type": "native", "name": "execute_service_single"},
    }
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.configured_function_tools_from_data",
        lambda _data: [current_tool],
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.get_exposed_entities",
        lambda _hass: [{"entity_id": "light.current"}],
    )

    agent = SimpleNamespace(_execute_function_tool=AsyncMock(return_value=object()))
    monkeypatch.setattr(manager, "_resolve_agent", lambda *_args: agent)

    retry = await manager._async_execute_due(record.call_id)

    assert retry is False
    assert record.call_id not in manager._records
    agent._execute_function_tool.assert_awaited_once()
    called_tool, tool_input, context, exposed = agent._execute_function_tool.await_args.args
    assert called_tool is current_tool
    assert isinstance(tool_input, llm.ToolInput)
    assert tool_input.tool_args == record.arguments
    assert context.context.user_id == "user-1"
    assert context.device_id == "device-1"
    assert getattr(context, _DELAYED_EXECUTION_MARKER) is True
    assert exposed == [{"entity_id": "light.current"}]
    assert manager._store.async_save.await_count == 2


async def test_due_call_is_cancelled_when_tool_is_disabled(hass, monkeypatch) -> None:
    """Disabling a Function Tool after scheduling prevents later execution."""
    manager = DelayedToolManager(hass)
    record = _record()
    manager._records = {record.call_id: record}
    manager._store = SimpleNamespace(async_save=AsyncMock())
    hass.config_entries.async_get_entry = MagicMock(
        return_value=SimpleNamespace(
            disabled_by=None,
            subentries={
                "agent": SimpleNamespace(subentry_type="conversation", data={})
            },
        )
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.configured_function_tools_from_data",
        lambda _data: [
            {
                "enabled": False,
                "spec": {"name": "control_light"},
                "function": {"type": "native", "name": "execute_service_single"},
            }
        ],
    )
    resolve_agent = MagicMock()
    monkeypatch.setattr(manager, "_resolve_agent", resolve_agent)

    assert await manager._async_execute_due(record.call_id) is False
    assert record.call_id not in manager._records
    resolve_agent.assert_not_called()


async def test_execution_boundary_failure_never_runs_tool(hass, monkeypatch) -> None:
    """No action runs unless the executing tombstone is durably persisted first."""
    manager = DelayedToolManager(hass)
    record = _record()
    manager._records = {record.call_id: record}
    manager._store = SimpleNamespace(
        async_save=AsyncMock(side_effect=OSError("storage unavailable"))
    )
    hass.config_entries.async_get_entry = MagicMock(
        return_value=SimpleNamespace(
            disabled_by=None,
            subentries={
                "agent": SimpleNamespace(subentry_type="conversation", data={})
            },
        )
    )
    hass.auth.async_get_user = AsyncMock(return_value=SimpleNamespace(is_active=True))
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.configured_function_tools_from_data",
        lambda _data: [
            {
                "enabled": True,
                "spec": {"name": "control_light"},
                "function": {"type": "native", "name": "execute_service_single"},
            }
        ],
    )
    agent = SimpleNamespace(_execute_function_tool=AsyncMock())
    monkeypatch.setattr(manager, "_resolve_agent", lambda *_args: agent)

    assert await manager._async_execute_due(record.call_id) is True
    agent._execute_function_tool.assert_not_awaited()
    assert manager._records[record.call_id].status == "pending"


async def test_interrupted_executing_calls_are_not_replayed(hass) -> None:
    """Startup discards an indeterminate executing record to avoid duplicate effects."""
    executing = _record(status=_EXECUTING)
    pending = DelayedToolCall(
        **{
            **_record().as_dict(),
            "call_id": "call-2",
            "status": "pending",
        }
    )
    manager = DelayedToolManager(hass)
    manager._store = SimpleNamespace(
        async_load=AsyncMock(
            return_value={"calls": [executing.as_dict(), pending.as_dict()]}
        ),
        async_save=AsyncMock(),
    )

    await manager.async_setup()

    assert executing.call_id not in manager._records
    assert manager._records[pending.call_id] == pending
    manager._store.async_save.assert_awaited_once()
