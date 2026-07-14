"""Skill management for the Extended OpenAI Conversation (Responses) component."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re

import yaml

from homeassistant.core import HomeAssistant

from .const import DEFAULT_SKILLS_DIRECTORY, DEFAULT_WORKING_DIRECTORY, SKILL_FILE_NAME

_LOGGER = logging.getLogger(__name__)


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
        """Parse SKILL.md content and return a Skill object.

        Uses directory path as name for identification.
        Parses frontmatter for description.
        The body content is loaded on-demand via load_skill function.

        Args:
            content: Full content of SKILL.md file
            skill_path: Path to the SKILL.md file
            skills_base_dir: Base directory for skills (used to calculate relative path)

        Returns:
            Skill object with metadata, or None if parsing fails
        """
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

        # Calculate relative directory path from skills base to use as name
        skill_dir = skill_path.parent
        try:
            relative_path = skill_dir.relative_to(skills_base_dir)
            name = str(relative_path)
        except ValueError:
            # Fallback to directory name if not relative to base
            name = skill_dir.name

        try:
            return Skill(
                name=name,
                description=description,
                path=skill_path,
            )
        except ValueError as e:
            _LOGGER.warning("Invalid skill in %s: %s", skill_path, e)
            return None

    @classmethod
    def extract_body(cls, content: str) -> str:
        """Extract the body content after frontmatter.

        Args:
            content: Full content of SKILL.md file

        Returns:
            Body content (markdown after frontmatter)
        """
        match = cls.FRONTMATTER_PATTERN.match(content)
        if not match:
            return content

        return content[match.end() :].strip()


class SkillManager:
    """Manages skills for the Extended OpenAI Conversation (Responses) component.

    Skills are loaded from the user skills directory.
    Only metadata (name, description) is loaded initially for system prompt.
    Full skill content is loaded on-demand via load_skill function.

    This class is designed to be used as a singleton - it only handles
    skill loading and reading. Enabled skills list is managed
    per ConversationEntity via subentry.data[CONF_SKILLS].
    """

    _instance: SkillManager | None = None

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the skill manager.

        Args:
            hass: Home Assistant instance
        """
        self._hass = hass
        self._skills: dict[str, Skill] = {}  # key is skill name (directory path)
        self._user_skills_dir: Path | None = None

    @classmethod
    async def async_get_instance(
        cls, hass: HomeAssistant, user_skills_dir: str | None = None
    ) -> SkillManager:
        """Get or create the singleton instance and load skills.

        Args:
            hass: Home Assistant instance
            user_skills_dir: Optional custom user skills directory path

        Returns:
            The singleton SkillManager instance with skills loaded
        """
        if cls._instance is None:
            cls._instance = cls(hass)
            if user_skills_dir:
                cls._instance._user_skills_dir = Path(user_skills_dir)
            await cls._instance.async_load_skills()
        return cls._instance

    @property
    def user_skills_dir(self) -> Path:
        """Get the user skills directory path."""
        if self._user_skills_dir is None:
            self._user_skills_dir = (
                Path(self._hass.config.config_dir)
                / DEFAULT_WORKING_DIRECTORY
                / DEFAULT_SKILLS_DIRECTORY
            )
        return self._user_skills_dir

    async def async_load_skills(self) -> None:
        """Load all skills from the user skills directory.

        Only loads metadata (frontmatter) from SKILL.md files.
        """
        self._skills.clear()

        skills_data = await self._hass.async_add_executor_job(
            self._load_skills_from_dir_sync, self.user_skills_dir
        )

        for skill_path, content in skills_data:
            try:
                skill = SkillMdParser.parse(content, skill_path, self.user_skills_dir)
                if skill:
                    self._skills[skill.name] = skill
                    _LOGGER.debug(
                        "Loaded skill: %s from %s",
                        skill.name,
                        skill_path,
                    )
            except Exception as e:
                _LOGGER.exception(
                    "Unexpected error loading skill from %s: %s", skill_path, e
                )

        _LOGGER.info("Loaded %d skills", len(self._skills))

    def _load_skills_from_dir_sync(self, skills_dir: Path) -> list[tuple[Path, str]]:
        """Load skill files synchronously from a specific directory (run in executor).

        Args:
            skills_dir: Path to the skills directory to load from

        Returns:
            List of tuples (skill_file_path, file_content)
        """
        results: list[tuple[Path, str]] = []

        if not skills_dir.exists():
            _LOGGER.debug("Skills directory does not exist: %s", skills_dir)
            return results

        if not skills_dir.is_dir():
            _LOGGER.warning("Skills path is not a directory: %s", skills_dir)
            return results

        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_file = skill_dir / SKILL_FILE_NAME
            if not skill_file.exists():
                _LOGGER.debug("No SKILL.md found in %s", skill_dir)
                continue

            try:
                content = skill_file.read_text(encoding="utf-8")
                results.append((skill_file, content))
            except OSError as e:
                _LOGGER.warning("Failed to read skill file %s: %s", skill_file, e)

        return results

    def get_skill(self, name: str) -> Skill | None:
        """Get a skill by name (directory path).

        Args:
            name: The skill name (directory path)

        Returns:
            The skill if found, None otherwise
        """
        return self._skills.get(name)

    def get_all_skills(self) -> list[Skill]:
        """Get all loaded skills.

        Returns:
            List of all skills
        """
        return list(self._skills.values())
