"""Skill file reads resolve the names supplied by their actual templates."""

import asyncio

import pytest

from custom_components.extended_openai_conversation_responses.functions import (
    file as file_module,
)
from custom_components.extended_openai_conversation_responses.functions.file import (
    ReadFileFunction,
)
from custom_components.extended_openai_conversation_responses.skills import SkillManager
from custom_components.extended_openai_conversation_responses.template import (
    ExtendedOpenAITemplateManager,
)
from homeassistant.helpers.template import Template


@pytest.fixture
async def skill_reader(hass, tmp_path, monkeypatch):
    monkeypatch.setattr(SkillManager, "_instance", None)
    root = tmp_path / "published-skills"
    for name in ("heating", "lighting"):
        directory = root / name
        (directory / "references").mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "---\ndescription: Audit Skill\n---\nInstructions"
        )
        (directory / "references" / "setup.md").write_text(f"{name} setup")
    manager = await SkillManager.async_get_instance(hass, user_skills_dir=str(root))
    helper = ExtendedOpenAITemplateManager(hass)
    monkeypatch.setitem(
        hass.data["template.environment"].globals,
        "extended_openai",
        helper._extended_openai,
    )
    return manager, ReadFileFunction()


@pytest.mark.parametrize(
    "path,arguments",
    [
        ("{{ extended_openai.skill_dir('heating') }}/references/setup.md", {}),
        (
            "{{ extended_openai.skill_dir(skill_name) }}/references/setup.md",
            {"skill_name": "heating"},
        ),
        (
            "{{ extended_openai.skill_dir('heating') }}/references/setup.md",
            {"name": "missing"},
        ),
        ("{{ extended_openai['skill_dir']('heating') }}/references/setup.md", {}),
        ("{{ extended_openai.skill_dir(name) }}/SKILL.md", {"name": "heating"}),
    ],
)
async def test_actual_template_controls_skill_resolution(
    hass, skill_reader, path, arguments
):
    manager, function = skill_reader
    result = await function.execute(
        hass, {"path": Template(path, hass)}, arguments, None, []
    )
    assert result["content"] == (
        "---\ndescription: Audit Skill\n---\nInstructions"
        if path.endswith("SKILL.md")
        else "heating setup"
    )
    assert manager._rendered_directories.get() is None


async def test_skill_template_cannot_escape_referenced_directory(hass, skill_reader):
    _, function = skill_reader
    result = await function.execute(
        hass,
        {
            "path": Template(
                "{{ extended_openai.skill_dir('heating') }}/../lighting/references/setup.md",
                hass,
            )
        },
        {},
        None,
        [],
    )
    assert "Access denied" in result["error"]


async def test_merely_mentioning_helper_grants_no_access(hass, skill_reader):
    manager, function = skill_reader
    path = manager.get_skill("heating").path.parent / "references" / "setup.md"
    template = Template("{# skill_dir #}" + str(path), hass)
    result = await function.execute(hass, {"path": template}, {}, None, [])
    assert "Access denied" in result["error"]


async def test_missing_skill_uses_rendered_name_not_unrelated_argument(
    hass, skill_reader
):
    _, function = skill_reader
    result = await function.execute(
        hass,
        {"path": Template("{{ extended_openai.skill_dir('missing') }}/SKILL.md", hass)},
        {"name": "heating"},
        None,
        [],
    )
    assert "Skill not found: missing" in result["error"]


async def test_captured_directories_are_isolated_between_tasks(skill_reader):
    manager, _ = skill_reader

    async def render(name):
        with manager.capture_rendered_directories() as directories:
            directory = manager.get_skill_directory(name)
            await asyncio.sleep(0)
            assert directories == {directory}
        assert manager._rendered_directories.get() is None

    await asyncio.gather(render("heating"), render("lighting"))


async def test_read_holds_skill_boundary_until_bounded_read_finishes(
    hass, skill_reader
):
    manager, function = skill_reader
    started, release = asyncio.Event(), asyncio.Event()

    async def executor(operation, *args):
        if operation is file_module._read_text_bounded:
            started.set()
            await release.wait()
        return operation(*args)

    hass.async_add_executor_job.side_effect = executor
    reading = asyncio.create_task(
        function.execute(
            hass,
            {
                "path": Template(
                    "{{ extended_openai.skill_dir('heating') }}/references/setup.md",
                    hass,
                ),
            },
            {},
            None,
            [],
        )
    )
    await started.wait()
    removal = asyncio.create_task(manager.async_remove_skill("heating"))
    await asyncio.sleep(0)
    assert not removal.done()
    release.set()
    assert (await reading)["content"] == "heating setup"
    assert await removal is True
    assert manager.get_skill("heating") is None


async def test_literal_helper_mention_needs_no_skill_manager(
    hass, tmp_path, monkeypatch
):
    monkeypatch.setattr(SkillManager, "_instance", None)
    path = tmp_path / "extended_openai_conversation_responses" / "skill_dir_notes.txt"
    path.write_text("ordinary file")
    result = await ReadFileFunction().execute(
        hass, {"path": Template(str(path), hass)}, {}, None, []
    )
    assert result["content"] == "ordinary file"
