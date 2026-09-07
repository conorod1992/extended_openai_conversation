"""Skill management for the Extended OpenAI Conversation (Responses) component."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import logging
from pathlib import Path
import re
import shutil
from typing import AsyncIterator, Awaitable, Callable, TypeVar
from uuid import uuid4

import yaml

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DEFAULT_SKILLS_DIRECTORY, DEFAULT_WORKING_DIRECTORY, SKILL_FILE_NAME
from .skill_resource_limits import (
    MAX_DISCOVERED_SKILLS,
    MAX_SKILL_DISCOVERY_ENTRIES,
    read_bounded_skill_text,
)

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")


@dataclass
class Skill:
    """Represents a skill loaded from SKILL.md.

    Only metadata (name, description) is loaded initially.
    The full content (body) is loaded on-demand via load_skill function.
    """

    name: str  # Directory path used as identifier
    description: str
    path: Path  # Path to SKILL.md file

    def __post_init__(self) -> None:
        """Validate skill fields."""
        if not self.name:
            raise ValueError("Skill name is required")
        if len(self.name) > 64:
            raise ValueError("Skill name must be 64 characters or less")
        if len(self.description) > 1024:
            raise ValueError("Skill description must be 1024 characters or less")


class SkillMdParser:
    """Parser for SKILL.md files following Agent Skills standard."""

    FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

    @classmethod
    def parse(
        cls, content: str, skill_path: Path, skills_base_dir: Path
    ) -> Skill | None:
        """Parse SKILL.md content and return a Skill object."""
        match = cls.FRONTMATTER_PATTERN.match(content)
        if not match:
            _LOGGER.warning(
                "Invalid SKILL.md format in %s: missing frontmatter", skill_path
            )
            return None

        try:
            frontmatter = yaml.safe_load(match.group(1))
        except yaml.YAMLError as e:
            _LOGGER.warning("Failed to parse YAML frontmatter in %s: %s", skill_path, e)
            return None

        if not isinstance(frontmatter, dict):
            _LOGGER.warning("Invalid frontmatter format in %s", skill_path)
            return None

        description = frontmatter.get("description")
        if not description:
            _LOGGER.warning("Missing required field (description) in %s", skill_path)
            return None

        skill_dir = skill_path.parent
        try:
            relative_path = skill_dir.relative_to(skills_base_dir)
            name = str(relative_path)
        except ValueError:
            name = skill_dir.name

        try:
            return Skill(name=name, description=description, path=skill_path)
        except ValueError as e:
            _LOGGER.warning("Invalid skill in %s: %s", skill_path, e)
            return None

    @classmethod
    def extract_body(cls, content: str) -> str:
        """Extract the body content after frontmatter."""
        match = cls.FRONTMATTER_PATTERN.match(content)
        if not match:
            return content
        return content[match.end() :].strip()


class SkillManager:
    """Manage the globally installed Skill catalogue and filesystem boundary.

    Per-agent enabled Skill names remain in agent configuration. The manager owns one
    shared lock only for installed-Skill discovery/publication and canonical Skill
    reads. It deliberately does not replace the agent-maintenance gate, which remains
    responsible for agent lifecycle/configuration operations.
    """

    _instance: SkillManager | None = None
    filesystem_concurrency_safe = True

    @classmethod
    def get_loaded_instance(cls) -> SkillManager | None:
        """Return the initialized singleton without performing discovery or I/O."""
        manager = cls._instance
        return manager if manager is not None and manager._initialized else None

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the skill manager."""
        self._hass = hass
        self._skills: dict[str, Skill] = {}
        self._user_skills_dir: Path | None = None
        self._filesystem_lock = asyncio.Lock()
        self._initialized = False

    @classmethod
    async def async_get_instance(
        cls, hass: HomeAssistant, user_skills_dir: str | None = None
    ) -> SkillManager:
        """Get the singleton and ensure every concurrent first caller awaits discovery."""
        manager = cls._instance
        if manager is None or manager._hass is not hass:
            manager = cls(hass)
            if user_skills_dir:
                manager._user_skills_dir = Path(user_skills_dir)
            cls._instance = manager
        elif user_skills_dir and manager._user_skills_dir is None and not manager._initialized:
            manager._user_skills_dir = Path(user_skills_dir)

        await manager.async_initialize()
        return manager

    @property
    def user_skills_dir(self) -> Path:
        """Get the installed-Skills directory scanned for published Skills."""
        if self._user_skills_dir is None:
            self._user_skills_dir = (
                Path(self._hass.config.config_dir)
                / DEFAULT_WORKING_DIRECTORY
                / DEFAULT_SKILLS_DIRECTORY
            )
        return self._user_skills_dir

    @property
    def staging_dir(self) -> Path:
        """Return a staging root outside the directory scanned as installed Skills."""
        skills_dir = self.user_skills_dir
        return skills_dir.parent / f".{skills_dir.name}.staging"

    async def _async_run_locked(
        self, operation: Callable[[], Awaitable[_T]]
    ) -> _T:
        """Run one filesystem operation without releasing the lock on cancellation."""
        async with self._filesystem_lock:
            task = asyncio.create_task(operation())
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # Executor work cannot be stopped safely at an arbitrary rename/read.
                # Keep the boundary owned until publication/rollback reaches a stable
                # state, then propagate the caller's cancellation.
                with suppress(BaseException):
                    await task
                raise

    async def async_initialize(self) -> None:
        """Perform first discovery exactly once for all concurrent callers."""
        async def initialize_locked() -> None:
            if self._initialized:
                return
            loaded = await self._async_discover_skills_locked()
            self._skills = loaded
            self._initialized = True
            _LOGGER.info("Loaded %d skills", len(loaded))

        await self._async_run_locked(initialize_locked)

    async def async_load_skills(self) -> None:
        """Rescan Skills and atomically publish only a complete catalogue snapshot."""
        async def load_locked() -> None:
            loaded = await self._async_discover_skills_locked()
            self._skills = loaded
            self._initialized = True
            _LOGGER.info("Loaded %d skills", len(loaded))

        await self._async_run_locked(load_locked)

    async def _async_discover_skills_locked(self) -> dict[str, Skill]:
        """Build a complete catalogue while the caller owns the filesystem lock."""
        skills_data = await self._hass.async_add_executor_job(
            self._load_skills_from_dir_sync, self.user_skills_dir
        )
        loaded: dict[str, Skill] = {}
        for skill_path, content in skills_data:
            try:
                skill = SkillMdParser.parse(content, skill_path, self.user_skills_dir)
                if skill is not None:
                    loaded[skill.name] = skill
                    _LOGGER.debug("Loaded skill: %s from %s", skill.name, skill_path)
            except Exception:
                _LOGGER.exception("Unexpected error loading skill from %s", skill_path)
        return loaded

    @asynccontextmanager
    async def async_skill_read(self) -> AsyncIterator[None]:
        """Prevent canonical Skill file reads from crossing install/remove publication."""
        async with self._filesystem_lock:
            yield

    async def async_publish_staged_skill(self, skill_name: str, staged_dir: Path) -> None:
        """Atomically publish a completed staged Skill and refresh the catalogue."""
        self._validate_direct_skill_name(skill_name)
        staging_root = self.staging_dir.resolve()
        staged = staged_dir.resolve()
        if staged == staging_root or not staged.is_relative_to(staging_root):
            raise HomeAssistantError("Skill staging path is outside the managed staging area")

        target = self.user_skills_dir / skill_name
        backup = self.staging_dir / f"{skill_name}.backup-{uuid4().hex}"

        async def publish_locked() -> None:
            await self._hass.async_add_executor_job(
                self._activate_staged_skill_sync, staged, target, backup
            )
            try:
                loaded = await self._async_discover_skills_locked()
                if skill_name not in loaded:
                    raise HomeAssistantError(
                        f"Downloaded Skill `{skill_name}` is not a valid installed Skill"
                    )
            except BaseException:
                await self._hass.async_add_executor_job(
                    self._rollback_staged_skill_sync, target, backup
                )
                restored = await self._async_discover_skills_locked()
                self._skills = restored
                raise
            self._skills = loaded
            await self._hass.async_add_executor_job(self._remove_path_sync, backup)

        await self._async_run_locked(publish_locked)

    async def async_remove_skill(self, skill_name: str) -> bool:
        """Remove one installed Skill without exposing a partially deleted directory."""
        self._validate_direct_skill_name(skill_name)
        target = self.user_skills_dir / skill_name
        backup = self.staging_dir / f"{skill_name}.remove-{uuid4().hex}"

        async def remove_locked() -> bool:
            exists = await self._hass.async_add_executor_job(target.exists)
            if not exists:
                return False
            await self._hass.async_add_executor_job(
                self._stage_removal_sync, target, backup
            )
            try:
                loaded = await self._async_discover_skills_locked()
            except BaseException:
                await self._hass.async_add_executor_job(
                    self._restore_removed_skill_sync, target, backup
                )
                raise
            self._skills = loaded
            await self._hass.async_add_executor_job(self._remove_path_sync, backup)
            return True

        return await self._async_run_locked(remove_locked)

    @staticmethod
    def _validate_direct_skill_name(skill_name: str) -> None:
        if (
            not skill_name
            or skill_name in {".", ".."}
            or Path(skill_name).name != skill_name
        ):
            raise HomeAssistantError("Invalid Skill name")

    @staticmethod
    def _activate_staged_skill_sync(staged: Path, target: Path, backup: Path) -> None:
        """Publish staged directory with rollback-safe same-filesystem renames."""
        target.parent.mkdir(parents=True, exist_ok=True)
        backup.parent.mkdir(parents=True, exist_ok=True)
        SkillManager._remove_path_sync(backup)
        had_existing = target.exists()
        if had_existing:
            target.rename(backup)
        try:
            staged.rename(target)
        except BaseException:
            if had_existing and backup.exists() and not target.exists():
                backup.rename(target)
            raise

    @staticmethod
    def _rollback_staged_skill_sync(target: Path, backup: Path) -> None:
        SkillManager._remove_path_sync(target)
        if backup.exists():
            backup.rename(target)

    @staticmethod
    def _stage_removal_sync(target: Path, backup: Path) -> None:
        backup.parent.mkdir(parents=True, exist_ok=True)
        SkillManager._remove_path_sync(backup)
        target.rename(backup)

    @staticmethod
    def _restore_removed_skill_sync(target: Path, backup: Path) -> None:
        if backup.exists() and not target.exists():
            backup.rename(target)

    @staticmethod
    def _remove_path_sync(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            with suppress(FileNotFoundError):
                path.unlink()

    def _load_skills_from_dir_sync(self, skills_dir: Path) -> list[tuple[Path, str]]:
        """Read bounded Skill metadata files from the published directory only."""
        results: list[tuple[Path, str]] = []
        if not skills_dir.exists():
            _LOGGER.debug("Skills directory does not exist: %s", skills_dir)
            return results
        if not skills_dir.is_dir():
            _LOGGER.warning("Skills path is not a directory: %s", skills_dir)
            return results

        entries_seen = 0
        for skill_dir in skills_dir.iterdir():
            entries_seen += 1
            if entries_seen > MAX_SKILL_DISCOVERY_ENTRIES:
                _LOGGER.warning(
                    "Skill discovery stopped after %d directory entries",
                    MAX_SKILL_DISCOVERY_ENTRIES,
                )
                break
            if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue

            skill_file = skill_dir / SKILL_FILE_NAME
            if not skill_file.exists():
                _LOGGER.debug("No SKILL.md found in %s", skill_dir)
                continue
            if len(results) >= MAX_DISCOVERED_SKILLS:
                _LOGGER.warning(
                    "Skill discovery stopped after %d Skills", MAX_DISCOVERED_SKILLS
                )
                break

            try:
                content = read_bounded_skill_text(skill_file)
                results.append((skill_file, content))
            except HomeAssistantError as e:
                _LOGGER.warning("Failed to read skill file %s: %s", skill_file, e)
        return results

    def get_skill(self, name: str) -> Skill | None:
        """Get a Skill from the current atomic catalogue snapshot."""
        return self._skills.get(name)

    def get_all_skills(self) -> list[Skill]:
        """Get all Skills from the current atomic catalogue snapshot."""
        return list(self._skills.values())
