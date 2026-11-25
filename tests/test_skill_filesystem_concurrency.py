"""Concurrency coverage for installed Skill discovery and reads."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses.skills import SkillManager
from tests.lock_probe import LockProbe


def _write_skill(directory: Path, description: str = "Test Skill") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\ndescription: {description}\n---\nInstructions\n",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def reset_skill_singleton():
    """Keep the process-global Skill singleton isolated between tests."""
    previous = SkillManager._instance
    SkillManager._instance = None
    try:
        yield
    finally:
        SkillManager._instance = previous


async def _manager(hass, tmp_path: Path) -> SkillManager:
    skills_dir = tmp_path / "published-skills"
    return await SkillManager.async_get_instance(hass, user_skills_dir=str(skills_dir))


async def test_runtime_hardening_keeps_manager_owned_boundary(hass, tmp_path) -> None:
    """Startup wrappers must not replace the manager's authoritative lock path."""
    manager = await _manager(hass, tmp_path)
    assert manager.filesystem_concurrency_safe is True
    assert hasattr(manager, "_filesystem_lock")
    assert not hasattr(manager, "_extended_openai_skill_load_lock")


async def test_concurrent_initialization_retry_cannot_create_parallel_managers(
    hass, tmp_path, monkeypatch
) -> None:
    """A failed first load cannot race a waiting retry against a new singleton."""
    skills_dir = tmp_path / "published-skills"
    original_discover = SkillManager._async_discover_skills_locked
    calls = 0
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    retry_started = asyncio.Event()
    release_retry = asyncio.Event()

    async def controlled_discover(manager: SkillManager):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await release_first.wait()
            raise RuntimeError("simulated initial discovery failure")
        if calls == 2:
            retry_started.set()
            await release_retry.wait()
        return await original_discover(manager)

    monkeypatch.setattr(
        SkillManager, "_async_discover_skills_locked", controlled_discover
    )

    first = asyncio.create_task(
        SkillManager.async_get_instance(hass, user_skills_dir=str(skills_dir))
    )
    await first_started.wait()
    second = asyncio.create_task(
        SkillManager.async_get_instance(hass, user_skills_dir=str(skills_dir))
    )
    release_first.set()
    with pytest.raises(RuntimeError, match="simulated initial discovery failure"):
        await first

    await retry_started.wait()
    third = asyncio.create_task(
        SkillManager.async_get_instance(hass, user_skills_dir=str(skills_dir))
    )
    await asyncio.sleep(0)
    assert calls == 2
    assert not third.done()

    release_retry.set()
    second_manager, third_manager = await asyncio.gather(second, third)
    assert second_manager is third_manager
    assert SkillManager._instance is second_manager
    assert calls == 2


async def test_staging_is_outside_discovery_and_incomplete_paths_stay_hidden(
    hass, tmp_path
) -> None:
    manager = await _manager(hass, tmp_path)
    assert not manager.staging_dir.resolve().is_relative_to(
        manager.user_skills_dir.resolve()
    )

    _write_skill(manager.staging_dir / "staged")
    _write_skill(manager.user_skills_dir / ".partial")
    await manager.async_load_skills()
    assert manager.get_all_skills() == []


async def test_reload_waits_for_active_skill_reader(
    hass, tmp_path, monkeypatch
) -> None:
    """Keep the current catalogue visible while reload waits for a reader."""
    manager = await _manager(hass, tmp_path)
    _write_skill(manager.user_skills_dir / "alpha", "Alpha")
    await manager.async_load_skills()
    assert [skill.name for skill in manager.get_all_skills()] == ["alpha"]

    _write_skill(manager.user_skills_dir / "beta", "Beta")
    _write_skill(manager.user_skills_dir / "alpha", "Updated alpha")

    probe = LockProbe(manager._filesystem_lock)
    monkeypatch.setattr(manager, "_filesystem_lock", probe)

    async with manager.async_skill_read():
        await probe.next_attempt()
        reload = asyncio.create_task(manager.async_load_skills())
        assert await probe.next_attempt() is reload
        assert not reload.done()
        assert manager.get_skill("alpha").description == "Alpha"
        assert manager.get_skill("beta") is None

    await reload
    assert manager.get_skill("alpha").description == "Updated alpha"
    assert {skill.name for skill in manager.get_all_skills()} == {"alpha", "beta"}


async def test_repeated_cancellation_keeps_lock_until_reload_settles(
    hass, tmp_path, monkeypatch
) -> None:
    """Repeated cancellation cannot cancel the owned operation or release its lock."""
    manager = await _manager(hass, tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()
    read_acquired = asyncio.Event()

    async def blocked_discover():
        started.set()
        await release.wait()
        completed.set()
        return {}

    async def competing_read() -> None:
        async with manager.async_skill_read():
            read_acquired.set()

    monkeypatch.setattr(manager, "_async_discover_skills_locked", blocked_discover)
    task = asyncio.create_task(manager.async_load_skills())
    await started.wait()

    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)

    reader = asyncio.create_task(competing_read())
    await asyncio.sleep(0)
    assert not task.done()
    assert not completed.is_set()
    assert not read_acquired.is_set()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert completed.is_set()

    await asyncio.wait_for(read_acquired.wait(), timeout=1)
    await reader
