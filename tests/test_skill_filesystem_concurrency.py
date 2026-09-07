"""Concurrency coverage for installed Skill discovery and publication."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses.runtime_hardening import (
    install_runtime_hardening,
)
from custom_components.extended_openai_conversation_responses.skills import SkillManager


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
    install_runtime_hardening()
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


async def test_scan_publish_and_remove_share_one_serialized_boundary(hass, tmp_path) -> None:
    """Concurrent operations never expose staging or partial removal state."""
    manager = await _manager(hass, tmp_path)
    _write_skill(manager.user_skills_dir / "alpha", "Alpha")
    await manager.async_load_skills()
    assert [skill.name for skill in manager.get_all_skills()] == ["alpha"]

    staged_beta = manager.staging_dir / "beta.download-test"
    _write_skill(staged_beta, "Beta")

    async with manager.async_skill_read():
        publish = asyncio.create_task(
            manager.async_publish_staged_skill("beta", staged_beta)
        )
        scan = asyncio.create_task(manager.async_load_skills())
        remove = asyncio.create_task(manager.async_remove_skill("alpha"))
        await asyncio.sleep(0)
        assert not publish.done()
        assert not scan.done()
        assert not remove.done()

    _, _, removed = await asyncio.gather(publish, scan, remove)
    assert removed is True
    assert not (manager.user_skills_dir / "alpha").exists()
    assert (manager.user_skills_dir / "beta" / "SKILL.md").is_file()
    assert [skill.name for skill in manager.get_all_skills()] == ["beta"]


async def test_publish_rescans_without_reentering_non_reentrant_lock(hass, tmp_path) -> None:
    """Publication may discover under the owned lock without calling the public loader."""
    manager = await _manager(hass, tmp_path)
    staged = manager.staging_dir / "demo.download-test"
    _write_skill(staged, "Demo")

    await asyncio.wait_for(
        manager.async_publish_staged_skill("demo", staged), timeout=2
    )
    assert manager.get_skill("demo") is not None


async def test_cancelled_publication_finishes_to_stable_state_before_unlock(
    hass, tmp_path, monkeypatch
) -> None:
    """Cancellation cannot leave a half-published Skill or a permanently held lock."""
    manager = await _manager(hass, tmp_path)
    staged = manager.staging_dir / "demo.download-test"
    _write_skill(staged, "Demo")

    original_discover = manager._async_discover_skills_locked
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_discover():
        started.set()
        await release.wait()
        return await original_discover()

    monkeypatch.setattr(manager, "_async_discover_skills_locked", blocked_discover)
    task = asyncio.create_task(manager.async_publish_staged_skill("demo", staged))
    await started.wait()
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    async with manager.async_skill_read():
        assert manager.get_skill("demo") is not None
    assert (manager.user_skills_dir / "demo" / "SKILL.md").is_file()


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
