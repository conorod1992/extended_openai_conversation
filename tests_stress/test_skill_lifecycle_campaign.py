"""Repeated installed-Skill publication, replacement, removal and discovery."""

from __future__ import annotations

import asyncio
from pathlib import Path
import random

import pytest

from custom_components.extended_openai_conversation_responses.skills import SkillManager
from homeassistant.core import HomeAssistant
from tests.lock_probe import LockProbe
from tests_stress.conftest import record


def _write_skill(directory: Path, description: str) -> None:
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\ndescription: {description}\n---\nStable instructions.\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_installed_skills_remain_atomic_during_repeated_lifecycle_changes(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stress_seed: int,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    """Readers see a whole catalogue across rescans, updates and removals."""
    monkeypatch.setattr(SkillManager, "_instance", None)
    root = tmp_path / "installed-skills"
    expected = {f"skill-{index:02d}": f"Initial {index}" for index in range(16)}
    for name, description in expected.items():
        _write_skill(root / name, description)
    (root / "malformed").mkdir(parents=True)
    (root / "malformed" / "SKILL.md").write_text(
        "Missing frontmatter", encoding="utf-8"
    )
    manager = await SkillManager.async_get_instance(hass, str(root))
    probe = LockProbe(manager._filesystem_lock)
    monkeypatch.setattr(manager, "_filesystem_lock", probe)
    rng = random.Random(stress_seed)
    operations = 20 * stress_scale
    publishes = removals = scans = blocked_mutations = 0
    successful_removals = 0

    def verify() -> None:
        actual = {skill.name: skill.description for skill in manager.get_all_skills()}
        assert actual == expected
        assert "malformed" not in actual
        for name, description in expected.items():
            skill = manager.get_skill(name)
            assert skill is not None and skill.description == description

    verify()
    for index in range(operations):
        name = f"skill-{rng.randrange(24):02d}"
        action = (
            "publish"
            if index == 0
            else rng.choices(("publish", "remove", "scan"), (5, 3, 2))[0]
        )
        if action == "publish":
            description = f"Revision {index} of {name}"
            staged = manager.staging_dir / f"{name}.stage-{index}"
            _write_skill(staged, description)
            if index % 5 == 0:
                while not probe.attempts.empty():
                    probe.attempts.get_nowait()
                async with manager.async_skill_read():
                    await probe.next_attempt()  # The held read lease.
                    pending = asyncio.create_task(
                        manager.async_publish_staged_skill(name, staged)
                    )
                    await probe.next_attempt()  # Publisher reached the same lock.
                    assert not pending.done()
                    verify()
                    blocked_mutations += 1
                await pending
            else:
                await manager.async_publish_staged_skill(name, staged)
            expected[name] = description
            publishes += 1
        elif action == "remove":
            removed = await manager.async_remove_skill(name)
            assert removed is (name in expected)
            expected.pop(name, None)
            removals += 1
            successful_removals += int(removed)
        else:
            await manager.async_load_skills()
            scans += 1
        verify()
        record(stress_trace, action, name=name, catalogue_size=len(expected))

    # Keep the full original random journey, adding only an unexercised minimum.
    if not successful_removals:
        name = next(iter(expected))
        assert await manager.async_remove_skill(name)
        del expected[name]
        removals += 1
        successful_removals += 1
        operations += 1
        verify()
        record(stress_trace, "remove", name=name, guaranteed_minimum=True)
    await manager.async_load_skills()
    scans += 1
    verify()
    assert publishes and removals and successful_removals and scans and blocked_mutations
    record(
        stress_trace,
        "summary",
        layer="real-ha",
        skill_lifecycle_operations=operations,
        skill_publishes=publishes,
        skill_removals=removals,
        skill_scans=scans,
        skill_blocked_mutations=blocked_mutations,
    )


async def test_seed_without_removal_guarantees_installed_mutation(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    """Seed 149 exercises no successful random removal in its original journey."""
    record(stress_trace, "fixed_minimum_seed", seed=149)
    await test_installed_skills_remain_atomic_during_repeated_lifecycle_changes(
        hass, tmp_path, monkeypatch, 149, 1, stress_trace
    )
    assert any(item.get("guaranteed_minimum") for item in stress_trace)
