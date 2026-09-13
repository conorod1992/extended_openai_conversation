"""Second-pass residual coverage for runtime hardening guards."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    delayed_tools,
    runtime_hardening,
    skills,
    usage,
)


def test_usage_hardening_leaves_already_guarded_getter_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated installation must not stack another per-agent Usage lock wrapper."""

    async def guarded(*_args: Any) -> object:
        return object()

    guarded._extended_openai_getter_guard = True  # type: ignore[attr-defined]
    monkeypatch.setattr(usage, "async_get_usage", guarded)

    runtime_hardening._install_usage_hardening()

    assert usage.async_get_usage is guarded


class _Parser:
    @staticmethod
    def parse(_content: str, _path: Path, _base: Path) -> None:
        return None


def _skill_manager_type():
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
        async def async_get_instance(
            cls, hass: Any, user_skills_dir: str | None = None
        ) -> Any:
            raise AssertionError("installer should replace this method")

        @classmethod
        def get_loaded_instance(cls) -> Any:
            return None

    return FakeManager


@pytest.mark.asyncio
async def test_skill_getter_initializes_without_custom_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default Skill directory path remains valid on first initialization."""
    manager_type = _skill_manager_type()
    hass = SimpleNamespace(data={}, async_add_executor_job=AsyncMock(return_value=[]))
    monkeypatch.setattr(skills, "SkillManager", manager_type)
    monkeypatch.setattr(skills, "SkillMdParser", _Parser)

    runtime_hardening._install_skill_hardening()
    manager = await manager_type.async_get_instance(hass)

    assert manager_type._instance is manager
    assert manager._user_skills_dir is None
    assert manager._extended_openai_skills_initialized is True


@pytest.mark.asyncio
async def test_skill_getter_adopts_late_directory_before_first_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An existing uninitialized singleton may still adopt its configured Skill path."""
    manager_type = _skill_manager_type()
    hass = SimpleNamespace(data={}, async_add_executor_job=AsyncMock(return_value=[]))
    manager = manager_type(hass)
    manager_type._instance = manager
    monkeypatch.setattr(skills, "SkillManager", manager_type)
    monkeypatch.setattr(skills, "SkillMdParser", _Parser)

    runtime_hardening._install_skill_hardening()
    resolved = await manager_type.async_get_instance(hass, "/late-skills")

    assert resolved is manager
    assert manager._user_skills_dir == Path("/late-skills")
    assert hass.async_add_executor_job.await_args.args[1] == Path("/late-skills")


@pytest.mark.asyncio
async def test_skill_first_load_failure_does_not_clear_newer_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed old initializer must not discard a singleton published meanwhile."""
    manager_type = _skill_manager_type()
    replacement_holder: dict[str, Any] = {}

    async def fail_after_replacement(*_args: Any) -> Any:
        replacement = manager_type(SimpleNamespace())
        replacement_holder["manager"] = replacement
        manager_type._instance = replacement
        raise OSError("load failed")

    hass = SimpleNamespace(
        data={},
        async_add_executor_job=AsyncMock(side_effect=fail_after_replacement),
    )
    monkeypatch.setattr(skills, "SkillManager", manager_type)
    monkeypatch.setattr(skills, "SkillMdParser", _Parser)

    runtime_hardening._install_skill_hardening()

    with pytest.raises(OSError, match="load failed"):
        await manager_type.async_get_instance(hass)

    assert manager_type._instance is replacement_holder["manager"]


def test_loaded_skill_getter_returns_none_for_uninitialized_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callers must not observe a singleton before its catalogue is complete."""
    manager_type = _skill_manager_type()
    manager_type._instance = manager_type(SimpleNamespace())
    monkeypatch.setattr(skills, "SkillManager", manager_type)
    monkeypatch.setattr(skills, "SkillMdParser", _Parser)

    runtime_hardening._install_skill_hardening()

    assert manager_type.get_loaded_instance() is None


def test_tool_result_installer_is_lazy_when_hook_is_guarded_and_conversation_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idempotent installation should not eagerly import/wrap the conversation platform."""

    def guarded_install() -> None:
        raise AssertionError("already-guarded hook should not be replaced or called")

    guarded_install._extended_openai_result_install_guard = True  # type: ignore[attr-defined]
    monkeypatch.setattr(delayed_tools, "_install_execution_hook", guarded_install)
    monkeypatch.delitem(
        sys.modules,
        "custom_components.extended_openai_conversation_responses.conversation",
        raising=False,
    )
    wrap = Mock()
    monkeypatch.setattr(runtime_hardening, "_wrap_conversation_tool_results", wrap)

    runtime_hardening._install_tool_result_hardening()

    assert delayed_tools._install_execution_hook is guarded_install
    wrap.assert_not_called()
