"""Focused branch coverage for bounded configured-regex execution."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import regex_execution
from custom_components.extended_openai_conversation_responses.const import (
    CONF_SPEECH_REGEX_REPLACEMENTS,
)
from homeassistant.exceptions import HomeAssistantError


def _rules() -> list[dict[str, str]]:
    return [{"pattern": "old", "replacement": "new"}]


def test_decode_worker_result_rejects_process_and_payload_failures():
    """The parent must distrust both failed workers and malformed stdout."""
    with pytest.raises(HomeAssistantError, match="worker exploded"):
        regex_execution._decode_regex_worker_result("", "worker exploded\n", 2)

    with pytest.raises(HomeAssistantError, match="regex worker failed"):
        regex_execution._decode_regex_worker_result("", "", 1)

    with pytest.raises(HomeAssistantError, match="returned invalid data"):
        regex_execution._decode_regex_worker_result("not json", "", 0)

    with pytest.raises(HomeAssistantError, match="returned invalid data"):
        regex_execution._decode_regex_worker_result("[]", "", 0)


def test_sync_worker_timeout_is_normalized(monkeypatch):
    """A wedged stdlib regex worker becomes a bounded HA error."""

    def timeout(*args, **kwargs):
        raise regex_execution.subprocess.TimeoutExpired(cmd="python", timeout=1)

    monkeypatch.setattr(regex_execution.subprocess, "run", timeout)

    with pytest.raises(HomeAssistantError, match="1 second execution limit"):
        regex_execution._run_regex_worker({"op": "search_many", "items": []})


async def test_async_stop_worker_suppresses_racing_process_exit():
    """Cleanup still reaps a process that exits between inspection and kill."""
    process = SimpleNamespace(
        returncode=None,
        kill=MagicMock(side_effect=ProcessLookupError),
        wait=AsyncMock(),
    )

    await regex_execution._async_stop_regex_worker(process)

    process.kill.assert_called_once_with()
    process.wait.assert_awaited_once_with()


async def test_async_worker_timeout_kills_and_reaps(monkeypatch):
    """Async regex timeouts terminate the child before surfacing the error."""
    process = SimpleNamespace(
        communicate=AsyncMock(side_effect=TimeoutError),
        returncode=None,
    )
    stop = AsyncMock()
    monkeypatch.setattr(
        regex_execution.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=process),
    )
    monkeypatch.setattr(regex_execution, "_async_stop_regex_worker", stop)

    with pytest.raises(HomeAssistantError, match="1 second execution limit"):
        await regex_execution._async_run_regex_worker({"op": "sub_many"})

    stop.assert_awaited_once_with(process)


async def test_async_worker_cancellation_kills_and_propagates(monkeypatch):
    """Caller cancellation must clean up the worker without being converted."""
    process = SimpleNamespace(
        communicate=AsyncMock(side_effect=asyncio.CancelledError),
        returncode=None,
    )
    stop = AsyncMock()
    monkeypatch.setattr(
        regex_execution.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=process),
    )
    monkeypatch.setattr(regex_execution, "_async_stop_regex_worker", stop)

    with pytest.raises(asyncio.CancelledError):
        await regex_execution._async_run_regex_worker({"op": "sub_many"})

    stop.assert_awaited_once_with(process)


async def test_configured_search_empty_input_avoids_executor():
    """No configured checks should not start a child process."""
    hass = SimpleNamespace(async_add_executor_job=AsyncMock())

    assert await regex_execution.async_search_configured_patterns(hass, []) == []
    hass.async_add_executor_job.assert_not_awaited()


@pytest.mark.parametrize(
    ("worker_result", "message"),
    [
        ({"invalid": [[0, "bad group"]], "results": [False]}, "bad group"),
        ({"invalid": [0], "results": [False]}, "invalid pattern"),
        ({"invalid": [], "results": "nope"}, "returned invalid data"),
        ({"invalid": [], "results": [1]}, "returned invalid data"),
        ({"invalid": [], "results": [True]}, "returned incomplete data"),
    ],
)
async def test_configured_search_rejects_untrusted_worker_results(
    worker_result, message
):
    """Search results are structurally validated before callers can consume them."""
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=worker_result))
    checks = [("a", "a", 0), ("b", "b", 0)]
    if "incomplete" not in message:
        checks = checks[:1]

    with pytest.raises(HomeAssistantError, match=message):
        await regex_execution.async_search_configured_patterns(hass, checks)


@pytest.mark.parametrize(
    "rules",
    [
        "not-a-list",
        [object()],
        [{"pattern": "", "replacement": "x"}],
        [{"pattern": "x", "replacement": 1}],
    ],
)
def test_speech_replacement_items_rejects_malformed_rules(rules):
    """Malformed persisted rule shapes fail closed before reaching a worker."""
    assert regex_execution._speech_replacement_items(rules) is None


def test_speech_replacement_items_enforces_all_size_limits():
    """Rule count, pattern size, and replacement size are bounded independently."""
    valid = {"pattern": "x", "replacement": "y"}
    assert (
        regex_execution._speech_replacement_items(
            [valid] * (regex_execution.MAX_SPEECH_REGEX_RULES + 1)
        )
        is None
    )
    assert (
        regex_execution._speech_replacement_items(
            [
                {
                    "pattern": "x"
                    * (regex_execution.MAX_SPEECH_REGEX_PATTERN_LENGTH + 1),
                    "replacement": "y",
                }
            ]
        )
        is None
    )
    assert (
        regex_execution._speech_replacement_items(
            [
                {
                    "pattern": "x",
                    "replacement": "y"
                    * (regex_execution.MAX_SPEECH_REGEX_REPLACEMENT_LENGTH + 1),
                }
            ]
        )
        is None
    )


async def test_speech_replacements_short_circuit_invalid_and_large_inputs(caplog):
    """Invalid configuration and oversized speech preserve the original text."""
    assert (
        await regex_execution._async_apply_speech_replacements("hello", []) == "hello"
    )
    assert (
        await regex_execution._async_apply_speech_replacements("hello", "bad rules")
        == "hello"
    )
    oversized = "x" * (regex_execution.MAX_SPEECH_REPLACEMENT_INPUT_CHARS + 1)
    assert (
        await regex_execution._async_apply_speech_replacements(oversized, _rules())
        == oversized
    )
    assert "preserving spoken text" in caplog.text
    assert "input exceeds" in caplog.text


@pytest.mark.parametrize(
    "error",
    [HomeAssistantError("bad regex"), RuntimeError("worker broke")],
)
async def test_speech_replacements_fail_open_on_worker_errors(monkeypatch, error):
    """Ordinary worker failures never destroy the response chosen for speech."""
    monkeypatch.setattr(
        regex_execution,
        "_async_run_regex_worker",
        AsyncMock(side_effect=error),
    )

    assert (
        await regex_execution._async_apply_speech_replacements("old", _rules()) == "old"
    )


@pytest.mark.parametrize(
    "worker_result",
    [
        {"invalid": "bad", "text": "new", "overflow": False},
        {"invalid": [[]], "text": "new", "overflow": False},
        {"invalid": [[3, "bad group"]], "text": "new", "overflow": False},
        {"invalid": [], "text": "new", "overflow": True},
        {"invalid": [], "text": 42, "overflow": False},
    ],
)
async def test_speech_replacements_rejects_malformed_worker_success(
    monkeypatch, worker_result
):
    """A zero-exit worker still cannot inject malformed or partial output."""
    monkeypatch.setattr(
        regex_execution,
        "_async_run_regex_worker",
        AsyncMock(return_value=worker_result),
    )

    assert (
        await regex_execution._async_apply_speech_replacements("old", _rules()) == "old"
    )


async def test_speech_replacements_rejects_output_beyond_parent_limit(monkeypatch):
    """The parent independently enforces the output growth bound."""
    monkeypatch.setattr(regex_execution, "_speech_output_limit", lambda chars: 3)
    monkeypatch.setattr(
        regex_execution,
        "_async_run_regex_worker",
        AsyncMock(return_value={"invalid": [], "text": "four", "overflow": False}),
    )

    assert (
        await regex_execution._async_apply_speech_replacements("old", _rules()) == "old"
    )


@pytest.mark.parametrize("custom", [False, True])
async def test_management_speech_preview_calls_isolated_regex_engine(
    hass, management_message, monkeypatch, custom
):
    from custom_components.extended_openai_conversation_responses import management_ui

    process = AsyncMock(return_value="processed")
    monkeypatch.setattr(management_ui, "async_process_speech_text", process)
    result = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "configuration",
            "speech_preview",
            sample_text="Some text",
            config={CONF_SPEECH_REGEX_REPLACEMENTS: _rules() if custom else []},
        ),
    )
    assert result["speech_text"] == "processed"
    process.assert_awaited_once()
