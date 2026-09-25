"""Focused coverage for Skill discovery and filesystem lifecycle behaviour."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import skills as skills_module
from custom_components.extended_openai_conversation_responses.skills import (
    Skill,
    SkillManager,
    SkillMdParser,
)


def _skill_text(description: str = "A useful skill", body: str = "Body") -> str:
    return f"---\ndescription: {description}\n---\n{body}\n"


def _write_skill(directory: Path, *, description: str = "A useful skill", body: str = "Body") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    skill_file = directory / skills_module.SKILL_FILE_NAME
    skill_file.write_text(_skill_text(description, body), encoding="utf-8")
    return skill_file


def test_skill_metadata_validation(tmp_path: Path) -> None:
    """Skill rejects metadata that cannot be represented safely."""
    with pytest.raises(ValueError, match="name is required"):
        Skill(name="", description="ok", path=tmp_path / "SKILL.md")
    with pytest.raises(ValueError, match="64 characters"):
        Skill(name="x" * 65, description="ok", path=tmp_path / "SKILL.md")
    with pytest.raises(ValueError, match="1024 characters"):
        Skill(name="ok", description="x" * 1025, path=tmp_path / "SKILL.md")


@pytest.mark.parametrize(
    "content",
    [
        "plain markdown",
        "---\ndescription: [unterminated\n---\nbody",
        "---\n- list-item\n---\nbody",
        "---\nname: no-description\n---\nbody",
    ],
)
def test_parser_rejects_invalid_frontmatter(
    content: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Malformed frontmatter is skipped with an actionable System Log warning."""
    skill_file = tmp_path / "alpha" / "SKILL.md"
    with caplog.at_level(logging.WARNING):
        assert SkillMdParser.parse(content, skill_file, tmp_path) is None
    assert "Skill 'alpha' was not loaded" in caplog.text
    assert "Extended OpenAI > Skills" in caplog.text


def test_parser_path_fallback_validation_and_body_extraction(tmp_path: Path) -> None:
    """Parser handles paths outside the scan root and invalid derived metadata."""
    outside = tmp_path / "outside" / "alpha" / "SKILL.md"
    skill = SkillMdParser.parse(_skill_text("Outside"), outside, tmp_path / "base")
    assert skill == Skill(name="alpha", description="Outside", path=outside)

    too_long = tmp_path / ("x" * 65) / "SKILL.md"
    assert SkillMdParser.parse(_skill_text(), too_long, tmp_path) is None

    assert SkillMdParser.extract_body("no frontmatter") == "no frontmatter"
    assert SkillMdParser.extract_body(_skill_text(body="  body text  ")) == "body text"


@pytest.mark.asyncio
async def test_singleton_initialization_failure_is_retryable(hass, tmp_path: Path, monkeypatch) -> None:
    """A failed first discovery does not poison the process-wide singleton."""
    monkeypatch.setattr(SkillManager, "_instance", None)
    calls = 0

    async def initialize(self: SkillManager) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("discovery failed")
        self._initialized = True

    monkeypatch.setattr(SkillManager, "async_initialize", initialize)

    with pytest.raises(RuntimeError, match="discovery failed"):
        await SkillManager.async_get_instance(hass, str(tmp_path / "skills"))
    assert SkillManager._instance is None
    assert SkillManager.get_loaded_instance() is None

    manager = await SkillManager.async_get_instance(hass, str(tmp_path / "skills"))
    assert manager._initialized is True
    assert SkillManager.get_loaded_instance() is manager


@pytest.mark.asyncio
async def test_singleton_adopts_directory_before_initialization(hass, tmp_path: Path, monkeypatch) -> None:
    """A pre-created uninitialized singleton can accept the configured Skill root."""
    manager = SkillManager(hass)
    monkeypatch.setattr(SkillManager, "_instance", manager)

    async def initialize(self: SkillManager) -> None:
        self._initialized = True

    monkeypatch.setattr(SkillManager, "async_initialize", initialize)
    configured = tmp_path / "configured-skills"
    assert await SkillManager.async_get_instance(hass, str(configured)) is manager
    assert manager.user_skills_dir == configured


