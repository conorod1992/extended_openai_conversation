"""Skill management for the Extended OpenAI Conversation (Responses) component."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import logging
from pathlib import Path
import re
import shutil
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
_SKILL_MANAGER_INSTANCE_LOCK = "extended_openai_conversation_responses.skill_manager_instance_lock"


@dataclass
class Skill:
    """Represent metadata for one installed Skill."""

    name: str
    description: str
    path: Path

    def __post_init__(self) -> None:
        """Validate Skill metadata."""
        if not self.name:
            raise ValueError("Skill name is required")
        if len(self.name) > 64:
            raise ValueError("Skill name must be 64 characters or less")
        if len(self.description) > 1024:
            raise ValueError("Skill description must be 1024 characters or less")


class SkillMdParser:
    """Parse SKILL.md files following the Agent Skills format."""

    FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

    @classmethod
    def parse(
        cls, content: str, skill_path: Path, skills_base_dir: Path
    ) -> Skill | None:
        """Parse metadata without loading the Skill body into runtime state."""
        match = cls.FRONTMATTER_PATTERN.match(content)
        if not match:
            _LOGGER.warning(
                "Invalid SKILL.md format in %s: missing frontmatter", skill_path
            )
            return None
        try:
            frontmatter = yaml.safe_load(match.group(1))
        except yaml.YAMLError as err:
            _LOGGER.warning("Failed to parse YAML frontmatter in %s: %s", skill_path, err)
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
            name = str(skill_dir.relative_to(skills_base_dir))
        except ValueError:
            name = skill_dir.name
        try:
            return Skill(name=name, description=description, path=skill_path)
        except ValueError as err:
            _LOGGER.warning("Invalid skill in %s: %s", skill_path, err)
            return None

    @classmethod
    def extract_body(cls, content: str) -> str:
        """Extract the markdown body after frontmatter."""
        match = cls.FRONTMATTER_PATTERN.match(content)
        if not match:
            return content
        return content[match.end() :].strip()


class SkillManager:
    """Own installed-Skill discovery, publication, removal, and canonical reads.

    The shared filesystem lock protects only the global installed-Skill resource.
    Per-agent configuration and lifecycle operations remain under the existing agent
    maintenance gate.
    """

    _instance: SkillManager | None = None
    filesystem_concurrency_safe = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the manager without performing filesystem I/O."""
        self._hass = hass
        self._skills: dict[str, Skill] = {}
        self._user_skills_dir: Path | None = None
        self._filesystem_lock = asyncio.Lock()
        self._initialized = False

    @classmethod
    def get_loaded_instance(cls) -> SkillManager | None:
        """Return an initialized singleton without triggering discovery."""
        manager = cls._instance
        return manager if manager is not None and manager._initialized else None

    @classmethod
    async def async_get_instance(
        cls, hass: HomeAssistant, user_skills_dir: str | None = None
    ) -> SkillManager:
        """Return one singleton and make concurrent first callers await discovery."""
        lock = hass.data.setdefault(_SKILL_MANAGER_INSTANCE_LOCK, asyncio.Lock())
        async with lock:
            manager = cls._instance
            if manager is None or manager._hass is not hass:
                manager = cls(hass)
                if user_skills_dir:
                    manager._user_skills_dir = Path(user_skills_dir)
                cls._instance = manager
            elif (
                user_skills_dir
                and manager._user_skills_dir is None
                and not manager._initialized
            ):
                manager._user_skills_dir = Path(user_skills_dir)
            try:
                await manager.async_initialize()
            except BaseException:
                if not manager._initialized and cls._instance is manager:
                    cls._instance = None
                raise
            return manager

    @property
    def user_skills_dir(self) -> Path:
        """Return the directory scanned as installed Skills."""
        if self._user_skills_dir is None:
            self._user_skills_dir = (
                Path(self._hass.config.config_dir)
                / DEFAULT_WORKING_DIRECTORY
                / DEFAULT_SKILLS_DIRECTORY
            )
        return self._user_skills_dir

    @property
    def staging_dir(self) -> Path:
        """Return a staging directory outside the installed-Skills scan root."""
        skills_dir = self.user_skills_dir
        return skills_dir.parent / f".{skills_dir.name}.staging"

    async def _async_run_locked[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        """Keep the mutation boundary owned until work reaches a stable state."""
        async with self._filesystem_lock:
            task: asyncio.Future[T] = asyncio.ensure_future(operation())
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                with suppress(BaseException):
                    await task
                raise

    async def async_initialize(self) -> None:
        """Perform first discovery exactly once for concurrent callers."""

        async def initialize_locked() -> None:
            if self._initialized:
                return
            loaded = await self._async_discover_skills_locked()
            self._skills = loaded
            self._initialized = True
            _LOGGER.info("Loaded %d skills", len(loaded))

        await self._async_run_locked(initialize_locked)

    async def async_load_skills(self) -> None:
        """Rescan and atomically replace the published catalogue."""

        async def load_locked() -> None:
            loaded = await self._async_discover_skills_locked()
            self._skills = loaded
            self._initialized = True
            _LOGGER.info("Loaded %d skills", len(loaded))

        await self._async_run_locked(load_locked)

    async def _async_discover_skills_locked(self) -> dict[str, Skill]:
        """Build a complete catalogue while the filesystem boundary is owned."""
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
        """Prevent a canonical Skill read from crossing publish/remove."""
        async with self._filesystem_lock:
            yield

    async def async_publish_staged_skill(self, skill_name: str, staged_dir: Path) -> None:
        """Publish a completed staged Skill and refresh the catalogue atomically."""
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
                self._skills = await self._async_discover_skills_locked()
                raise
            self._skills = loaded
            await self._hass.async_add_executor_job(self._remove_path_sync, backup)

        await self._async_run_locked(publish_locked)

    async def async_remove_skill(self, skill_name: str) -> bool:
        """Remove one Skill without exposing a partially deleted directory."""
        self._validate_direct_skill_name(skill_name)
        target = self.user_skills_dir / skill_name
        backup = self.staging_dir / f"{skill_name}.remove-{uuid4().hex}"

        async def remove_locked() -> bool:
            if not await self._hass.async_add_executor_job(target.exists):
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
        """Publish using same-filesystem renames with rollback."""
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
        """Read bounded metadata only from published, non-temporary Skill paths."""
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
                results.append((skill_file, read_bounded_skill_text(skill_file)))
            except HomeAssistantError as err:
                _LOGGER.warning("Failed to read skill file %s: %s", skill_file, err)
        return results

    def get_skill(self, name: str) -> Skill | None:
        """Get one Skill from the current atomic catalogue snapshot."""
        return self._skills.get(name)

    def get_all_skills(self) -> list[Skill]:
        """Get all Skills from the current atomic catalogue snapshot."""
        return list(self._skills.values())


# runtime_hardening predates the manager-owned boundary. These markers preserve the
# existing installation order while preventing it from replacing the authoritative
# manager implementations with a second lock/catalogue path.
SkillManager.async_load_skills._extended_openai_atomic_load = True  # type: ignore[attr-defined]
SkillManager.async_get_instance.__func__._extended_openai_init_guard = True  # type: ignore[attr-defined]
SkillManager.get_loaded_instance.__func__._extended_openai_loaded_guard = True  # type: ignore[attr-defined]
