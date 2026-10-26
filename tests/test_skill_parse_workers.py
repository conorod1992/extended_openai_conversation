"""Catalogue parsing belongs in the worker that owns discovery's stable inputs."""

import asyncio
import threading

import pytest

from custom_components.extended_openai_conversation_responses.skills import (
    Skill,
    SkillManager,
    SkillMdParser,
)


@pytest.mark.parametrize(
    "files,fields",
    [(1, 0), (1, 10000), (128, 100)],
    ids=["tiny", "large-frontmatter", "catalogue"],
)
async def test_skill_reload_parses_before_atomic_publish_in_worker(
    hass, tmp_path, monkeypatch, files, fields
):
    root = tmp_path / "skills"
    for i in range(files):
        directory = root / f"skill-{i:03d}"
        directory.mkdir(parents=True)
        metadata = "".join(f"metadata_{j}: value_{j}\n" for j in range(fields))
        (directory / "SKILL.md").write_text(
            f"---\ndescription: Valid skill\n{metadata}---\nInstructions\n",
            encoding="utf-8",
        )

    async def executor(function, *args):
        return await asyncio.to_thread(function, *args)

    monkeypatch.setattr(hass, "async_add_executor_job", executor)
    manager = SkillManager(hass)
    manager._user_skills_dir = root
    old = {"old": Skill("old", "Old catalogue", root / "old")}
    manager._skills = old
    parse = SkillMdParser.parse
    threads = []
    main = threading.get_ident()

    def checked(*args):
        threads.append(threading.get_ident())
        assert manager._filesystem_lock.locked()
        assert manager._skills is old
        return parse(*args)

    monkeypatch.setattr(SkillMdParser, "parse", staticmethod(checked))
    await manager.async_load_skills()
    assert len(manager._skills) == files
    assert set(manager._skills) == {f"skill-{i:03d}" for i in range(files)}
    assert old.keys() == {"old"} and manager._skills is not old
    assert len(threads) == files and main not in threads
