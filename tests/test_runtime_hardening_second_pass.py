"""Second-pass residual coverage for runtime hardening guards."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import skills


class _Parser:
    @staticmethod
    def parse(_content: str, _path: Path, _base: Path) -> None:
        return None


def _skill_manager_type():
    class FakeManager(skills.SkillManager):
        _instance = None

    return FakeManager


@pytest.mark.asyncio
async def test_skill_getter_initializes_without_custom_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default Skill directory path remains valid on first initialization."""
    manager_type = _skill_manager_type()
    hass = SimpleNamespace(
        config=SimpleNamespace(config_dir="/config"),
        data={},
        async_add_executor_job=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(skills, "SkillManager", manager_type)
    monkeypatch.setattr(skills, "SkillMdParser", _Parser)

    manager = await manager_type.async_get_instance(hass)

    assert manager_type._instance is manager
    assert manager._user_skills_dir == manager.user_skills_dir
    assert manager._initialized is True


@pytest.mark.asyncio
async def test_skill_getter_adopts_late_directory_before_first_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An existing uninitialized singleton may still adopt its configured Skill path."""
    manager_type = _skill_manager_type()
    hass = SimpleNamespace(
        config=SimpleNamespace(config_dir="/config"),
        data={},
        async_add_executor_job=AsyncMock(return_value=[]),
    )
    manager = manager_type(hass)
    manager_type._instance = manager
    monkeypatch.setattr(skills, "SkillManager", manager_type)
    monkeypatch.setattr(skills, "SkillMdParser", _Parser)

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
        config=SimpleNamespace(config_dir="/config"),
        data={},
        async_add_executor_job=AsyncMock(side_effect=fail_after_replacement),
    )
    monkeypatch.setattr(skills, "SkillManager", manager_type)
    monkeypatch.setattr(skills, "SkillMdParser", _Parser)

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

    assert manager_type.get_loaded_instance() is None
