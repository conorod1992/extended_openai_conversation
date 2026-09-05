"""Bound administrator-configured regex work outside the Home Assistant process."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from functools import partial
import json
import logging
import subprocess
import sys
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_SPEECH_REGEX_REPLACEMENTS,
    CONF_SPEECH_STRIP_MARKDOWN,
    CONF_SPEECH_STRIP_URLS,
    DEFAULT_SPEECH_REGEX_REPLACEMENTS,
    DEFAULT_SPEECH_STRIP_MARKDOWN,
    DEFAULT_SPEECH_STRIP_URLS,
)
from .speech import (
    _built_in_cleanup,
    _final_whitespace_cleanup,
    has_custom_speech_replacements,
)

_LOGGER = logging.getLogger(__name__)
_CONFIGURED_REGEX_TIMEOUT_SECONDS = 1.0

# Keep the child interpreter deliberately tiny: it imports only stdlib modules and
# receives data over stdin. A catastrophic Python ``re`` match can therefore hold
# only the child's GIL, and subprocess.run can terminate that child at the deadline.
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
    text = payload.get("text", "")
    invalid = []
    for item in payload.get("items", []):
        try:
            text = re.sub(item["pattern"], item["replacement"], text)
        except re.error as err:
            invalid.append([item["index"], str(err)])
    output = {"text": text, "invalid": invalid}
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
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "regex worker failed"
        raise HomeAssistantError(f"Configured regular expression failed: {detail}")
    try:
        result = json.loads(completed.stdout)
    except (TypeError, ValueError) as err:
        raise HomeAssistantError(
            "Configured regular expression returned invalid data"
        ) from err
    if not isinstance(result, dict):
        raise HomeAssistantError("Configured regular expression returned invalid data")
    return result


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


async def _async_apply_speech_replacements(
    hass: HomeAssistant,
    text: str,
    rules: object,
) -> str:
    """Apply configured speech substitutions in one bounded child process."""
    if not isinstance(rules, list) or not rules:
        return text
    items: list[dict[str, Any]] = []
    for index, rule in enumerate(rules):
        try:
            if not isinstance(rule, Mapping):
                raise TypeError("rule is not an object")
            items.append(
                {
                    "index": index,
                    "pattern": str(rule["pattern"]),
                    "replacement": str(rule["replacement"]),
                }
            )
        except KeyError, TypeError:
            _LOGGER.warning("Skipping invalid speech regex replacement %s", index)
    if not items:
        return text
    result = await hass.async_add_executor_job(
        _run_regex_worker,
        {"op": "sub_many", "text": text, "items": items},
    )
    invalid = result.get("invalid")
    if isinstance(invalid, list):
        for item in invalid:
            invalid_index = item[0] if isinstance(item, list) and item else "unknown"
            _LOGGER.warning(
                "Skipping invalid speech regex replacement %s", invalid_index
            )
    value = result.get("text")
    if not isinstance(value, str):
        raise HomeAssistantError(
            "Configured speech regular expression returned invalid data"
        )
    return value


async def _async_process_speech_text(
    hass: HomeAssistant,
    original_text: str,
    agent_config: Mapping[str, Any],
) -> str:
    """Preserve the completed-response speech pipeline with isolated custom regex."""
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
        hass,
        text,
        agent_config.get(
            CONF_SPEECH_REGEX_REPLACEMENTS, DEFAULT_SPEECH_REGEX_REPLACEMENTS
        ),
    )
    return await hass.async_add_executor_job(_final_whitespace_cleanup, text)


def _install_speech_regex_isolation() -> None:
    """Defer completed-response custom speech regex until after the async handler."""
    from . import conversation as conversation_module
    from .conversation import ExtendedOpenAIAgentEntity

    current = ExtendedOpenAIAgentEntity._async_handle_message
    if getattr(current, "_extended_openai_configurable_regex_executor", False):
        return

    original_handle_message = current
    original_process_speech_text = conversation_module.process_speech_text

    def deferred_process_speech_text(
        original_text: str, agent_config: Mapping[str, Any]
    ) -> str:
        if _DEFER_SPEECH_PROCESSING.get() and has_custom_speech_replacements(
            agent_config
        ):
            _DEFERRED_SPEECH_INPUT.set((original_text, agent_config))
            return original_text
        return original_process_speech_text(original_text, agent_config)

    conversation_module.process_speech_text = deferred_process_speech_text

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
            speech_text = await _async_process_speech_text(
                agent.hass,
                *deferred_input,
            )
            result.response.async_set_speech(speech_text)
        return result

    async_handle_message._extended_openai_configurable_regex_executor = True  # type: ignore[attr-defined]
    ExtendedOpenAIAgentEntity._async_handle_message = async_handle_message  # type: ignore[method-assign,assignment]


def install_configurable_regex_isolation() -> None:
    """Install process-isolated handling for administrator-configured speech regex."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_speech_regex_isolation()
    _INSTALLED = True
