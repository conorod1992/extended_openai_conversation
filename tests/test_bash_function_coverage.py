"""Focused coverage for Bash Function Tool safety and subprocess lifecycle behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses.functions import bash as bash_module


class _Template:
    """Minimal Home Assistant template stand-in for Bash configuration tests."""

    def __init__(self, rendered: str) -> None:
        self.rendered = rendered

    def async_render(self, variables, *, parse_result: bool = False):
        assert parse_result is False
        return self.rendered


def _hass(tmp_path: Path) -> SimpleNamespace:
    """Return the Home Assistant surface used by BashFunction."""
    return SimpleNamespace(
        config=SimpleNamespace(config_dir=str(tmp_path)),
        async_add_executor_job=AsyncMock(),
    )


def _reader(*chunks: bytes) -> AsyncMock:
    """Return a stream reader yielding the supplied chunks and then EOF."""
    reader = AsyncMock()
    reader.read = AsyncMock(side_effect=[*chunks, b""])
    return reader


def _process(
    *,
    stdout: AsyncMock | None = None,
    stderr: AsyncMock | None = None,
    returncode: int | None = 0,
    pid: int = 4321,
) -> SimpleNamespace:
    """Return the asyncio subprocess surface used by BashFunction."""
    return SimpleNamespace(
        stdout=stdout or _reader(),
        stderr=stderr or _reader(),
        returncode=returncode,
        pid=pid,
        wait=AsyncMock(return_value=returncode),
        terminate=Mock(),
        kill=Mock(),
    )


def test_allow_pattern_validation_accepts_valid_and_rejects_bad_values() -> None:
    """Configured allowlist patterns are validated before execution."""
    assert bash_module._valid_allow_pattern(r"^echo\s+") == r"^echo\s+"

    with pytest.raises(vol.Invalid, match="must be a string"):
        bash_module._valid_allow_pattern(123)  # type: ignore[arg-type]

    with pytest.raises(vol.Invalid, match="invalid allow pattern"):
        bash_module._valid_allow_pattern("[")


@pytest.mark.asyncio
async def test_bounded_stream_handles_none_and_truncates_while_draining() -> None:
    """Pipe draining keeps only the configured prefix but consumes all output."""
    assert await bash_module._read_bounded_stream(None, 3) == (b"", False)

    stream = _reader(b"abcd", b"ef")
    assert await bash_module._read_bounded_stream(stream, 5) == (b"abcde", True)
    assert stream.read.await_count == 3

    empty_limit = _reader(b"abc")
    assert await bash_module._read_bounded_stream(empty_limit, 0) == (b"", True)


def test_decode_bounded_output_marks_byte_and_character_truncation() -> None:
    """Output reports truncation whether imposed by bytes or character count."""
    marker = "\n... (truncated, output too large)"
    assert bash_module._decode_bounded_output(b"ok", False) == "ok"
    assert bash_module._decode_bounded_output(b"ok", True) == f"ok{marker}"

    oversized = b"x" * (bash_module.SHELL_OUTPUT_LIMIT + 1)
    decoded = bash_module._decode_bounded_output(oversized, False)
    assert decoded == ("x" * bash_module.SHELL_OUTPUT_LIMIT) + marker

    # Invalid UTF-8 is deliberately replaced rather than making command results fail.
    assert "\ufffd" in bash_module._decode_bounded_output(b"\xff", False)


@pytest.mark.asyncio
async def test_cleanup_non_posix_graceful_terminates_and_waits() -> None:
    """Graceful non-POSIX cleanup terminates the process and settles readers."""
    process = _process(returncode=None)

    async def _wait_and_finish():
        process.returncode = 0
        return 0

    process.wait = AsyncMock(side_effect=_wait_and_finish)
    stdout_task = asyncio.create_task(asyncio.sleep(0, result=(b"", False)))
    stderr_task = asyncio.create_task(asyncio.sleep(0, result=(b"", False)))

    with patch.object(bash_module.os, "name", "nt"):
        await bash_module._async_cleanup_process(
            process, stdout_task, stderr_task, graceful=True
        )

    process.terminate.assert_called_once_with()
    process.kill.assert_not_called()
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_non_posix_force_kills_running_process() -> None:
    """Forced non-POSIX cleanup kills and reaps a still-running process."""
    process = _process(returncode=None)
    stdout_task = asyncio.create_task(asyncio.sleep(0, result=(b"", False)))
    stderr_task = asyncio.create_task(asyncio.sleep(0, result=(b"", False)))

    with patch.object(bash_module.os, "name", "nt"):
        await bash_module._async_cleanup_process(
            process, stdout_task, stderr_task, graceful=False
        )

    process.kill.assert_called_once_with()
    process.terminate.assert_not_called()
    process.wait.assert_awaited_once()


def test_guard_command_covers_allowlist_and_recursive_rm(tmp_path: Path) -> None:
    """Local defensive checks reject dangerous commands and allowlist misses."""
    function = bash_module.BashFunction()

    with pytest.raises(ValueError, match="recursive rm"):
        function._guard_command("rm -fr cache", tmp_path, False)

    with pytest.raises(ValueError, match="not in allowlist"):
        function._guard_command(
            "echo hello", tmp_path, False, allow_patterns=[r"^printf\b"]
        )

    # Allow patterns are intentionally case-insensitive.
    function._guard_command(
        "ECHO hello", tmp_path, False, allow_patterns=[r"^echo\b"]
    )


def test_guard_command_rejects_workspace_escape_forms(tmp_path: Path) -> None:
    """Literal, traversal, cd, and dynamic workspace escapes fail closed."""
    function = bash_module.BashFunction()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ValueError, match="path traversal"):
        function._guard_command("cat ../secret", workspace, True)

    with pytest.raises(ValueError, match="dynamic cd target"):
        function._guard_command("cd $TARGET", workspace, True)

    with pytest.raises(ValueError, match="cd target is outside"):
        function._guard_command("cd /tmp", workspace, True)

    with pytest.raises(ValueError, match="outside working dir"):
        function._guard_command("cat /etc/passwd", workspace, True)

    # Benign current-directory and in-workspace changes remain valid.
    (workspace / "inside").mkdir()
    function._guard_command("cd .; cd inside; pwd", workspace, True)


def test_guard_command_ignores_unresolvable_literal_path(tmp_path: Path) -> None:
    """A literal whose Path resolution itself fails is left to the shell."""
    function = bash_module.BashFunction()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    original_resolve = Path.resolve

    def _resolve(path: Path, *args, **kwargs):
        if str(path) == "/synthetic":
            raise OSError("cannot resolve")
        return original_resolve(path, *args, **kwargs)

    with patch.object(Path, "resolve", _resolve):
        function._guard_command("cat /synthetic", workspace, True)


@pytest.mark.asyncio
async def test_async_guard_uses_bounded_pattern_search_and_executor(tmp_path: Path) -> None:
    """Configured regexes use the bounded matcher before built-in checks."""
    function = bash_module.BashFunction()
    hass = _hass(tmp_path)

    with patch.object(
        bash_module,
        "async_search_configured_patterns",
        new=AsyncMock(return_value=[False, True]),
    ) as search:
        await function._async_guard_command(
            hass,
            "echo hi",
            tmp_path,
            False,
            [r"^no$", r"^echo"],
        )

    search.assert_awaited_once()
    hass.async_add_executor_job.assert_awaited_once_with(
        function._guard_command, "echo hi", tmp_path, False, None
    )

    hass.async_add_executor_job.reset_mock()
    with (
        patch.object(
            bash_module,
            "async_search_configured_patterns",
            new=AsyncMock(return_value=[False]),
        ),
        pytest.raises(ValueError, match="not in allowlist"),
    ):
        await function._async_guard_command(
            hass, "echo hi", tmp_path, False, [r"^printf"]
        )
    hass.async_add_executor_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_requires_opt_in_and_valid_timeout(tmp_path: Path) -> None:
    """Bash remains explicitly opt-in and rejects invalid timeout input early."""
    function = bash_module.BashFunction()
    hass = _hass(tmp_path)
    base = {"command": _Template("echo hello")}

    disabled = await function.execute(hass, base, {}, None, None)
    assert "disabled" in disabled["error"]

    invalid = await function.execute(
        hass, {**base, "allow_unsafe_shell": True}, {"timeout": "later"}, None, None
    )
    assert invalid == {"error": "Timeout must be a number"}

    nonpositive = await function.execute(
        hass, {**base, "allow_unsafe_shell": True}, {"timeout": 0}, None, None
    )
    assert nonpositive == {"error": "Timeout must be greater than zero"}


@pytest.mark.asyncio
async def test_execute_returns_guard_error_without_spawning(tmp_path: Path) -> None:
    """A rejected command never reaches subprocess creation."""
    function = bash_module.BashFunction()
    hass = _hass(tmp_path)
    config = {
        "command": _Template("echo hi"),
        "allow_unsafe_shell": True,
    }

    with (
        patch.object(
            function,
            "_async_guard_command",
            new=AsyncMock(side_effect=ValueError("blocked safely")),
        ),
        patch.object(
            bash_module.asyncio, "create_subprocess_shell", new=AsyncMock()
        ) as create_process,
    ):
        result = await function.execute(hass, config, {}, None, None)

    assert result == {"error": "blocked safely"}
    create_process.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_success_uses_relative_cwd_caps_timeout_and_reports_stderr(
    tmp_path: Path,
) -> None:
    """Successful execution renders cwd, caps timeout, and returns bounded pipes."""
    function = bash_module.BashFunction()
    hass = _hass(tmp_path)
    process = _process(stdout=_reader(b"hello"), stderr=_reader(b"warning"), returncode=7)
    config = {
        "command": _Template("echo hello"),
        "cwd": _Template("nested"),
        "allow_unsafe_shell": True,
        "restrict_to_workspace": False,
    }
    expected_cwd = (function.get_working_dir(hass).resolve() / "nested").resolve()

    with (
        patch.object(function, "_async_guard_command", new=AsyncMock()) as guard,
        patch.object(
            bash_module.asyncio,
            "create_subprocess_shell",
            new=AsyncMock(return_value=process),
        ) as create_process,
    ):
        result = await function.execute(
            hass,
            config,
            {"timeout": bash_module.SHELL_TIMEOUT * 10},
            None,
            None,
        )

    assert result == {"exit_code": 7, "stdout": "hello", "stderr": "warning"}
    guard.assert_awaited_once_with(
        hass,
        "echo hello",
        cwd=expected_cwd,
        restrict_to_workspace=False,
        allow_patterns=[],
    )
    create_process.assert_awaited_once_with(
        "echo hello",
        cwd=str(expected_cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=bash_module.os.name == "posix",
    )


@pytest.mark.asyncio
async def test_execute_success_omits_empty_stderr_and_marks_truncated_output(
    tmp_path: Path,
) -> None:
    """Successful output keeps the public result shape and truncation marker."""
    function = bash_module.BashFunction()
    hass = _hass(tmp_path)
    process = _process(returncode=0)
    config = {"command": _Template("echo hello"), "allow_unsafe_shell": True}

    async def _bounded(stream):
        if stream is process.stdout:
            return b"hello", True
        return b"", False

    with (
        patch.object(function, "_async_guard_command", new=AsyncMock()),
        patch.object(
            bash_module.asyncio,
            "create_subprocess_shell",
            new=AsyncMock(return_value=process),
        ),
        patch.object(bash_module, "_read_bounded_stream", side_effect=_bounded),
    ):
        result = await function.execute(hass, config, {}, None, None)

    assert result["exit_code"] == 0
    assert result["stdout"].endswith("... (truncated, output too large)")
    assert "stderr" not in result


@pytest.mark.asyncio
async def test_execute_timeout_cleans_up_process_group(tmp_path: Path) -> None:
    """Timeout returns a stable error only after force-cleaning the subprocess."""
    function = bash_module.BashFunction()
    hass = _hass(tmp_path)
    process = _process(returncode=None)
    process.wait = AsyncMock(side_effect=[TimeoutError(), 0])
    config = {"command": _Template("sleep forever"), "allow_unsafe_shell": True}

    with (
        patch.object(function, "_async_guard_command", new=AsyncMock()),
        patch.object(
            bash_module.asyncio,
            "create_subprocess_shell",
            new=AsyncMock(return_value=process),
        ),
        patch.object(bash_module.os, "killpg") as killpg,
    ):
        result = await function.execute(hass, config, {"timeout": 2}, None, None)

    assert result == {"error": "Command timed out after 2 seconds"}
    if bash_module.os.name == "posix":
        killpg.assert_called_with(process.pid, bash_module.signal.SIGKILL)


@pytest.mark.asyncio
async def test_execute_cancellation_performs_graceful_cleanup_then_reraises(
    tmp_path: Path,
) -> None:
    """Task cancellation gracefully tears down the child and remains cancellation."""
    function = bash_module.BashFunction()
    hass = _hass(tmp_path)
    process = _process(returncode=None)
    process.wait = AsyncMock(side_effect=[asyncio.CancelledError(), 0])
    config = {"command": _Template("sleep forever"), "allow_unsafe_shell": True}

    with (
        patch.object(function, "_async_guard_command", new=AsyncMock()),
        patch.object(
            bash_module.asyncio,
            "create_subprocess_shell",
            new=AsyncMock(return_value=process),
        ),
        patch.object(bash_module, "_SHELL_CANCEL_GRACE_SECONDS", 0),
        patch.object(bash_module.os, "killpg") as killpg,
        pytest.raises(asyncio.CancelledError),
    ):
        await function.execute(hass, config, {}, None, None)

    if bash_module.os.name == "posix":
        assert killpg.call_count >= 2


@pytest.mark.asyncio
async def test_execute_subprocess_creation_failure_is_returned(tmp_path: Path) -> None:
    """OS-level spawn failures are converted to the Function Tool error contract."""
    function = bash_module.BashFunction()
    hass = _hass(tmp_path)
    config = {"command": _Template("echo hello"), "allow_unsafe_shell": True}

    with (
        patch.object(function, "_async_guard_command", new=AsyncMock()),
        patch.object(
            bash_module.asyncio,
            "create_subprocess_shell",
            new=AsyncMock(side_effect=OSError("spawn failed")),
        ),
    ):
        result = await function.execute(hass, config, {}, None, None)

    assert result == {"error": "spawn failed"}
