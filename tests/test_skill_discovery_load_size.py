"""Discovered Skill instructions must fit the canonical load_skill reader."""

from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    DEFAULT_CONF_FUNCTION_TOOLS,
    FILE_READ_SIZE_LIMIT,
)
from custom_components.extended_openai_conversation_responses.functions import (
    get_function,
)
from custom_components.extended_openai_conversation_responses.skills import SkillManager
from custom_components.extended_openai_conversation_responses.template import (
    ExtendedOpenAITemplateManager,
)
from homeassistant.exceptions import HomeAssistantError


def _instructions(size: int) -> bytes:
    prefix = b"---\ndescription: Size boundary regression\n---\n"
    # UTF-8 byte counts must govern both paths, rather than character counts.
    remaining = size - len(prefix)
    return prefix + "é".encode() * (remaining // 2) + b"x" * (remaining % 2)


@pytest.fixture
async def skill_loader(hass, tmp_path, monkeypatch):
    monkeypatch.setattr(SkillManager, "_instance", None)
    root = tmp_path / "skills"
    manager = await SkillManager.async_get_instance(hass, user_skills_dir=str(root))
    helper = ExtendedOpenAITemplateManager(hass)
    monkeypatch.setitem(
        hass.data["template.environment"].globals,
        "extended_openai",
        helper._extended_openai,
    )
    preset = next(
        tool["function"]
        for tool in DEFAULT_CONF_FUNCTION_TOOLS
        if tool["spec"]["name"] == "load_skill"
    )
    function = get_function(preset["type"])
    config = function.validate_schema(preset)

    async def load(name):
        return await function.execute(
            hass, config, {"name": name, "file": "SKILL.md"}, None, []
        )

    return manager, root, load


def _write(root: Path, name: str, content: bytes) -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.mark.parametrize("size", [1024 * 1024 - 1, 1024 * 1024])
async def test_discovered_skill_loads_at_reader_boundary(skill_loader, size):
    manager, root, load = skill_loader
    content = _instructions(size)
    _write(root, "boundary", content)
    await manager.async_load_skills()

    assert manager.get_skill("boundary") is not None
    result = await load("boundary")
    assert result == {"content": content.decode(), "size": size}


@pytest.mark.parametrize(
    "size", [1024 * 1024 + 1, 1536 * 1024, 2 * 1024 * 1024, 2 * 1024 * 1024 + 1]
)
async def test_unloadable_skill_is_skipped_without_losing_healthy_sibling(
    skill_loader, size, caplog
):
    manager, root, load = skill_loader
    path = _write(root, "oversized", _instructions(size))
    healthy = _instructions(256)
    _write(root, "healthy", healthy)
    await manager.async_load_skills()

    assert manager.get_skill("oversized") is None
    assert "could not be read and was skipped" in caplog.text
    assert path.stat().st_size == size  # Discovery never deletes authored files.
    assert "Skill not found" in (await load("oversized"))["error"]
    assert (await load("healthy"))["content"] == healthy.decode()


async def test_growing_discovered_skill_is_bounded_then_removed_on_reload(skill_loader):
    manager, root, load = skill_loader
    path = _write(root, "growing", _instructions(256))
    await manager.async_load_skills()
    assert manager.get_skill("growing") is not None
    assert (await load("growing"))["size"] == 256

    path.write_bytes(_instructions(FILE_READ_SIZE_LIMIT + 1))
    assert "File too large" in (await load("growing"))["error"]
    await manager.async_load_skills()
    assert manager.get_skill("growing") is None

    # Correcting the file and reloading restores ordinary operation.
    content = _instructions(FILE_READ_SIZE_LIMIT)
    path.write_bytes(content)
    await manager.async_load_skills()
    assert manager.get_skill("growing") is not None
    assert (await load("growing"))["content"] == content.decode()


@pytest.mark.parametrize("replace_existing", [False, True])
async def test_oversized_publication_preserves_previous_installed_state(
    skill_loader, replace_existing
):
    manager, root, load = skill_loader
    original = _instructions(256)
    if replace_existing:
        _write(root, "target", original)
    await manager.async_load_skills()
    staged = manager.staging_dir / "candidate"
    _write(manager.staging_dir, "candidate", _instructions(1536 * 1024))

    with pytest.raises(HomeAssistantError, match="not a valid installed Skill"):
        await manager.async_publish_staged_skill("target", staged)

    if replace_existing:
        assert manager.get_skill("target") is not None
        assert (await load("target"))["content"] == original.decode()
        assert (root / "target" / "SKILL.md").read_bytes() == original
    else:
        assert manager.get_skill("target") is None
        assert not (root / "target").exists()
