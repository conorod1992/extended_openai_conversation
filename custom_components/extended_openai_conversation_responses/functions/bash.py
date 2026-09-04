"""Bash tool for explicitly trusted shell command execution."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import os
from pathlib import Path
import re
import signal

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, llm

from ..const import (
    DEFAULT_WORKING_DIRECTORY,
    SHELL_DENY_PATTERNS,
    SHELL_OUTPUT_LIMIT,
    SHELL_TIMEOUT,
)
from ..regex_execution import async_run_configurable_regex
from .base import Function

_LOGGER = logging.getLogger(__name__)
_STREAM_CHUNK_SIZE = 4096
_SHELL_CANCEL_GRACE_SECONDS = 1.0
_SHELL_CLEANUP_WAIT_SECONDS = 5.0
# UTF-8 uses at most four bytes per Unicode code point. Retaining this many bytes
# preserves the existing character limit while bounding pipe memory during execution.
_SHELL_OUTPUT_BYTE_LIMIT = SHELL_OUTPUT_LIMIT * 4


async def _read_bounded_stream(
    stream: asyncio.StreamReader | None,
    limit: int = _SHELL_OUTPUT_BYTE_LIMIT,
) -> tuple[bytes, bool]:
    """Drain a subprocess pipe while retaining at most ``limit`` bytes."""
    if stream is None:
        return b"", False

    retained = bytearray()
    truncated = False
    while chunk := await stream.read(_STREAM_CHUNK_SIZE):
        remaining = max(0, limit - len(retained))
        if remaining:
            retained.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    return bytes(retained), truncated


def _decode_bounded_output(content: bytes, truncated: bool) -> str:
    """Decode bounded subprocess output and preserve the character limit."""
    text = content.decode("utf-8", errors="replace")
    if len(text) > SHELL_OUTPUT_LIMIT:
        text = text[:SHELL_OUTPUT_LIMIT]
        truncated = True
    if truncated:
        text += "\n... (truncated, output too large)"
    return text


async def _async_cleanup_process(
    process: asyncio.subprocess.Process,
    stdout_task: asyncio.Task[tuple[bytes, bool]],
    stderr_task: asyncio.Task[tuple[bytes, bool]],
    *,
    graceful: bool,
) -> None:
    """Stop a subprocess and settle pipe readers with bounded waits."""

    def stop_process(*, terminate: bool) -> None:
        if process.returncode is not None:
            return
        if os.name == "posix":
            sig = signal.SIGTERM if terminate else signal.SIGKILL
            with suppress(ProcessLookupError):
                os.killpg(process.pid, sig)
        elif terminate:
            with suppress(ProcessLookupError):
                process.terminate()
        else:
            with suppress(ProcessLookupError):
                process.kill()

    if process.returncode is None and graceful:
        stop_process(terminate=True)
        with suppress(TimeoutError):
            await asyncio.wait_for(
                process.wait(), timeout=_SHELL_CANCEL_GRACE_SECONDS
            )

    if process.returncode is None:
        stop_process(terminate=False)

    if process.returncode is None:
        with suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=_SHELL_CLEANUP_WAIT_SECONDS)

    readers = (stdout_task, stderr_task)
    try:
        await asyncio.wait_for(
            asyncio.gather(*readers, return_exceptions=True),
            timeout=_SHELL_CLEANUP_WAIT_SECONDS,
        )
    except TimeoutError:
        for reader in readers:
            if not reader.done():
                reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)


class BashFunction(Function):
    """Execute explicitly trusted shell commands with defensive guardrails only."""

    def get_working_dir(self, hass: HomeAssistant) -> Path:
        """Get the default working directory for bash operations."""
        return Path(hass.config.config_dir) / DEFAULT_WORKING_DIRECTORY

    def __init__(self) -> None:
        """Initialize bash tool."""
        schema = vol.Schema(
            {
                vol.Required("command"): cv.template,
                # Arbitrary shell execution cannot be sandboxed by lexical command
                # inspection. Requiring a positive acknowledgement makes Bash opt-in
                # even for legacy Function Tool definitions that were implicitly on.
                vol.Optional("allow_unsafe_shell", default=False): bool,
                vol.Optional("cwd"): cv.template,
                vol.Optional("restrict_to_workspace", default=True): bool,
                vol.Optional("allow_patterns"): vol.All(cv.ensure_list, [str]),
            }
        )
        super().__init__(schema)

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        """Return whether path is root or lies beneath root by path components."""
        return path == root or path.is_relative_to(root)

    def _guard_command(
        self,
        command: str,
        cwd: str | Path,
        restrict_to_workspace: bool,
        allow_patterns: list[str] | None = None,
    ) -> None:
        """Apply best-effort defensive checks; this is not a shell sandbox."""
        cwd_path = Path(cwd).resolve()

        # Deny patterns check. These reduce accidental damage only; shell syntax can
        # construct equivalent commands dynamically, so they are never authorization.
        for pattern in SHELL_DENY_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                raise ValueError(
                    f"Command blocked by defensive policy: matches pattern '{pattern}'"
                )
        # Catch common reordered recursive-rm flags as defence-in-depth. This is
        # deliberately not presented as comprehensive shell parsing.
        if re.search(r"\brm\s+-[A-Za-z]*r[A-Za-z]*", command, re.IGNORECASE):
            raise ValueError("Command blocked by defensive policy: recursive rm")

        # Allow patterns check
        if allow_patterns:
            lower = command.lower()
            if not any(re.search(p, lower) for p in allow_patterns):
                raise ValueError("Command blocked: not in allowlist")

        # Path restriction check when restrict_to_workspace is enabled. The
        # configured cwd is the workspace root. This is a defensive path guard,
        # not an OS sandbox: arbitrary executable code can construct paths
        # dynamically, so callers must still treat Bash as trusted.
        if restrict_to_workspace:
            # Block path traversal patterns.
            if "../" in command or "..\\" in command:
                raise ValueError("Command blocked: path traversal detected")

            # Block directory changes that would escape the configured cwd even when
            # the command uses the bare `cd ..` form rather than a ../ path token.
            for match in re.finditer(
                r"(?:^|[;&|]\s*)cd\s+([^\s;&|]+)", command, re.IGNORECASE
            ):
                raw_target = match.group(1).strip("\"'")
                if not raw_target or raw_target == ".":
                    continue
                if any(token in raw_target for token in ("$", "`", "$(`")):
                    raise ValueError(
                        "Command blocked: dynamic cd target cannot be verified"
                    )
                cd_target = Path(raw_target)
                if not cd_target.is_absolute():
                    cd_target = cwd_path / cd_target
                cd_target = cd_target.resolve()
                if not self._is_within(cd_target, cwd_path):
                    raise ValueError(
                        "Command blocked: cd target is outside working directory"
                    )

            # Extract and validate literal absolute paths in command text.
            win_paths = re.findall(r"[A-Za-z]:\\[^\\\"\' ]+", command)
            posix_paths = re.findall(r"(?<!\w)/[^\s\"\']+", command)

            for raw in win_paths + posix_paths:
                try:
                    p = Path(raw).resolve()
                except Exception:
                    continue

                if not self._is_within(p, cwd_path):
                    raise ValueError(
                        f"Command blocked by defensive path guard (path '{raw}' outside working dir).\nSet 'restrict_to_workspace: false' to permit literal paths outside the working directory."
                    )

    async def _async_guard_command(
        self,
        hass: HomeAssistant,
        command: str,
        cwd: str | Path,
        restrict_to_workspace: bool,
        allow_patterns: list[str] | None = None,
    ) -> None:
        """Run command guards, including configured regex allowlists, off-loop."""
        await async_run_configurable_regex(
            hass,
            self._guard_command,
            command,
            cwd,
            restrict_to_workspace,
            allow_patterns,
        )

    async def execute(
        self,
        hass: HomeAssistant,
        function_config,
        arguments,
        llm_context: llm.LLMContext | None,
        exposed_entities,
    ):
        """Execute an explicitly enabled shell command."""
        if function_config.get("allow_unsafe_shell") is not True:
            return {
                "error": (
                    "Bash execution is disabled. Set allow_unsafe_shell: true in "
                    "the Function Tool configuration to explicitly allow arbitrary "
                    "shell commands with Home Assistant's OS privileges."
                )
            }

        command_template = function_config.get("command")
        command = command_template.async_render(arguments, parse_result=False)

        default_workspace = self.get_working_dir(hass).resolve()

        # A configured cwd intentionally defines a custom workspace root. Relative
        # values remain relative to the integration's default workspace.
        cwd_template = function_config.get("cwd")
        if cwd_template:
            cwd = Path(cwd_template.async_render(arguments, parse_result=False))
            if not cwd.is_absolute():
                cwd = default_workspace / cwd
        else:
            cwd = default_workspace
        cwd = cwd.resolve()

        restrict_to_workspace = function_config.get("restrict_to_workspace", True)

        raw_timeout = arguments.get("timeout", SHELL_TIMEOUT)
        try:
            timeout = float(raw_timeout)
        except TypeError, ValueError:
            return {"error": "Timeout must be a number"}
        if timeout <= 0:
            return {"error": "Timeout must be greater than zero"}
        timeout = min(timeout, float(SHELL_TIMEOUT))

        allow_patterns = function_config.get("allow_patterns", [])

        try:
            await self._async_guard_command(
                hass,
                command,
                cwd=cwd,
                restrict_to_workspace=restrict_to_workspace,
                allow_patterns=allow_patterns,
            )
        except ValueError as err:
            return {"error": str(err)}

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name == "posix",
            )
            stdout_task = asyncio.create_task(_read_bounded_stream(process.stdout))
            stderr_task = asyncio.create_task(_read_bounded_stream(process.stderr))

            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
                (
                    (stdout, stdout_truncated),
                    (stderr, stderr_truncated),
                ) = await asyncio.gather(stdout_task, stderr_task)
            except TimeoutError:
                await _async_cleanup_process(
                    process,
                    stdout_task,
                    stderr_task,
                    graceful=False,
                )
                return {"error": f"Command timed out after {timeout:g} seconds"}
            except asyncio.CancelledError:
                await asyncio.shield(
                    _async_cleanup_process(
                        process,
                        stdout_task,
                        stderr_task,
                        graceful=True,
                    )
                )
                raise

            stdout_text = _decode_bounded_output(stdout, stdout_truncated)
            stderr_text = _decode_bounded_output(stderr, stderr_truncated)

            result = {
                "exit_code": process.returncode,
                "stdout": stdout_text,
            }

            if stderr_text:
                result["stderr"] = stderr_text

        except Exception as err:
            _LOGGER.error(err)
            return {"error": str(err)}

        return result