@pytest.mark.asyncio
async def test_default_paths_initialization_and_reload(hass, tmp_path: Path, monkeypatch) -> None:
    """Default paths discover Skills once and explicit reload atomically refreshes them."""
    monkeypatch.setattr(SkillManager, "_instance", None)
    manager = SkillManager(hass)
    expected = (
        tmp_path
        / skills_module.DEFAULT_WORKING_DIRECTORY
        / skills_module.DEFAULT_SKILLS_DIRECTORY
    )
    assert manager.user_skills_dir == expected
    assert manager.staging_dir == expected.parent / f".{expected.name}.staging"

    await manager.async_initialize()
    await manager.async_initialize()  # initialized fast path
    assert manager.get_all_skills() == []

    _write_skill(expected / "alpha", description="Alpha")
    await manager.async_load_skills()
    assert manager.get_skill("alpha") is not None
    assert manager.get_skill("missing") is None


@pytest.mark.asyncio
async def test_locked_operation_finishes_before_cancellation_escapes(hass) -> None:
    """Cancellation cannot release the mutation lock while filesystem work is active."""
    manager = SkillManager(hass)
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation() -> str:
        started.set()
        await release.wait()
        return "done"

    task = asyncio.create_task(manager._async_run_locked(operation))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    # Exercise the repeated-cancellation branch while the shielded work is pending.
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert not manager._filesystem_lock.locked()


