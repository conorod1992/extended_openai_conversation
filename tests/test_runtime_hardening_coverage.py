"""Focused resilience coverage for runtime hardening guards."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation,
    guest_mode,
    runtime_hardening,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)


@pytest.mark.asyncio
async def test_guest_mode_storage_failure_remains_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSchedule:
        def __init__(self, **values: Any) -> None:
            self.__dict__.update(values)

    class FakeManager(guest_mode.GuestModeManager):
        def __init__(self):
            self._initialization_lock = asyncio.Lock()

    store = SimpleNamespace(
        async_load=AsyncMock(
            side_effect=[
                OSError("storage unavailable"),
                {
                    "schedule": {
                        "active_from": "2026-09-13T10:00:00+00:00",
                        "active_until": None,
                    }
                },
            ]
        )
    )
    manager = FakeManager()
    manager.hass = SimpleNamespace()
    manager._store = store
    manager._initialized = False
    manager._schedule = None

    monkeypatch.setattr(guest_mode, "GuestModeManager", FakeManager)
    monkeypatch.setattr(guest_mode, "GuestModeSchedule", FakeSchedule)
    parse_timestamp = Mock()
    monkeypatch.setattr(guest_mode, "_parse_timestamp", parse_timestamp)

    with pytest.raises(OSError, match="storage unavailable"):
        await manager.async_initialize()
    assert manager._initialized is False

    await manager.async_initialize()
    assert manager._initialized is True
    assert manager._schedule.active_from == "2026-09-13T10:00:00+00:00"
    parse_timestamp.assert_called_once()

    await manager.async_initialize()
    assert store.async_load.await_count == 2


@pytest.mark.asyncio
async def test_guest_mode_malformed_state_is_ignored_but_initialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSchedule:
        def __init__(self, **values: Any) -> None:
            self.__dict__.update(values)

    class FakeManager(guest_mode.GuestModeManager):
        def __init__(self):
            self._initialization_lock = asyncio.Lock()

    manager = FakeManager()
    manager.hass = SimpleNamespace()
    manager._store = SimpleNamespace(
        async_load=AsyncMock(
            return_value={
                "schedule": {"active_from": "not-a-date", "active_until": None}
            }
        )
    )
    manager._initialized = False
    manager._schedule = object()

    monkeypatch.setattr(guest_mode, "GuestModeManager", FakeManager)
    monkeypatch.setattr(guest_mode, "GuestModeSchedule", FakeSchedule)
    monkeypatch.setattr(
        guest_mode,
        "_parse_timestamp",
        Mock(side_effect=ValueError("invalid timestamp")),
    )

    await manager.async_initialize()

    assert manager._initialized is True
    assert manager._schedule is None


@pytest.mark.asyncio
async def test_tool_result_guard_bounds_outermost_string_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeEntity(conversation.ExtendedOpenAIAgentEntity):
        async def _async_dispatch_function_tool(self, *_args: Any) -> Any:
            return SimpleNamespace(
                tool_result={
                    "result": "x"
                    * (runtime_hardening.MAX_MODEL_TOOL_RESULT_CHARACTERS + 100)
                }
            )

    content = await object.__new__(FakeEntity)._execute_function_tool({}, {}, None, [])
    result = tool_result_data(content)["result"]
    assert len(result) <= runtime_hardening.MAX_MODEL_TOOL_RESULT_CHARACTERS
    assert runtime_hardening._TOOL_RESULT_TRUNCATION_LABEL in result
