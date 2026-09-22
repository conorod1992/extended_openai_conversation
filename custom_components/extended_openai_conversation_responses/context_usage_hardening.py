"""Keep context truncation reliable when providers omit stream usage metadata.

Provider-reported usage remains authoritative for accounting. A conservative local
estimate is attached only to the in-flight RequestUsage object so the existing
context-management decision can still fire; UsageManager is wrapped to strip that
estimate before totals/details are persisted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
import logging
import math
from typing import Any

from .request_static_cache import (
    formatted_tool_measurement,
    remember_formatted_tool_measurement,
)
from .usage import RequestUsage, extract_usage

_LOGGER = logging.getLogger(__name__)
_LOCAL_ESTIMATE_DETAIL = "__extended_openai_local_context_estimate"
_PARTIAL_PROVIDER_USAGE_DETAIL = "__extended_openai_partial_provider_usage"


def _has_token_count(usage: RequestUsage) -> bool:
    """Return whether normalized provider metadata contains any real token count."""
    return any(
        (
            usage.input_tokens,
            usage.output_tokens,
            usage.total_tokens,
            usage.cached_input_tokens,
            usage.reasoning_tokens,
        )
    )


def _local_estimate(usage: RequestUsage | None) -> int | None:
    """Return the request-local context estimate when one is currently active."""
    if usage is None:
        return None
    value = usage.details.get(_LOCAL_ESTIMATE_DETAIL)
    return value if isinstance(value, int) and value > 0 else None


def _restore_local_estimate(usage: RequestUsage | None, estimate: int | None) -> None:
    """Restore an estimate if a terminal provider event replaced it with zeros."""
    if usage is None or estimate is None or _has_token_count(usage):
        return
    usage.input_tokens = estimate
    usage.output_tokens = 0
    usage.total_tokens = estimate
    usage.cached_input_tokens = 0
    usage.reasoning_tokens = 0
    usage.details = {_LOCAL_ESTIMATE_DETAIL: estimate}


def _copy_usage(target: RequestUsage, source: RequestUsage) -> None:
    """Replace an in-flight estimate with provider-reported usage."""
    target.input_tokens = source.input_tokens
    target.output_tokens = source.output_tokens
    target.total_tokens = source.total_tokens
    target.cached_input_tokens = source.cached_input_tokens
    target.reasoning_tokens = source.reasoning_tokens
    target.details = dict(source.details)


def _capture_provider_usage(
    target: RequestUsage | None,
    raw_usage: Any,
    *,
    local_estimate: int | None = None,
) -> bool:
    """Normalize provider usage while preserving a missing-input context estimate."""
    if target is None or raw_usage is None:
        return False
    normalized = extract_usage(raw_usage)
    if not _has_token_count(normalized):
        return False

    estimate = local_estimate if local_estimate is not None else _local_estimate(target)
    _copy_usage(target, normalized)
    if estimate is not None and normalized.input_tokens <= 0:
        target.input_tokens = estimate
        target.details[_LOCAL_ESTIMATE_DETAIL] = estimate
        target.details[_PARTIAL_PROVIDER_USAGE_DETAIL] = 1
    return True


def _serialized_characters(value: Any) -> tuple[int, int]:
    """Return total and non-ASCII characters for deterministic JSON serialization."""
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    if serialized.isascii():
        return len(serialized), 0
    non_ascii = sum(ord(character) > 127 for character in serialized)
    return len(serialized), non_ascii


def measure_provider_input(
    input_value: Any,
    tools: Any = None,
    *,
    tool_measurement: tuple[int, int] | None = None,
) -> tuple[int, int, int]:
    """Return serialized input/tool characters and conservative context tokens."""
    input_characters, input_non_ascii = _serialized_characters(input_value)
    tool_characters = 0
    tool_non_ascii = 0
    if tools:
        if tool_measurement is None:
            tool_measurement = _serialized_characters(tools)
        tool_characters, tool_non_ascii = tool_measurement

    total_characters = input_characters + tool_characters
    non_ascii = input_non_ascii + tool_non_ascii
    ascii_characters = max(0, total_characters - non_ascii)
    conservative_tokens = max(
        1,
        math.ceil(ascii_characters / 3) + (non_ascii * 2),
    )
    return input_characters, tool_characters, conservative_tokens


def estimate_provider_input_tokens(input_value: Any, tools: Any = None) -> int:
    """Conservatively estimate provider input tokens without a tokenizer dependency.

    ASCII-heavy request JSON is budgeted at roughly three characters per token.
    Non-ASCII characters are budgeted more aggressively because many tokenizers encode
    them at one or more tokens per character. This estimate is intentionally used only
    as a safety fallback for truncation and is never reported as provider/billing usage.
    """
    return measure_provider_input(input_value, tools)[2]


def usage_for_accounting(usage: RequestUsage | None) -> RequestUsage | None:
    """Strip local estimates while preserving genuine partial provider usage."""
    if usage is None or _LOCAL_ESTIMATE_DETAIL not in usage.details:
        return usage
    if _PARTIAL_PROVIDER_USAGE_DETAIL not in usage.details:
        return RequestUsage()

    details = {
        key: value
        for key, value in usage.details.items()
        if key not in {_LOCAL_ESTIMATE_DETAIL, _PARTIAL_PROVIDER_USAGE_DETAIL}
    }
    return RequestUsage(
        input_tokens=0,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        details=details,
    )


async def normalized_chat_stream(
    chat_log: Any, result: Any, request_usage: RequestUsage | None
) -> AsyncIterator[Any]:
    async for chunk in result:
        estimate = _local_estimate(request_usage)
        raw_usage = getattr(chunk, "usage", None)
        captured = _capture_provider_usage(request_usage, raw_usage)
        # The original transformer handles the standard final usage-only
        # chunk. Trace only non-standard usage attached to a normal choice.
        if captured and getattr(chunk, "choices", None):
            provider_usage = extract_usage(raw_usage)
            chat_log.async_trace(
                {
                    "stats": {
                        "input_tokens": provider_usage.input_tokens,
                        "output_tokens": provider_usage.output_tokens,
                    }
                }
            )
        yield chunk
        if captured:
            # The stock transformer writes normalized provider usage into the
            # same object while consuming this event. Reconcile afterwards so
            # partial usage cannot erase the request-local input estimate.
            _capture_provider_usage(request_usage, raw_usage, local_estimate=estimate)
        else:
            _restore_local_estimate(request_usage, estimate)


async def normalized_responses_stream(
    chat_log: Any, result: Any, request_usage: RequestUsage | None
) -> AsyncIterator[Any]:
    async for event in result:
        estimate = _local_estimate(request_usage)
        response = getattr(event, "response", None)
        raw_usage = getattr(response, "usage", None)
        if raw_usage is None:
            raw_usage = getattr(event, "usage", None)
        captured = _capture_provider_usage(request_usage, raw_usage)
        event_type = getattr(event, "type", "")
        if captured and event_type not in {
            "response.completed",
            "response.incomplete",
        }:
            provider_usage = extract_usage(raw_usage)
            chat_log.async_trace(
                {
                    "stats": {
                        "input_tokens": provider_usage.input_tokens,
                        "output_tokens": provider_usage.output_tokens,
                    }
                }
            )
        yield event
        if captured:
            # Terminal Responses events also write into request_usage in the
            # stock transformer before this generator resumes.
            _capture_provider_usage(request_usage, raw_usage, local_estimate=estimate)
        else:
            _restore_local_estimate(request_usage, estimate)


def estimate_prepared_request(
    entity: Any, request_usage: RequestUsage, input_value: Any, tools: Any
) -> None:
    """Measure the already assembled provider round without rebuilding tools/history."""
    # Attachments are excluded from the established character footprint. Avoid
    # rebuilding the complete input list on the overwhelmingly common text-only path.
    # Materialize non-list iterables once so the eligibility scan cannot consume them.
    measurement_source = (
        input_value if isinstance(input_value, list) else list(input_value)
    )
    has_multipart_user_content = any(
        isinstance(item, dict)
        and item.get("role") == "user"
        and isinstance(item.get("content"), list)
        for item in measurement_source
    )
    measured = (
        [
            {
                **item,
                "content": "".join(
                    str(part.get("text", ""))
                    for part in item["content"]
                    if part.get("type") in {"text", "input_text"}
                ),
            }
            if isinstance(item, dict)
            and item.get("role") == "user"
            and isinstance(item.get("content"), list)
            else item
            for item in measurement_source
        ]
        if has_multipart_user_content
        else measurement_source
    )
    tool_measurement = formatted_tool_measurement(tools) if tools else None
    if tools and tool_measurement is None:
        tool_measurement = _serialized_characters(tools)
        remember_formatted_tool_measurement(tools, tool_measurement)
    try:
        estimate = measure_provider_input(
            measured,
            tools,
            tool_measurement=tool_measurement,
        )[2]
    except Exception:
        _LOGGER.debug("Unable to estimate provider input size", exc_info=True)
        return
    request_usage.input_tokens = estimate
    request_usage.total_tokens = estimate
    request_usage.details = {_LOCAL_ESTIMATE_DETAIL: estimate}
