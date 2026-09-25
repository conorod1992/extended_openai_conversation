"""Repeated installed-Skill publication, replacement, removal and discovery."""

from __future__ import annotations

import asyncio
from pathlib import Path
import random

import pytest

from custom_components.extended_openai_conversation_responses.skills import SkillManager
from homeassistant.core import HomeAssistant
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
    rng = random.Random(stress_seed)
    operations = 20 * stress_scale
    publishes = removals = scans = blocked_mutations = 0

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
        action = rng.choices(("publish", "remove", "scan"), (5, 3, 2))[0]
        if action == "publish":
            description = f"Revision {index} of {name}"
            staged = manager.staging_dir / f"{name}.stage-{index}"
            _write_skill(staged, description)
            if index % 5 == 0:
                async with manager.async_skill_read():
                    pending = asyncio.create_task(
                        manager.async_publish_staged_skill(name, staged)
                    )
                    await asyncio.sleep(0)
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
        else:
            await manager.async_load_skills()
            scans += 1
        verify()
        record(stress_trace, action, name=name, catalogue_size=len(expected))

    await manager.async_load_skills()
    verify()
    assert publishes and removals and scans and blocked_mutations
    record(
        stress_trace,
        "summary",
        layer="real-ha",
        skill_lifecycle_operations=operations,
        skill_publishes=publishes,
        skill_removals=removals,
        skill_scans=scans + 1,
        skill_blocked_mutations=blocked_mutations,
    )
