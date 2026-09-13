"""Focused resilience coverage for runtime hardening guards."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    delayed_tools,
    guest_mode,
    runtime_hardening,
    skills,
    usage,
)
from custom_components.extended_openai_conversation_responses import conversation


def test_install_runtime_hardening_is_one_shot(monkeypatch: pytest.MonkeyPatch) -> None:
    installers = [Mock() for _ in range(4)]
    monkeypatch.setattr(runtime_hardening, "_INSTALLED", False)
    monkeypatch.setattr(runtime_hardening, "_install_usage_hardening", installers[0])
    monkeypatch.setattr(runtime_hardening, "_install_skill_hardening", installers[1])
    monkeypatch.setattr(runtime_hardening, "_install_guest_mode_hardening", installers[2])
    monkeypatch.setattr(runtime_hardening, "_install_tool_result_hardening", installers[3])

    runtime_hardening.install_runtime_hardening()
    runtime_hardening.install_runtime_hardening()

    for installer in installers:
        installer.assert_called_once_with()


@pytest.mark.asyncio
async def test_usage_guard_serializes_same_agent_and_rebinds_alias(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = 0
    max_active = 0
    release = asyncio.Event()
    entered = asyncio.Event()

    async def original(_hass: Any, _entry: str, _subentry: str) -> str:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active == 1:
            entered.set()
            await release.wait()
        active -= 1
        return "usage"

    monkeypatch.setattr(usage, "async_get_usage", original)
    alias = ModuleType(
        "custom_components.extended_openai_conversation_responses.coverage_alias"
    )
    alias.async_get_usage = original  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, alias.__name__, alias)

    runtime_hardening._install_usage_hardening()
    guarded = usage.async_get_usage
    assert alias.async_get_usage is guarded  # type: ignore[attr-defined]

    first = asyncio.create_task(guarded(hass, "entry", "agent"))
    await entered.wait()
    second = asyncio.create_task(guarded(hass, "entry", "agent"))
    await asyncio.sleep(0)
    assert max_active == 1
    release.set()
    assert await asyncio.gather(first, second) == ["usage", "usage"]
    assert max_active == 1


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

    class FakeManager:
        _instance = None

        def __init__(self, hass: Any) -> None:
            self._hass = hass
            self._skills = {"old": object()}
            self._user_skills_dir: Path | None = None

        @property
        def user_skills_dir(self) -> Path:
            return self._user_skills_dir or Path("/skills")

        def _load_skills_from_dir_sync(self, _path: Path) -> list[tuple[Path, str]]:
            raise AssertionError("executor stub should supply discovery data")

        async def async_load_skills(self) -> None:
            raise AssertionError("installer should replace this method")

        @classmethod
        async def async_get_instance(cls, hass: Any, user_skills_dir: str | None = None):
            raise AssertionError("installer should replace this method")

        @classmethod
        def get_loaded_instance(cls):
            return None

    hass = SimpleNamespace(
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

    runtime_hardening._install_skill_hardening()
    manager = FakeManager(hass)
    await manager.async_load_skills()

    assert set(manager._skills) == {"good"}
    assert manager._extended_openai_skills_initialized is True


@pytest.mark.asyncio
async def test_skill_first_load_failure_clears_singleton_and_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeParser:
        @staticmethod
        def parse(_content: str, _path: Path, _base: Path) -> Any:
            return None

    class FakeManager:
        _instance = None

        def __init__(self, hass: Any) -> None:
            self._hass = hass
            self._skills = {}
            self._user_skills_dir: Path | None = None

        @property
        def user_skills_dir(self) -> Path:
            return self._user_skills_dir or Path("/default-skills")

        def _load_skills_from_dir_sync(self, _path: Path) -> list[Any]:
            return []

        async def async_load_skills(self) -> None:
            raise AssertionError("installer should replace this method")

        @classmethod
        async def async_get_instance(cls, hass: Any, user_skills_dir: str | None = None):
            raise AssertionError("installer should replace this method")

        @classmethod
        def get_loaded_instance(cls):
            return None

    executor = AsyncMock(side_effect=[OSError("disk unavailable"), []])
    hass = SimpleNamespace(data={}, async_add_executor_job=executor)
    monkeypatch.setattr(skills, "SkillManager", FakeManager)
    monkeypatch.setattr(skills, "SkillMdParser", FakeParser)

    runtime_hardening._install_skill_hardening()

    with pytest.raises(OSError, match="disk unavailable"):
        await FakeManager.async_get_instance(hass, "/custom-skills")
    assert FakeManager._instance is None

    manager = await FakeManager.async_get_instance(hass, "/custom-skills")
    assert FakeManager._instance is manager
    assert manager._user_skills_dir == Path("/custom-skills")
    assert manager._extended_openai_skills_initialized is True
    assert FakeManager.get_loaded_instance() is manager
    assert await FakeManager.async_get_instance(hass, "/ignored-after-init") is manager


@pytest.mark.asyncio
async def test_guest_mode_storage_failure_remains_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSchedule:
        def __init__(self, **values: Any) -> None:
            self.__dict__.update(values)

    class FakeManager:
        async def async_initialize(self) -> None:
            raise AssertionError("installer should replace this method")

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

    runtime_hardening._install_guest_mode_hardening()

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

    class FakeManager:
        async def async_initialize(self) -> None:
            raise AssertionError("installer should replace this method")

    manager = FakeManager()
    manager.hass = SimpleNamespace()
    manager._store = SimpleNamespace(
        async_load=AsyncMock(
            return_value={"schedule": {"active_from": "not-a-date", "active_until": None}}
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

    runtime_hardening._install_guest_mode_hardening()
    await manager.async_initialize()

    assert manager._initialized is True
    assert manager._schedule is None


@pytest.mark.asyncio
async def test_tool_result_guard_bounds_outermost_string_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeEntity:
        async def _execute_function_tool(self, *_args: Any) -> Any:
            return SimpleNamespace(
                tool_result={
                    "result": "x" * (runtime_hardening.MAX_MODEL_TOOL_RESULT_CHARACTERS + 100)
                }
            )

    monkeypatch.setattr(conversation, "ExtendedOpenAIAgentEntity", FakeEntity)

    runtime_hardening._wrap_conversation_tool_results()
    guarded = FakeEntity._execute_function_tool
    runtime_hardening._wrap_conversation_tool_results()
    assert FakeEntity._execute_function_tool is guarded

    content = await FakeEntity()._execute_function_tool({}, {}, None, [])
    result = content.tool_result["result"]
    assert len(result) <= runtime_hardening.MAX_MODEL_TOOL_RESULT_CHARACTERS
    assert runtime_hardening._TOOL_RESULT_TRUNCATION_LABEL in result


@pytest.mark.asyncio
async def test_tool_result_install_wraps_delayed_hook_and_existing_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def original_install() -> None:
        calls.append("delayed")

    monkeypatch.setattr(delayed_tools, "_install_execution_hook", original_install)
    wrap = Mock()
    monkeypatch.setattr(runtime_hardening, "_wrap_conversation_tool_results", wrap)

    runtime_hardening._install_tool_result_hardening()
    installed = delayed_tools._install_execution_hook
    assert installed is not original_install
    wrap.assert_called_once_with()

    wrap.reset_mock()
    installed()
    assert calls == ["delayed"]
    wrap.assert_called_once_with()
