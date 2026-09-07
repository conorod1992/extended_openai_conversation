"""Bound administrator-configured regex work outside the Home Assistant process."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextvars import ContextVar
from functools import partial
import json
import logging
import subprocess
import sys
from typing import Any, cast

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_SPEECH_REGEX_REPLACEMENTS,
    CONF_SPEECH_STRIP_MARKDOWN,
    CONF_SPEECH_STRIP_URLS,
    DEFAULT_SPEECH_REGEX_REPLACEMENTS,
    DEFAULT_SPEECH_STRIP_MARKDOWN,
    DEFAULT_SPEECH_STRIP_URLS,
    MAX_SPEECH_REGEX_PATTERN_LENGTH,
    MAX_SPEECH_REGEX_REPLACEMENT_LENGTH,
    MAX_SPEECH_REGEX_RULES,
)
from .speech import (
    _built_in_cleanup,
    _final_whitespace_cleanup,
    has_custom_speech_replacements,
)

_LOGGER = logging.getLogger(__name__)
_CONFIGURED_REGEX_TIMEOUT_SECONDS = 1.0
MAX_SPEECH_REPLACEMENT_INPUT_CHARS = 32_768
MAX_SPEECH_REPLACEMENT_OUTPUT_CHARS = 65_536
MAX_SPEECH_REPLACEMENT_EXPANSION = 4
MIN_SPEECH_REPLACEMENT_OUTPUT_ALLOWANCE = 4_096

# Keep the child interpreter deliberately tiny: it imports only stdlib modules and
# receives data over stdin. A catastrophic Python ``re`` match can therefore hold
# only the child's GIL, and the parent can terminate the child at the deadline.
_REGEX_WORKER = r"""
import json
import re
import sys

payload = json.load(sys.stdin)
op = payload.get("op")
if op == "search_many":
    results = []
    invalid = []
    for index, item in enumerate(payload.get("items", [])):
        try:
            results.append(
                re.search(item["pattern"], item["text"], int(item.get("flags", 0)))
                is not None
            )
        except re.error as err:
            results.append(False)
            invalid.append([index, str(err)])
    output = {"results": results, "invalid": invalid}
elif op == "sub_many":
    original = payload.get("text", "")
    text = original
    invalid = []
    overflow = False
    max_output_chars = int(payload.get("max_output_chars", 0))
    for item in payload.get("items", []):
        try:
            candidate = re.sub(item["pattern"], item["replacement"], text)
        except re.error as err:
            invalid.append([item["index"], str(err)])
            break
        if max_output_chars > 0 and len(candidate) > max_output_chars:
            overflow = True
            break
        text = candidate
    output = {
        "text": original if invalid or overflow else text,
        "invalid": invalid,
        "overflow": overflow,
    }
else:
    raise SystemExit("unsupported regex worker operation")
json.dump(output, sys.stdout)
"""

_DEFER_SPEECH_PROCESSING: ContextVar[bool] = ContextVar(
    "extended_openai_defer_speech_processing", default=False
)
_DEFERRED_SPEECH_INPUT: ContextVar[tuple[str, Mapping[str, Any]] | None] = ContextVar(
    "extended_openai_deferred_speech_input", default=None
)

_INSTALLED = False


def _decode_regex_worker_result(
    stdout: str, stderr: str, returncode: int
) -> dict[str, Any]:
    """Validate one child-process result without trusting worker output."""
    if returncode != 0:
        detail = stderr.strip() or "regex worker failed"
        raise HomeAssistantError(f"Configured regular expression failed: {detail}")
    try:
        result = json.loads(stdout)
    except (TypeError, ValueError) as err:
        raise HomeAssistantError(
            "Configured regular expression returned invalid data"
        ) from err
    if not isinstance(result, dict):
        raise HomeAssistantError("Configured regular expression returned invalid data")
    return result


def _run_regex_worker(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one bounded stdlib-regex operation in an isolated child interpreter."""
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-c", _REGEX_WORKER],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=_CONFIGURED_REGEX_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        raise HomeAssistantError(
            "Configured regular expression exceeded the 1 second execution limit"
        ) from err
    return _decode_regex_worker_result(
        completed.stdout, completed.stderr, completed.returncode
    )


