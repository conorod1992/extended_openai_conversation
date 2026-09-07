"""Safety and parity tests for administrator-configured speech replacements."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import regex_execution
from custom_components.extended_openai_conversation_responses.regex_execution import (
    MAX_SPEECH_REPLACEMENT_INPUT_CHARS,
    _async_apply_speech_replacements,
    _async_run_regex_worker,
    async_process_speech_text,
    install_configurable_regex_isolation,
)


class _ExecutorHass:
    async def async_add_executor_job(self, target, *args):
        return await asyncio.get_running_loop().run_in_executor(None, target, *args)


def _config(rules, **updates):
    return {
        "speech_processing_enabled": True,
        "speech_strip_markdown": False,
        "speech_strip_urls": False,
        "speech_regex_replacements": rules,
        **updates,
    }


async def test_replacements_are_ordered_in_one_bounded_operation() -> None:
    text = await _async_apply_speech_replacements(
        "HA is ready",
        [
            {"pattern": r"\bHA\b", "replacement": "Home Assistant"},
            {"pattern": "Home Assistant", "replacement": "the smart home"},
        ],
    )
    assert text == "the smart home is ready"


async def test_invalid_later_rule_fails_open_without_partial_replacement() -> None:
    original = "HA is ready"
    result = await _async_apply_speech_replacements(
        original,
        [
            {"pattern": "HA", "replacement": "Home Assistant"},
            {"pattern": "(", "replacement": "broken"},
        ],
    )
    assert result == original


async def test_invalid_replacement_expression_fails_open() -> None:
    original = "HA is ready"
    assert await _async_apply_speech_replacements(
        original,
        [{"pattern": "HA", "replacement": r"\9"}],
    ) == original


async def test_malformed_stored_rules_fail_open() -> None:
    original = "Keep this unchanged"
    assert await _async_apply_speech_replacements(original, [{"pattern": "x"}]) == original
    assert await _async_apply_speech_replacements(original, "not-a-list") == original


async def test_output_growth_limit_fails_open() -> None:
    original = "x" * 10
    assert await _async_apply_speech_replacements(
        original,
        [{"pattern": "x", "replacement": "y" * 500}],
    ) == original


async def test_oversized_input_skips_custom_replacements() -> None:
    original = "x" * (MAX_SPEECH_REPLACEMENT_INPUT_CHARS + 1)
    assert await _async_apply_speech_replacements(
        original,
        [{"pattern": "x", "replacement": "y"}],
    ) == original


async def test_worker_failure_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(_payload):
        raise HomeAssistantError("worker unavailable")

    monkeypatch.setattr(regex_execution, "_async_run_regex_worker", fail)
    original = "HA is ready"
    assert await _async_apply_speech_replacements(
        original,
        [{"pattern": "HA", "replacement": "Home Assistant"}],
    ) == original


async def test_unexpected_worker_failure_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(_payload):
        raise RuntimeError("unexpected worker problem")

    monkeypatch.setattr(regex_execution, "_async_run_regex_worker", fail)
    original = "HA is ready"
    assert await _async_apply_speech_replacements(
        original,
        [{"pattern": "HA", "replacement": "Home Assistant"}],
    ) == original


async def test_pathological_replacement_times_out_without_blocking_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(regex_execution, "_CONFIGURED_REGEX_TIMEOUT_SECONDS", 0.05)
    original = "a" * 30 + "!"
    task = asyncio.create_task(
        _async_apply_speech_replacements(
            original,
            [{"pattern": r"^(a+)+$", "replacement": "blocked"}],
        )
    )
    loop = asyncio.get_running_loop()
    started = loop.time()
    await asyncio.sleep(0.01)
    assert loop.time() - started < 0.2
    assert await asyncio.wait_for(task, timeout=1) == original


async def test_async_pipeline_preserves_builtin_cleanup_when_custom_stage_fails() -> None:
    hass = _ExecutorHass()
    result = await async_process_speech_text(
        hass,
        "**HA** is ready",
        _config(
            [{"pattern": "(", "replacement": "broken"}],
            speech_strip_markdown=True,
        ),
    )
    assert result == "HA is ready"


async def test_worker_is_killed_when_parent_task_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()

    class FakeProcess:
        returncode = None
        killed = False
        waited = False

        async def communicate(self, _payload):
            entered.set()
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            self.waited = True
            return self.returncode

    process = FakeProcess()

    async def create_process(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    task = asyncio.create_task(
        _async_run_regex_worker({"op": "sub_many", "text": "x", "items": []})
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed is True
    assert process.waited is True


def test_live_and_preview_async_isolation_are_installed() -> None:
    install_configurable_regex_isolation()

    from custom_components.extended_openai_conversation_responses import management_ui
    from custom_components.extended_openai_conversation_responses.conversation import (
        ExtendedOpenAIAgentEntity,
    )

    assert getattr(
        ExtendedOpenAIAgentEntity._async_handle_message,
        "_extended_openai_configurable_regex_executor",
        False,
    )
    assert getattr(
        management_ui.async_management_command,
        "_extended_openai_speech_preview_executor",
        False,
    )


def test_custom_regex_still_disables_progressive_tts() -> None:
    from custom_components.extended_openai_conversation_responses.conversation import (
        ExtendedOpenAIAgentEntity,
    )

    subentry = SimpleNamespace(
        subentry_id="agent-id",
        title="Agent",
        data=_config([{"pattern": "HA", "replacement": "Home Assistant"}]),
    )
    entity = ExtendedOpenAIAgentEntity(SimpleNamespace(), subentry)
    assert entity.supports_streaming is False
