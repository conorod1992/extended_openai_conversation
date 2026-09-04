"""File functions for read, write, and edit operations."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, llm
from homeassistant.helpers.template import Template

from ..const import (
    DEFAULT_ALLOWED_DIRS,
    DEFAULT_WORKING_DIRECTORY,
    DOMAIN,
    FILE_READ_SIZE_LIMIT,
)
from ..skills import SkillManager
from .base import Function

_LOGGER = logging.getLogger(__name__)
_FILE_EDIT_LOCKS = f"{DOMAIN}.file_edit_locks"
type _FileFingerprint = tuple[int, int, int, int, int]


def _fingerprint(stat_result: os.stat_result) -> _FileFingerprint:
    """Return a cheap identity/version fingerprint for conflict detection."""
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_ctime_ns,
    )


def _read_text_bounded(path: Path) -> str:
    """Read UTF-8 text without allowing a size-check/read race to grow memory."""
    file_size = path.stat().st_size
    if file_size > FILE_READ_SIZE_LIMIT:
        raise ValueError(
            f"File too large: {file_size} bytes (limit: {FILE_READ_SIZE_LIMIT})"
        )
    with path.open("rb") as handle:
        content = handle.read(FILE_READ_SIZE_LIMIT + 1)
    if len(content) > FILE_READ_SIZE_LIMIT:
        raise ValueError(
            "File grew beyond the read limit while it was being read "
            f"(limit: {FILE_READ_SIZE_LIMIT} bytes)"
        )
    return content.decode("utf-8")


def _read_text_bounded_snapshot(path: Path) -> tuple[str, _FileFingerprint]:
    """Read bounded text and capture the exact file version that was read."""
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if before.st_size > FILE_READ_SIZE_LIMIT:
            raise ValueError(
                f"File too large: {before.st_size} bytes "
                f"(limit: {FILE_READ_SIZE_LIMIT})"
            )
        content = handle.read(FILE_READ_SIZE_LIMIT + 1)
        after = os.fstat(handle.fileno())

    if len(content) > FILE_READ_SIZE_LIMIT:
        raise ValueError(
            "File grew beyond the read limit while it was being read "
            f"(limit: {FILE_READ_SIZE_LIMIT} bytes)"
        )
    if _fingerprint(before) != _fingerprint(after):
        raise RuntimeError("File changed while it was being read; retry the edit")
    return content.decode("utf-8"), _fingerprint(after)


def _atomic_replace_text(path: Path, content: str) -> int:
    """Atomically write text, preserving an existing file's mode when present."""
    encoded = content.encode("utf-8")
    if len(encoded) > FILE_READ_SIZE_LIMIT:
        raise ValueError(
            "File would exceed the size limit: "
            f"{len(encoded)} bytes (limit: {FILE_READ_SIZE_LIMIT})"
        )

    current_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    fd, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, current_mode)
        os.replace(temp_path, path)
    finally:
        with suppress(FileNotFoundError):
            temp_path.unlink()
    return len(encoded)


def _atomic_replace_text_if_unchanged(
    path: Path, content: str, expected: _FileFingerprint
) -> int:
    """Atomically replace text only when the path still identifies the read version."""
    try:
        current = _fingerprint(path.stat())
    except FileNotFoundError as err:
        raise RuntimeError("File changed since it was read; retry the edit") from err
    if current != expected:
        raise RuntimeError("File changed since it was read; retry the edit")
    return _atomic_replace_text(path, content)


def _get_edit_lock(hass: HomeAssistant, path: Path) -> asyncio.Lock:
    """Return the integration-wide lock for one canonical target path."""
    locks: dict[str, asyncio.Lock] = hass.data.setdefault(_FILE_EDIT_LOCKS, {})
    key = os.path.normcase(str(path))
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    return lock


class FileFunction(Function):
    """Base class for file-related functions."""

    def get_working_dir(self, hass: HomeAssistant) -> Path:
        """Get the default working directory for file operations."""
        return Path(hass.config.config_dir) / DEFAULT_WORKING_DIRECTORY

    def to_absolute_path(
        self, hass: HomeAssistant, path: str, base_dir: Path | None = None
    ) -> Path:
        """Convert path to absolute path."""
        p = Path(path)
        if p.is_absolute():
            return p

        if base_dir is None:
            base_dir = Path(hass.config.config_dir)

        return base_dir / p

    def _resolve_path(
        self,
        hass: HomeAssistant,
        path: str,
        allow_dirs: list[str],
    ) -> Path:
        """Resolve path relative to working directory."""
        workdir = self.get_working_dir(hass)
        target = self.to_absolute_path(hass, path, workdir).resolve()

        # Resolve both sides and use a real path-component containment check.
        # String prefixes are unsafe here: /config/workspace_backup starts with
        # /config/workspace but is not inside it.
        allowed = False
        for allow_dir in allow_dirs:
            allowed_path = Path(allow_dir).resolve()
            if target == allowed_path or target.is_relative_to(allowed_path):
                allowed = True
                break

        if not allowed:
            raise PermissionError(
                f"Access denied: path '{path}' is not in allowed directories"
            )

        return target

    def _render_allow_dirs(
        self,
        hass: HomeAssistant,
        allow_dirs: list[Template],
        arguments: dict[str, Any],
        *,
        include_defaults: bool = True,
    ) -> list[str]:
        """Render allow_dir templates."""
        all_allow_dirs = (
            [str(self.to_absolute_path(hass, d)) for d in DEFAULT_ALLOWED_DIRS]
            if include_defaults
            else []
        )

        # Add custom allow_dir if specified.
        if allow_dirs:
            template_arguments = {
                "config_dir": hass.config.config_dir,
            }
            template_arguments.update(arguments)
            custom_dirs = [
                template.async_render(template_arguments, parse_result=False)
                for template in allow_dirs
            ]
            all_allow_dirs.extend(custom_dirs)

        return all_allow_dirs