async def _async_run_regex_worker(payload: dict[str, Any]) -> dict[str, Any]:
    """Run a killable regex worker whose lifetime follows async cancellation."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-c",
        _REGEX_WORKER,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    encoded = json.dumps(payload, ensure_ascii=False).encode()
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(encoded), timeout=_CONFIGURED_REGEX_TIMEOUT_SECONDS
        )
    except TimeoutError as err:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise HomeAssistantError(
            "Configured regular expression exceeded the 1 second execution limit"
        ) from err
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    return _decode_regex_worker_result(
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
        process.returncode or 0,
    )


async def async_search_configured_patterns(
    hass: HomeAssistant,
    checks: list[tuple[str, str, int]],
) -> list[bool]:
    """Evaluate configured searches with a hard process boundary and deadline."""
    if not checks:
        return []
    payload = {
        "op": "search_many",
        "items": [
            {"pattern": pattern, "text": text, "flags": flags}
            for pattern, text, flags in checks
        ],
    }
    result = await hass.async_add_executor_job(_run_regex_worker, payload)
    invalid = result.get("invalid")
    if isinstance(invalid, list) and invalid:
        first = invalid[0]
        detail = (
            first[1]
            if isinstance(first, list) and len(first) > 1
            else "invalid pattern"
        )
        raise HomeAssistantError(f"Invalid configured regular expression: {detail}")
    matches = result.get("results")
    if not isinstance(matches, list) or not all(
        isinstance(item, bool) for item in matches
    ):
        raise HomeAssistantError("Configured regular expression returned invalid data")
    if len(matches) != len(checks):
        raise HomeAssistantError(
            "Configured regular expression returned incomplete data"
        )
    return matches


def _speech_replacement_items(rules: object) -> list[dict[str, Any]] | None:
    """Return a bounded runtime rule payload, or None for malformed stored data."""
    if not isinstance(rules, list):
        return None
    if not rules:
        return []
    if len(rules) > MAX_SPEECH_REGEX_RULES:
        return None
    items: list[dict[str, Any]] = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, Mapping):
            return None
        pattern = rule.get("pattern")
        replacement = rule.get("replacement")
        if not isinstance(pattern, str) or not pattern:
            return None
        if not isinstance(replacement, str):
            return None
        if len(pattern) > MAX_SPEECH_REGEX_PATTERN_LENGTH:
            return None
        if len(replacement) > MAX_SPEECH_REGEX_REPLACEMENT_LENGTH:
            return None
        items.append(
            {
                "index": index,
                "pattern": pattern,
                "replacement": replacement,
            }
        )
    return items


def _speech_output_limit(input_chars: int) -> int:
    """Bound replacement expansion while allowing useful short-text substitutions."""
    return min(
        MAX_SPEECH_REPLACEMENT_OUTPUT_CHARS,
        max(
            MIN_SPEECH_REPLACEMENT_OUTPUT_ALLOWANCE,
            input_chars * MAX_SPEECH_REPLACEMENT_EXPANSION,
        ),
    )


async def _async_apply_speech_replacements(text: str, rules: object) -> str:
    """Apply all custom substitutions atomically or fail open to the input text."""
    items = _speech_replacement_items(rules)
    if items == []:
        return text
    if items is None:
        _LOGGER.warning(
            "Ignoring malformed speech regex replacements and preserving spoken text"
        )
        return text
    if len(text) > MAX_SPEECH_REPLACEMENT_INPUT_CHARS:
        _LOGGER.warning(
            ("Skipping speech regex replacements because input exceeds %d characters"),
            MAX_SPEECH_REPLACEMENT_INPUT_CHARS,
        )
        return text
    try:
        result = await _async_run_regex_worker(
            {
                "op": "sub_many",
                "text": text,
                "items": items,
                "max_output_chars": _speech_output_limit(len(text)),
            }
        )
    except HomeAssistantError as err:
        _LOGGER.warning(
            "Speech regex replacement failed; preserving spoken text: %s", err
        )
        return text
    except Exception as err:
        # Cancellation is a BaseException in supported Python versions and therefore
        # still propagates. Ordinary worker/serialization failures remain fail-open.
        _LOGGER.warning(
            "Speech regex worker failed; preserving spoken text: %s", type(err).__name__
        )
        return text

    invalid = result.get("invalid")
    if not isinstance(invalid, list):
        _LOGGER.warning(
            "Speech regex worker returned invalid diagnostics; preserving spoken text"
        )
        return text
    if invalid:
        first = invalid[0]
        invalid_index = first[0] if isinstance(first, list) and first else "unknown"
        _LOGGER.warning(
            "Speech regex replacement %s is invalid; preserving spoken text",
            invalid_index,
        )
        return text
    if result.get("overflow") is True:
        _LOGGER.warning(
            "Speech regex replacements exceeded the output growth limit; preserving spoken text"
        )
        return text
    value = result.get("text")
    if not isinstance(value, str):
        _LOGGER.warning(
            "Speech regex worker returned invalid text; preserving spoken text"
        )
        return text
    if len(value) > _speech_output_limit(len(text)):
        _LOGGER.warning(
            "Speech regex worker exceeded the output limit; preserving spoken text"
        )
        return text
    return value


async def async_process_speech_text(
    hass: HomeAssistant,
    original_text: str,
    agent_config: Mapping[str, Any],
) -> str:
    """Run the bounded completed-response speech pipeline used by live and Preview."""
    text = await hass.async_add_executor_job(
        partial(
            _built_in_cleanup,
            original_text,
            markdown=agent_config.get(
                CONF_SPEECH_STRIP_MARKDOWN, DEFAULT_SPEECH_STRIP_MARKDOWN
            ),
            urls=agent_config.get(CONF_SPEECH_STRIP_URLS, DEFAULT_SPEECH_STRIP_URLS),
        )
    )
    text = await _async_apply_speech_replacements(
        text,
        agent_config.get(
            CONF_SPEECH_REGEX_REPLACEMENTS, DEFAULT_SPEECH_REGEX_REPLACEMENTS
        ),
    )
    return await hass.async_add_executor_job(_final_whitespace_cleanup, text)


def _deferred_process_speech_text_factory(original_process_speech_text: Any):
    """Create the synchronous shim that only records custom-regex speech input."""

    def deferred_process_speech_text(
        original_text: str, agent_config: Mapping[str, Any]
    ) -> str:
        if _DEFER_SPEECH_PROCESSING.get() and has_custom_speech_replacements(
            agent_config
        ):
            _DEFERRED_SPEECH_INPUT.set((original_text, agent_config))
            return original_text
        return cast(str, original_process_speech_text(original_text, agent_config))

    return deferred_process_speech_text


def _install_speech_regex_isolation() -> None:
    """Defer live custom regex until after ChatLog has retained original content."""
    from . import conversation as conversation_module
    from .conversation import ExtendedOpenAIAgentEntity

    current = ExtendedOpenAIAgentEntity._async_handle_message
    if getattr(current, "_extended_openai_configurable_regex_executor", False):
        return

    original_handle_message = current
    original_process_speech_text = conversation_module.process_speech_text
    conversation_module.process_speech_text = _deferred_process_speech_text_factory(
        original_process_speech_text
    )

    async def async_handle_message(
        agent: Any,
        user_input: Any,
        chat_log: Any,
        request_options: Mapping[str, Any] | None = None,
    ) -> Any:
        subentry_data = getattr(getattr(agent, "subentry", None), "data", None)
        defer = bool(subentry_data and has_custom_speech_replacements(subentry_data))
        defer_token = _DEFER_SPEECH_PROCESSING.set(defer)
        input_token = _DEFERRED_SPEECH_INPUT.set(None)
        deferred_input: tuple[str, Mapping[str, Any]] | None = None
        try:
            result = await original_handle_message(
                agent, user_input, chat_log, request_options
            )
            deferred_input = _DEFERRED_SPEECH_INPUT.get()
        finally:
            _DEFERRED_SPEECH_INPUT.reset(input_token)
            _DEFER_SPEECH_PROCESSING.reset(defer_token)

        if deferred_input is not None:
            speech_text = await async_process_speech_text(agent.hass, *deferred_input)
            result.response.async_set_speech(speech_text)
        return result

    async_handle_message._extended_openai_configurable_regex_executor = True  # type: ignore[attr-defined]
    ExtendedOpenAIAgentEntity._async_handle_message = async_handle_message  # type: ignore[method-assign,assignment]


def _install_speech_preview_isolation() -> None:
    """Run Speech Preview through the same async bounded engine as live speech."""
    from . import management_ui

    current = management_ui.async_management_command
    if getattr(current, "_extended_openai_speech_preview_executor", False):
        return

    original_command = current
    original_process_speech_text = management_ui.process_speech_text
    management_ui.process_speech_text = _deferred_process_speech_text_factory(
        original_process_speech_text
    )

    async def async_management_command(
        hass: HomeAssistant,
        user_id: str,
        is_admin: bool,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        defer = message.get("action") == "speech_preview"
        defer_token = _DEFER_SPEECH_PROCESSING.set(defer)
        input_token = _DEFERRED_SPEECH_INPUT.set(None)
        deferred_input: tuple[str, Mapping[str, Any]] | None = None
        try:
            result = await original_command(hass, user_id, is_admin, message)
            deferred_input = _DEFERRED_SPEECH_INPUT.get()
        finally:
            _DEFERRED_SPEECH_INPUT.reset(input_token)
            _DEFER_SPEECH_PROCESSING.reset(defer_token)

        if deferred_input is not None:
            result = dict(result)
            result["speech_text"] = await async_process_speech_text(
                hass, *deferred_input
            )
        return result

    async_management_command._extended_openai_speech_preview_executor = True  # type: ignore[attr-defined]
    management_ui.async_management_command = async_management_command  # type: ignore[assignment]


def install_configurable_regex_isolation() -> None:
    """Install process-isolated handling for administrator-configured speech regex."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_speech_regex_isolation()
    _install_speech_preview_isolation()
    _INSTALLED = True