@pytest.mark.asyncio
async def test_discovery_isolates_one_unexpected_parser_failure(hass, tmp_path: Path, monkeypatch) -> None:
    """One parser exception does not prevent a later Skill from being discovered."""
    manager = SkillManager(hass)
    manager._user_skills_dir = tmp_path / "skills"
    first = manager.user_skills_dir / "first" / "SKILL.md"
    second = manager.user_skills_dir / "second" / "SKILL.md"
    monkeypatch.setattr(
        manager,
        "_load_skills_from_dir_sync",
        lambda _root: [(first, _skill_text()), (second, _skill_text("Second"))],
    )

    original_parse = SkillMdParser.parse
    calls = 0

    def parse(content: str, skill_path: Path, base: Path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("bad parser input")
        return original_parse(content, skill_path, base)

    monkeypatch.setattr(SkillMdParser, "parse", parse)
    loaded = await manager._async_discover_skills_locked()
    assert list(loaded) == ["second"]


@pytest.mark.asyncio
async def test_skill_read_holds_filesystem_lock(hass) -> None:
    """Canonical reads use the same boundary as mutations."""
    manager = SkillManager(hass)
    async with manager.async_skill_read():
        assert manager._filesystem_lock.locked()
    assert not manager._filesystem_lock.locked()


@pytest.mark.asyncio
async def test_publish_new_skill_and_replace_existing_skill(hass, tmp_path: Path) -> None:
    """Publishing installs new content and cleans replacement backups."""
    manager = SkillManager(hass)
    manager._user_skills_dir = tmp_path / "skills"

    staged = manager.staging_dir / "download-new"
    _write_skill(staged, description="Alpha", body="new body")
    await manager.async_publish_staged_skill("alpha", staged)
    assert manager.get_skill("alpha") is not None
    assert (manager.user_skills_dir / "alpha" / "SKILL.md").read_text(encoding="utf-8").endswith("new body\n")
    assert not staged.exists()

    replacement = manager.staging_dir / "download-replacement"
    _write_skill(replacement, description="Alpha replacement", body="replacement")
    await manager.async_publish_staged_skill("alpha", replacement)
    assert manager.get_skill("alpha").description == "Alpha replacement"
    assert not any(manager.staging_dir.glob("alpha.backup-*"))


@pytest.mark.asyncio
async def test_publish_rejects_unmanaged_staging_path(hass, tmp_path: Path) -> None:
    """Only directories below the manager-owned staging root can be published."""
    manager = SkillManager(hass)
    manager._user_skills_dir = tmp_path / "skills"
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(HomeAssistantError, match="outside the managed staging area"):
        await manager.async_publish_staged_skill("alpha", outside)
    with pytest.raises(HomeAssistantError, match="outside the managed staging area"):
        await manager.async_publish_staged_skill("alpha", manager.staging_dir)


@pytest.mark.asyncio
async def test_invalid_replacement_rolls_back_existing_skill(hass, tmp_path: Path) -> None:
    """A staged Skill that fails discovery restores the previous installed version."""
    manager = SkillManager(hass)
    manager._user_skills_dir = tmp_path / "skills"
    target = manager.user_skills_dir / "alpha"
    _write_skill(target, description="Old", body="old body")
    await manager.async_load_skills()

    staged = manager.staging_dir / "bad-download"
    staged.mkdir(parents=True)
    (staged / "SKILL.md").write_text("not valid skill frontmatter", encoding="utf-8")

    with pytest.raises(HomeAssistantError, match="not a valid installed Skill"):
        await manager.async_publish_staged_skill("alpha", staged)

    assert target.exists()
    assert "old body" in (target / "SKILL.md").read_text(encoding="utf-8")
    assert manager.get_skill("alpha").description == "Old"
    assert not any(manager.staging_dir.glob("alpha.backup-*"))


@pytest.mark.asyncio
async def test_remove_success_absent_and_discovery_failure_rollback(hass, tmp_path: Path, monkeypatch) -> None:
    """Removal handles absence and restores the Skill if catalogue refresh fails."""
    manager = SkillManager(hass)
    manager._user_skills_dir = tmp_path / "skills"
    target = manager.user_skills_dir / "alpha"
    _write_skill(target, description="Alpha")
    await manager.async_load_skills()

    assert await manager.async_remove_skill("alpha") is True
    assert not target.exists()
    assert manager.get_skill("alpha") is None
    assert await manager.async_remove_skill("alpha") is False

    _write_skill(target, description="Restored candidate")

    async def fail_discovery():
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(manager, "_async_discover_skills_locked", fail_discovery)
    with pytest.raises(RuntimeError, match="refresh failed"):
        await manager.async_remove_skill("alpha")
    assert target.exists()
    assert not any(manager.staging_dir.glob("alpha.remove-*"))


@pytest.mark.parametrize("name", ["", ".", "..", "nested/skill", "/absolute"])
def test_direct_skill_name_validation(name: str) -> None:
    """Mutation APIs reject empty, special, nested, and absolute names."""
    with pytest.raises(HomeAssistantError, match="Invalid Skill name"):
        SkillManager._validate_direct_skill_name(name)


def test_activate_staged_skill_restores_existing_target_on_rename_failure(tmp_path: Path) -> None:
    """The synchronous activation primitive restores the old target on failure."""
    target = tmp_path / "skills" / "alpha"
    _write_skill(target, description="Old")
    missing_staged = tmp_path / "staging" / "missing"
    backup = tmp_path / "staging" / "alpha.backup"

    with pytest.raises(FileNotFoundError):
        SkillManager._activate_staged_skill_sync(missing_staged, target, backup)
    assert target.exists()
    assert not backup.exists()


def test_sync_restore_and_remove_helpers_cover_file_directory_and_missing(tmp_path: Path) -> None:
    """Filesystem helpers are idempotent across their supported path shapes."""
    target = tmp_path / "target"
    backup = tmp_path / "backup"
    _write_skill(backup)
    SkillManager._restore_removed_skill_sync(target, backup)
    assert target.exists() and not backup.exists()

    # No-op when there is nothing to restore, and when a target already exists.
    SkillManager._restore_removed_skill_sync(target, backup)
    _write_skill(backup, description="Backup")
    SkillManager._restore_removed_skill_sync(target, backup)
    assert backup.exists()

    file_path = tmp_path / "plain-file"
    file_path.write_text("x", encoding="utf-8")
    SkillManager._remove_path_sync(file_path)
    SkillManager._remove_path_sync(file_path)
    assert not file_path.exists()

    SkillManager._remove_path_sync(backup)
    assert not backup.exists()


def test_discovery_handles_missing_non_directory_and_ignored_entries(hass, tmp_path: Path) -> None:
    """Discovery safely skips roots and entries that cannot contain published Skills."""
    manager = SkillManager(hass)
    missing = tmp_path / "missing"
    assert manager._load_skills_from_dir_sync(missing) == []

    not_dir = tmp_path / "not-dir"
    not_dir.write_text("x", encoding="utf-8")
    assert manager._load_skills_from_dir_sync(not_dir) == []

    root = tmp_path / "skills"
    root.mkdir()
    (root / "plain-file").write_text("x", encoding="utf-8")
    _write_skill(root / ".hidden")
    (root / "no-skill-file").mkdir()
    assert manager._load_skills_from_dir_sync(root) == []


def test_discovery_handles_read_errors_and_both_resource_limits(hass, tmp_path: Path, monkeypatch) -> None:
    """Discovery contains read failures and obeys entry and Skill count limits."""
    manager = SkillManager(hass)

    read_error_root = tmp_path / "read-errors"
    _write_skill(read_error_root / "alpha")

    def fail_read(_path: Path) -> str:
        raise HomeAssistantError("too large")

    monkeypatch.setattr(skills_module, "read_bounded_skill_text", fail_read)
    assert manager._load_skills_from_dir_sync(read_error_root) == []

    entry_limit_root = tmp_path / "entry-limit"
    _write_skill(entry_limit_root / "alpha")
    monkeypatch.setattr(skills_module, "MAX_SKILL_DISCOVERY_ENTRIES", 0)
    assert manager._load_skills_from_dir_sync(entry_limit_root) == []

    # Restore the entry limit and force the independent discovered-Skill limit.
    monkeypatch.setattr(skills_module, "MAX_SKILL_DISCOVERY_ENTRIES", 100)
    monkeypatch.setattr(skills_module, "MAX_DISCOVERED_SKILLS", 0)
    assert manager._load_skills_from_dir_sync(entry_limit_root) == []


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

    class FakeManager(skills_module.SkillManager):
        _instance = None

    hass = SimpleNamespace(
        config=SimpleNamespace(config_dir="/config"),
        data={},
        async_add_executor_job=AsyncMock(
            return_value=[
                (Path("/skills/good/SKILL.md"), "good"),
                (Path("/skills/bad/SKILL.md"), "bad"),
                (Path("/skills/ignored/SKILL.md"), "ignore"),
            ]
        ),
    )
    monkeypatch.setattr(skills_module, "SkillManager", FakeManager)
    monkeypatch.setattr(skills_module, "SkillMdParser", FakeParser)

    manager = FakeManager(hass)
    await manager.async_load_skills()

    assert set(manager._skills) == {"good"}
    assert manager._initialized is True


@pytest.mark.asyncio
async def test_skill_first_load_failure_clears_singleton_and_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeParser:
        @staticmethod
        def parse(_content: str, _path: Path, _base: Path) -> Any:
            return None

    class FakeManager(skills_module.SkillManager):
        _instance = None

    executor = AsyncMock(side_effect=[OSError("disk unavailable"), []])
    hass = SimpleNamespace(
        config=SimpleNamespace(config_dir="/config"),
        data={},
        async_add_executor_job=executor,
    )
    monkeypatch.setattr(skills_module, "SkillManager", FakeManager)
    monkeypatch.setattr(skills_module, "SkillMdParser", FakeParser)

    with pytest.raises(OSError, match="disk unavailable"):
        await FakeManager.async_get_instance(hass, "/custom-skills")
    assert FakeManager._instance is None

    manager = await FakeManager.async_get_instance(hass, "/custom-skills")
    assert FakeManager._instance is manager
    assert manager._user_skills_dir == Path("/custom-skills")
    assert manager._initialized is True
    assert FakeManager.get_loaded_instance() is manager
    assert await FakeManager.async_get_instance(hass, "/ignored-after-init") is manager

class _Parser:
    @staticmethod
    def parse(_content: str, _path: Path, _base: Path) -> None:
        return None


def _skill_manager_type():
    class FakeManager(skills_module.SkillManager):
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
    monkeypatch.setattr(skills_module, "SkillManager", manager_type)
    monkeypatch.setattr(skills_module, "SkillMdParser", _Parser)

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
    monkeypatch.setattr(skills_module, "SkillManager", manager_type)
    monkeypatch.setattr(skills_module, "SkillMdParser", _Parser)

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
    monkeypatch.setattr(skills_module, "SkillManager", manager_type)
    monkeypatch.setattr(skills_module, "SkillMdParser", _Parser)

    with pytest.raises(OSError, match="load failed"):
        await manager_type.async_get_instance(hass)

    assert manager_type._instance is replacement_holder["manager"]


def test_loaded_skill_getter_returns_none_for_uninitialized_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callers must not observe a singleton before its catalogue is complete."""
    manager_type = _skill_manager_type()
    manager_type._instance = manager_type(SimpleNamespace())
    monkeypatch.setattr(skills_module, "SkillManager", manager_type)
    monkeypatch.setattr(skills_module, "SkillMdParser", _Parser)

    assert manager_type.get_loaded_instance() is None