class ReadFileFunction(FileFunction):
    """Read file contents."""

    def __init__(self) -> None:
        """Initialize read file tool."""
        schema = vol.Schema(
            {
                vol.Required("path"): cv.template,
                vol.Optional("allow_dir"): vol.All(cv.ensure_list, [cv.template]),
                vol.Optional("restrict_to_allow_dir", default=False): bool,
            }
        )
        super().__init__(schema)

    async def execute(
        self,
        hass: HomeAssistant,
        function_config,
        arguments,
        llm_context: llm.LLMContext | None,
        exposed_entities,
    ):
        """Read file contents."""
        path_template = function_config.get("path")
        path_str = path_template.async_render(arguments, parse_result=False)

        # The built-in load_skill tool predates strict per-tool allow directories.
        # Detect that template and bind it to the resolved skill directory so a
        # relative file such as ../other_skill/SKILL.md cannot cross skill roots.
        template_source = str(getattr(path_template, "template", ""))
        if "extended_openai.skill_dir" in template_source:
            manager = SkillManager._instance
            skill_name = arguments.get("name")
            skill = manager.get_skill(str(skill_name)) if manager is not None else None
            if skill is None:
                return {"error": f"Skill not found: {skill_name}"}
            allow_dirs = [str(skill.path.parent.resolve())]
        else:
            allow_dirs = self._render_allow_dirs(
                hass,
                function_config.get("allow_dir", []),
                arguments,
                include_defaults=not function_config.get(
                    "restrict_to_allow_dir", False
                ),
            )

        try:
            target_path = self._resolve_path(hass, path_str, allow_dirs)

            if not target_path.exists():
                return {"error": f"File not found: {path_str}"}

            if not target_path.is_file():
                return {"error": f"Not a file: {path_str}"}

            file_size = target_path.stat().st_size
            content = await hass.async_add_executor_job(_read_text_bounded, target_path)

        except Exception as err:
            _LOGGER.error(err)
            return {"error": str(err)}

        return {"content": content, "size": file_size}


class WriteFileFunction(FileFunction):
    """Write content to file."""

    def __init__(self) -> None:
        """Initialize write file tool."""
        schema = vol.Schema(
            {
                vol.Required("path"): cv.template,
                vol.Required("content"): cv.template,
                vol.Optional("allow_dir"): vol.All(cv.ensure_list, [cv.template]),
            }
        )
        super().__init__(schema)

    async def execute(
        self,
        hass: HomeAssistant,
        function_config,
        arguments,
        llm_context: llm.LLMContext | None,
        exposed_entities,
    ):
        """Write content to file."""
        path_template = function_config.get("path")
        path_str = path_template.async_render(arguments, parse_result=False)
        content_template = function_config.get("content")
        content = content_template.async_render(arguments, parse_result=False)
        allow_dirs = self._render_allow_dirs(
            hass, function_config.get("allow_dir", []), arguments
        )

        try:
            target_path = self._resolve_path(hass, path_str, allow_dirs)
            bytes_written = await hass.async_add_executor_job(
                _atomic_replace_text, target_path, content
            )

        except Exception as err:
            _LOGGER.exception("File write error: %s", err)
            return {"error": str(err)}

        return {
            "success": True,
            "path": str(target_path),
            "bytes_written": bytes_written,
        }


class EditFileFunction(FileFunction):
    """Edit file with find-and-replace."""

    def __init__(self) -> None:
        """Initialize edit file tool."""
        schema = vol.Schema(
            {
                vol.Required("path"): cv.template,
                vol.Required("old_text"): cv.template,
                vol.Required("new_text"): cv.template,
                vol.Optional("allow_dir"): vol.All(cv.ensure_list, [cv.template]),
            }
        )
        super().__init__(schema)

    async def execute(
        self,
        hass: HomeAssistant,
        function_config,
        arguments,
        llm_context: llm.LLMContext | None,
        exposed_entities,
    ):
        """Edit file with find-and-replace."""
        path_template = function_config.get("path")
        path_str = path_template.async_render(arguments, parse_result=False)
        old_text_template = function_config.get("old_text")
        old_text = old_text_template.async_render(arguments, parse_result=False)
        new_text_template = function_config.get("new_text")
        new_text = new_text_template.async_render(arguments, parse_result=False)
        allow_dirs = self._render_allow_dirs(
            hass, function_config.get("allow_dir", []), arguments
        )

        try:
            target_path = self._resolve_path(hass, path_str, allow_dirs)
            async with _get_edit_lock(hass, target_path):
                if not target_path.exists():
                    return {"error": f"File not found: {path_str}"}

                if not target_path.is_file():
                    return {"error": f"Not a file: {path_str}"}

                content, fingerprint = await hass.async_add_executor_job(
                    _read_text_bounded_snapshot, target_path
                )

                if old_text not in content:
                    return {"error": f"Text not found in file: {old_text[:50]}..."}

                occurrence_count = content.count(old_text)
                if occurrence_count > 1:
                    return {
                        "error": f"Text appears {occurrence_count} times in file. "
                        "Please provide more specific text to ensure single replacement."
                    }

                new_content = content.replace(old_text, new_text, 1)
                await hass.async_add_executor_job(
                    _atomic_replace_text_if_unchanged,
                    target_path,
                    new_content,
                    fingerprint,
                )

        except Exception as err:
            _LOGGER.error(err)
            return {"error": str(err)}

        return {
            "success": True,
            "path": str(target_path),
            "replacements": 1,
        }
