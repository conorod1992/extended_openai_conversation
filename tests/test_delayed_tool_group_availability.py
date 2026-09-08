"""Regression coverage for delayed Function Tool group availability."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses import delayed_tools
from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DelayedToolCall,
    DelayedToolManager,
)


def _tool() -> dict:
    return {
        "enabled": True,
        "spec": {
            "name": "control_light",
            "description": "Control a light",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "execute_service_single"},
    }


def _disabled_group() -> dict:
    return {
        "id": "lighting",
        "name": "Lighting",
        "description": "Lighting controls",
        "loading_mode": "always",
        "functions": ["control_light"],
        "enabled": False,
    }


def _record() -> DelayedToolCall:
    now = dt_util.utcnow().isoformat()
    return DelayedToolCall(
        call_id="delayed-group-call",
        entry_id="entry",
        subentry_id="agent",
        tool_name="control_light",
        arguments={"entity_id": "light.kitchen"},
        due_at=now,
        created_at=now,
    )


async def test_due_call_is_cancelled_when_function_group_is_disabled(
    hass, monkeypatch
) -> None:
    """A group disabled after scheduling must block the delayed side effect."""
    manager = DelayedToolManager(hass)
    record = _record()
    manager._records = {record.call_id: record}
    manager._store = SimpleNamespace(async_save=AsyncMock())

    current_tool = _tool()
    latest_data = {"function_groups": [_disabled_group()]}
    latest_subentry = SimpleNamespace(
        subentry_type="conversation",
        data=latest_data,
    )
    latest_entry = SimpleNamespace(
        disabled_by=None,
        subentries={"agent": latest_subentry},
    )
    hass.config_entries.async_get_entry = MagicMock(return_value=latest_entry)

    monkeypatch.setattr(
        delayed_tools,
        "configured_function_tools_from_data",
        lambda _data: [current_tool],
    )

    execute_function_tool = AsyncMock()
    agent = SimpleNamespace(
        hass=hass,
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent", data={}),
        _configured_function_tools_from_data=lambda _data: [current_tool],
        _execute_function_tool=execute_function_tool,
    )
    monkeypatch.setattr(manager, "_resolve_agent", lambda *_args: agent)

    assert await manager._async_execute_due(record.call_id) is False

    execute_function_tool.assert_not_awaited()
    assert record.call_id not in manager._records
    manager._store.async_save.assert_awaited_once_with({"calls": []})
