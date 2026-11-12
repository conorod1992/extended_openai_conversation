"""Bash tool for explicitly trusted shell command execution."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import math
import os
from pathlib import Path
import re
import shlex
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
from ..execution_outcomes import (
    execution_completed,
    execution_started,
    owns_execution_receipts,
)
from ..function_execution import backend_failure
from ..operation_settlement import async_wait_owned_cleanup
from ..operational_errors import log_handled_failure
from ..regex_execution import async_search_configured_patterns
from .base import Function

_LOGGER = logging.getLogger(__name__)
_STREAM_CHUNK_SIZE = 4096
_SHELL_CANCEL_GRACE_SECONDS = 1.0
_SHELL_CLEANUP_WAIT_SECONDS = 5.0
# UTF-8 uses at most four bytes per Unicode code point. Retaining this many bytes
# preserves the existing character limit while bounding pipe memory during execution.
_SHELL_OUTPUT_BYTE_LIMIT = SHELL_OUTPUT_LIMIT * 4


def _without_shell_comments(command: str) -> str:
    """Remove unquoted shell comments without consuming hashes inside words."""
    result: list[str] = []
    quote: str | None = None
    escaped = False
    word_start = True
    comment = False
    for char in command:
        if comment:
            if char == "\n":
                comment = False
                word_start = True
                result.append(char)
            continue
        if escaped:
            result.append(char)
            escaped = False
            if char != "\n":
                word_start = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            result.append(char)
            continue
        if quote:
            if char == quote:
                quote = None
            result.append(char)
            continue
        if char in {"'", '"'}:
            quote = char
            word_start = False
        elif char == "#" and word_start:
            comment = True
            continue
        else:
            word_start = char.isspace() or char in "();&|<>"
        result.append(char)
    return "".join(result)


def _shell_command_tokens(
    command: str, *, skip_heredoc_bodies: bool = False
) -> list[tuple[str, bool]]:
    """Retain whether words and operator runs were unquoted and unescaped.

    ``shlex`` alone removes that distinction, making a quoted semicolon look
    like a command boundary. Decode words with shlex only after finding their
    quote-aware boundaries. This remains a defensive lexer, not a shell parser.
    Directory checks can omit ordinary heredoc bodies, consumed in declaration
    order at the next newline. Unquoted substitutions are unverifiable and fail
    closed in that mode; the other guards retain their existing behavior.
    """
    if not skip_heredoc_bodies:
        command = _without_shell_comments(command)
    tokens: list[tuple[str, bool]] = []
    pending: list[tuple[str, bool, bool]] = []
    delimiter_next = False
    strip_tabs_next = False
    arithmetic_depth = 0
    operators = "();&|<>\n"
    whitespace = " \t\r"
    index = 0
    while index < len(command):
        if skip_heredoc_bodies and command[index] == "#":
            newline = command.find("\n", index)
            index = len(command) if newline < 0 else newline
            continue
        if skip_heredoc_bodies and command[index] == "\n":
            if pending and index and command[index - 1] == "\r":
                # Bash can include CR in an unquoted delimiter, unlike this
                # lexer's whitespace rules. Never hide a tail on that mismatch.
                raise ValueError(
                    "Command blocked: heredoc CRLF delimiter cannot be verified"
                )
            if delimiter_next:
                raise ValueError("Command blocked: missing heredoc delimiter")
            tokens.append(("\n", True))
            index += 1
            for delimiter, strip_tabs, quoted in pending:
                while index < len(command):
                    end = command.find("\n", index)
                    end = len(command) if end < 0 else end + 1
                    line = command[index:end].removesuffix("\n")
                    index = end
                    if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                        break
                    if not quoted and (
                        line.endswith("\\") or any(char in line for char in "$`")
                    ):
                        raise ValueError(
                            "Command blocked: heredoc substitution or continuation cannot be verified"
                        )
            pending.clear()
            continue
        if command[index] in whitespace:
            index += 1
            continue
        start = index
        if command[index] in operators:
            while (
                index < len(command)
                and command[index] in operators
                and not (skip_heredoc_bodies and command[index] == "\n")
            ):
                index += 1
            operator = command[start:index]
            if skip_heredoc_bodies and (arithmetic_depth or "((" in operator):
                arithmetic_depth = max(
                    0, arithmetic_depth + operator.count("(") - operator.count(")")
                )
            tokens.append((operator, True))
            delimiter_next = (
                skip_heredoc_bodies
                and not arithmetic_depth
                and operator.endswith("<<")
                and not operator.endswith("<<<")
            )
            strip_tabs_next = delimiter_next and command[index : index + 1] == "-"
            if strip_tabs_next:
                index += 1
            continue
        quote: str | None = None
        word: list[str] = []
        while index < len(command):
            char = command[index]
            if skip_heredoc_bodies and not word and quote is None and char == "#":
                # An escaped newline alone does not start a shell word; let the
                # outer loop recognize the following comment, not its fake <<.
                break
            if char == "\\" and quote != "'":
                # POSIX shells remove escaped newlines before tokenization,
                # including inside double quotes. shlex retains the newline.
                if command[index : index + 2] != "\\\n":
                    word.append(command[index : index + 2])
                index += 2
                continue
            if quote:
                if char == quote:
                    quote = None
            elif char in {"'", '"'}:
                quote = char
            elif char in whitespace or char in operators:
                break
            word.append(char)
            index += 1
        raw = "".join(word)
        if not raw:
            continue
        value = shlex.split(raw, comments=False, posix=True)[0]
        tokens.append((value, value == raw))
        if (
            skip_heredoc_bodies
            and any(char in raw for char in "$`")
            and not (raw == "$" and command[index : index + 2] == "((")
        ):
            # Do not guess whether << inside expansion-like syntax is a
            # redirection. Keep the original token analysis for this script.
            return _shell_command_tokens(command)
        if delimiter_next:
            delimiter = value
            if "\n" in delimiter or any(char in delimiter for char in "$`"):
                raise ValueError(
                    "Command blocked: heredoc delimiter cannot be verified"
                )
            pending.append((delimiter, strip_tabs_next, value != raw))
            delimiter_next = False
    return tokens


def _literal_text_operands(tokens: list[tuple[str, bool]]) -> set[int]:
    """Prove a few non-path operands using the existing defensive lexer.

    This is deliberately a closed grammar, not a sed/awk parser. Unknown flags,
    programs, wrappers and expansion syntax retain ordinary path checking.
    Redirections terminate operand classification; their targets never qualify.
    """
    exempt: set[int] = set()
    start = 0
    for end in range(len(tokens) + 1):
        if end < len(tokens):
            word, literal = tokens[end]
            if not (literal and word and all(c in "();&|<>\n" for c in word)):
                continue
        segment = tokens[start:end]
        if (
            segment
            and segment[0][1]
            and not any(c in word for word, _ in segment for c in "$`")
        ):
            command = segment[0][0]
            args = [word for word, _ in segment[1:]]
            if (
                command == "awk"
                and args
                and re.fullmatch(r"/[A-Za-z0-9 _^.*+?()-]+/\s*\{print\}", args[0])
            ):
                exempt.add(start + 1)
            elif (
                command == "sed"
                and len(args) >= 2
                and args[0] == "-n"
                and re.fullmatch(
                    r"/[A-Za-z0-9 _^.*+?()-]+/,/[A-Za-z0-9 _^.*+?()-]+/p", args[1]
                )
            ):
                exempt.add(start + 2)
            elif command == "grep" and len(args) >= 2 and args[0] in {"-E", "-F", "-e"}:
                if not args[1].startswith("-"):
                    exempt.add(start + 2)
            elif command == "echo":
                for offset, arg in enumerate(args, start + 1):
                    if re.fullmatch(r"[A-Za-z ]+ /[A-Za-z][A-Za-z0-9_-]*", arg):
                        exempt.add(start + offset)
        # Only shell command boundaries establish another command position.
        # A redirect target (even one named echo/grep) cannot start an exemption.
        start = (
            end + 1
            if end == len(tokens) or not any(c in tokens[end][0] for c in "<>")
            else len(tokens)
        )
    return exempt


def _contains_disk_format_command(command: str) -> bool:
    """Detect literal disk-format commands at executable positions only.

    A best-effort defensive check: shell execution is explicitly opt-in.
    """
    try:
        tokens = _shell_command_tokens(command)
    except ValueError:
        # Heredoc bodies may contain quotes that are literal data to the shell.
        # Keep the former broad protection for syntax this lexer cannot parse
        # without rejecting every such command when workspace checks are off.
        return re.search(r"\bformat\b", command, re.IGNORECASE) is not None
    at_command = True
    index = 0
    while index < len(tokens):
        token, literal = tokens[index]
        if literal and token and all(char in "();&|\n" for char in token):
            at_command = True
            index += 1
            continue
        if not at_command:
            index += 1
            continue
        lower = token.casefold()
        if literal and token in {
            "if",
            "then",
            "elif",
            "else",
            "while",
            "until",
            "do",
            "!",
            "{",
            "}",
        }:
            index += 1
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
            index += 1
            continue
        if lower in {"sudo", "command", "builtin", "exec", "nohup"}:
            index += 1
            continue
        if lower == "env":
            index += 1
            while index < len(tokens) and (
                tokens[index][0].startswith("-")
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[index][0])
            ):
                index += 1
            continue
        if (
            lower in {"bash", "sh", "dash", "zsh"}
            and index + 2 < len(tokens)
            and tokens[index + 1][0] in {"-c", "-lc", "-ec", "-xc"}
            and _contains_disk_format_command(tokens[index + 2][0])
        ):
            return True
        if (
            lower in {"cmd", "cmd.exe"}
            and index + 2 < len(tokens)
            and tokens[index + 1][0].casefold() in {"/c", "/k"}
            and _contains_disk_format_command(
                " ".join(value for value, _ in tokens[index + 2 :])
            )
        ):
            return True
        if lower in {"format", "format.exe"}:
            return True
        at_command = False
        index += 1
    return False


def _valid_allow_pattern(value: str) -> str:
    """Reject malformed Bash allowlist regular expressions at configuration time."""
    if not isinstance(value, str):
        raise vol.Invalid("allow pattern must be a string")
    try:
        re.compile(value)
    except re.error as err:
        raise vol.Invalid(f"invalid allow pattern: {err}") from err
    return value


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

    # POSIX descendants can outlive the shell leader, so ownership of the
    # process group must not be inferred from the leader's return code.
    process_group_id = process.pid if os.name == "posix" else None

    def stop_process(*, terminate: bool) -> None:
        if process_group_id is not None:
            sig = signal.SIGTERM if terminate else signal.SIGKILL
            with suppress(ProcessLookupError):
                os.killpg(process_group_id, sig)
            return
        if process.returncode is not None:
            return
        if terminate:
            with suppress(ProcessLookupError):
                process.terminate()
        else:
            with suppress(ProcessLookupError):
                process.kill()

    if graceful:
        stop_process(terminate=True)
        if process_group_id is not None:
            # There is no asyncio Process handle for surviving group descendants.
            # Give SIGTERM a bounded grace period before enforcing group teardown.
            await asyncio.sleep(_SHELL_CANCEL_GRACE_SECONDS)
        elif process.returncode is None:
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    process.wait(), timeout=_SHELL_CANCEL_GRACE_SECONDS
                )

    if process_group_id is not None or process.returncode is None:
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


async def _async_settle_process_cleanup(
    process: asyncio.subprocess.Process,
    stdout_task: asyncio.Task[tuple[bytes, bool]],
    stderr_task: asyncio.Task[tuple[bytes, bool]],
    *,
    graceful: bool,
) -> None:
    """Keep teardown owned and its private errors out of runtime shield logs."""
    task = asyncio.create_task(
        _async_cleanup_process(process, stdout_task, stderr_task, graceful=graceful)
    )
    cancellation = await async_wait_owned_cleanup(task)
    task.result()
    if cancellation is not None:
        raise cancellation


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
                vol.Optional("allow_patterns"): vol.All(
                    cv.ensure_list, [_valid_allow_pattern]
                ),
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
        if _contains_disk_format_command(command):
            raise ValueError("Command blocked by defensive policy: disk format")
        for pattern in SHELL_DENY_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                raise ValueError(
                    f"Command blocked by defensive policy: matches pattern '{pattern}'"
                )
        # Catch common reordered recursive-rm flags as defence-in-depth. This is
        # deliberately not presented as comprehensive shell parsing.
        if re.search(r"\brm\s+-[A-Za-z]*r[A-Za-z]*", command, re.IGNORECASE):
            raise ValueError("Command blocked by defensive policy: recursive rm")

        # Allow patterns are intentionally case-insensitive. They were historically
        # matched against a lower-cased command; IGNORECASE preserves that behavior
        # while making mixed-case configured patterns work consistently too.
        if allow_patterns and not any(
            re.search(pattern, command, re.IGNORECASE) for pattern in allow_patterns
        ):
            raise ValueError("Command blocked: not in allowlist")

        # Path restriction check when restrict_to_workspace is enabled. The
        # configured cwd is the workspace root. This is a defensive path guard,
        # not an OS sandbox: arbitrary executable code can construct paths
        # dynamically, so callers must still treat Bash as trusted.
        if restrict_to_workspace:
            uncommented = _without_shell_comments(command)
            # Block path traversal patterns.
            if "../" in uncommented or "..\\" in uncommented:
                raise ValueError("Command blocked: path traversal detected")

            # Inspect executable positions, preserving the lexer's distinction
            # between real operators and harmless quoted command-like text.
            tokens = _shell_command_tokens(command, skip_heredoc_bodies=True)
            at_command = True
            wrapper_options = False
            for index, (word, literal) in enumerate(tokens):
                if literal and word and all(char in "();&|\n" for char in word):
                    at_command = True
                    wrapper_options = False
                    continue
                if not at_command:
                    continue
                if word in {"command", "builtin"}:
                    wrapper_options = True
                    continue
                if wrapper_options and word in {"--", "-p"}:
                    continue
                if literal and word in {
                    "if",
                    "then",
                    "elif",
                    "else",
                    "while",
                    "until",
                    "do",
                    "!",
                    "{",
                    "}",
                }:
                    continue
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", word):
                    continue
                at_command = False
                if word not in {"cd", "pushd", "popd"}:
                    continue
                if word == "popd":
                    raise ValueError(
                        "Command blocked: directory stack target cannot be verified"
                    )
                target_index = index + 1
                flags = {"-L", "-P", "-e"} if word == "cd" else {"-n"}
                while target_index < len(tokens) and tokens[target_index][0] in flags:
                    target_index += 1
                if target_index < len(tokens) and tokens[target_index][0] == "--":
                    target_index += 1
                raw_target = (
                    tokens[target_index][0] if target_index < len(tokens) else ""
                )
                if any(char in raw_target for char in "$`"):
                    raise ValueError(
                        f"Command blocked: dynamic {word} target cannot be verified"
                    )
                if (
                    not raw_target
                    or raw_target.startswith(("-", "+", "~"))
                    or any(char in raw_target for char in "*?[]{}")
                    or (
                        tokens[target_index][1]
                        and any(char in "();&|<>\n" for char in raw_target)
                    )
                ):
                    raise ValueError(
                        "Command blocked: directory change target cannot be verified"
                    )
                if raw_target == ".":
                    continue
                cd_target = Path(raw_target)
                if not cd_target.is_absolute():
                    cd_target = cwd_path / cd_target
                cd_target = cd_target.resolve()
                if not self._is_within(cd_target, cwd_path):
                    raise ValueError(
                        "Command blocked: cd target is outside working directory"
                    )

            # Extract and validate literal absolute paths in command text.
            win_paths = re.findall(r"[A-Za-z]:\\[^\\\"\' ]+", uncommented)
            # Tokenize shell operators before recognizing URLs: a URL argument
            # must not hide a following command or redirection. This remains a
            # defensive literal-path guard, not a sandbox for arbitrary Bash.
            path_tokens = _shell_command_tokens(command)
            text_operands = _literal_text_operands(path_tokens)
            posix_paths = []
            for index, (token, _) in enumerate(path_tokens):
                if index in text_operands:
                    continue
                if re.match(r"^(?:[^=]+=)?[A-Za-z][A-Za-z0-9+.-]*://", token):
                    continue
                posix_paths.extend(re.findall(r"(?<![\w./:])/[^\s\"\']+", token))

            safe_streams = {"/dev/null", "/dev/stdout", "/dev/stderr"}
            for raw in win_paths + posix_paths:
                if os.name == "posix" and raw in safe_streams:
                    continue
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
        """Run configured allowlist regex in a bounded child process."""
        if allow_patterns:
            matches = await async_search_configured_patterns(
                hass,
                [(pattern, command, re.IGNORECASE) for pattern in allow_patterns],
            )
            if not any(matches):
                raise ValueError("Command blocked: not in allowlist")

        # Built-in defensive patterns are fixed by the integration and remain on the
        # ordinary executor fast path. Do not re-run administrator patterns here.
        await hass.async_add_executor_job(
            self._guard_command,
            command,
            cwd,
            restrict_to_workspace,
            None,
        )

    @owns_execution_receipts
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
            return backend_failure(
                "Bash execution is disabled. Set allow_unsafe_shell: true in "
                "the Function Tool configuration to explicitly allow arbitrary "
                "shell commands with Home Assistant's OS privileges."
            )

        command_template = function_config.get("command")
        command = command_template.async_render(arguments, parse_result=False)

        default_workspace = self.get_working_dir(hass).resolve()
        from .workspace import async_ensure_workspace

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
        await async_ensure_workspace(hass, default_workspace, target=cwd)

        restrict_to_workspace = function_config.get("restrict_to_workspace", True)

        raw_timeout = arguments.get("timeout", SHELL_TIMEOUT)
        try:
            timeout = float(raw_timeout)
        except TypeError, ValueError, OverflowError:
            return backend_failure("Timeout must be a number")
        # A NaN timer reaches asyncio's event-loop heap and may crash HA's
        # shared loop, rather than failing within this Function Tool coroutine.
        if not math.isfinite(timeout):
            return backend_failure("Timeout must be a finite number")
        if timeout <= 0:
            return backend_failure("Timeout must be greater than zero")
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
            return backend_failure(str(err), err)

        environment = dict(os.environ)
        receipt = execution_started(
            kind="bash",
            prepared={
                "command": command,
                "cwd": str(cwd),
                "environment": environment,
                "timeout": timeout,
                "platform": os.name,
            },
        )
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name == "posix",
            )
            stdout_task = asyncio.create_task(_read_bounded_stream(process.stdout))
            stderr_task = asyncio.create_task(_read_bounded_stream(process.stderr))

            try:
                # An exited shell can leave descendants holding its pipes open.
                # The command deadline includes draining those owned pipes.
                async with asyncio.timeout(timeout):
                    await process.wait()
                    (
                        (stdout, stdout_truncated),
                        (stderr, stderr_truncated),
                    ) = await asyncio.gather(stdout_task, stderr_task)
            except TimeoutError as err:
                await _async_settle_process_cleanup(
                    process,
                    stdout_task,
                    stderr_task,
                    graceful=False,
                )
                # Settle through the outer failure handler after process cleanup.
                # Its backend_failure call can then propagate outside this try.
                raise TimeoutError(
                    f"Command timed out after {timeout:g} seconds"
                ) from err
            except asyncio.CancelledError:
                # A second cancellation cannot abandon process/pipe ownership.
                # Preserve the original cancellation after successful teardown;
                # an underlying cleanup failure keeps its established priority.
                with suppress(asyncio.CancelledError):
                    await _async_settle_process_cleanup(
                        process,
                        stdout_task,
                        stderr_task,
                        graceful=True,
                    )
                raise

            execution_completed(receipt)
            stdout_text = _decode_bounded_output(stdout, stdout_truncated)
            stderr_text = _decode_bounded_output(stderr, stderr_truncated)

            result = {
                "exit_code": process.returncode,
                "stdout": stdout_text,
            }

            if stderr_text:
                result["stderr"] = stderr_text

        except Exception as err:
            log_handled_failure(_LOGGER, "Function Tool shell execution failed", err)
            return backend_failure(str(err), err)

        return result
