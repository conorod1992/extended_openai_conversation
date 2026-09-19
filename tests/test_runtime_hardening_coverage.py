"""Focused resilience coverage for runtime hardening guards."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation,
    guest_mode,
    runtime_hardening,
    skills,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)


@pytest.mark.asyncio
async def test_skill_load_publishes_complete_result_and_skips_bad_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeParser:
        @staticmethod
        def parse(content: str, path: Path, _base: Path) -> Any:
            if content == "bad":
                raise ValueError("broken skill")
            if content == "ignore":
                return None
            return SimpleNamespace(name=path.parent.name)

    class FakeManager(skills.SkillManager):
        _instance = None

    hass = SimpleNamespace(
        config=SimpleNamespace(config_dir="/config"),
        data={},
        async_add_executor_job=AsyncMock(
            return_value=[
                (Path("/skills/good/SKILL.md"), "good"),
                (Path("/skills/bad/SKILL.md"), "bad"),
                (Path("/skills/ignored/SKILL.md"), "ignore"),
            ]
        ),
    )
    monkeypatch.setattr(skills, "SkillManager", FakeManager)
    monkeypatch.setattr(skills, "SkillMdParser", FakeParser)

    manager = FakeManager(hass)
    await manager.async_load_skills()

    assert set(manager._skills) == {"good"}
    assert manager._initialized is True


@pytest.mark.asyncio
async def test_skill_first_load_failure_clears_singleton_and_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeParser:
        @staticmethod
        def parse(_content: str, _path: Path, _base: Path) -> Any:
            return None

    class FakeManager(skills.SkillManager):
        _instance = None

    executor = AsyncMock(side_effect=[OSError("disk unavailable"), []])
    hass = SimpleNamespace(
        config=SimpleNamespace(config_dir="/config"),
        data={},
        async_add_executor_job=executor,
    )
    monkeypatch.setattr(skills, "SkillManager", FakeManager)
    monkeypatch.setattr(skills, "SkillMdParser", FakeParser)

    with pytest.raises(OSError, match="disk unavailable"):
        await FakeManager.async_get_instance(hass, "/custom-skills")
    assert FakeManager._instance is None

    manager = await FakeManager.async_get_instance(hass, "/custom-skills")
    assert FakeManager._instance is manager
    assert manager._user_skills_dir == Path("/custom-skills")
    assert manager._initialized is True
    assert FakeManager.get_loaded_instance() is manager
    assert await FakeManager.async_get_instance(hass, "/ignored-after-init") is manager


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
