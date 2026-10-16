"""Keep context truncation reliable when providers omit stream usage metadata.

Provider-reported usage remains authoritative for accounting. A conservative local
estimate is attached only to the in-flight RequestUsage object so the existing
context-management decision can still fire; UsageManager is wrapped to strip that
estimate before totals/details are persisted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import replace
import json
import logging
import math
from typing import Any

from .operational_errors import log_handled_failure
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


def _copy_usage(target: RequestUsage, source: RequestUsage) -> None:
    """Copy reconciled provider counts into the in-flight record."""
    target.input_tokens = source.input_tokens
    target.output_tokens = source.output_tokens
    target.total_tokens = source.total_tokens
    target.cached_input_tokens = source.cached_input_tokens
    target.reasoning_tokens = source.reasoning_tokens
    target.details = dict(source.details)


def _usage_value(source: Any, key: str) -> Any:
    """Read supplied fields without treating SDK model defaults as reports."""
    supplied = getattr(source, "model_fields_set", None)
    if isinstance(supplied, set) and key not in supplied:
        return None
    return (
        source.get(key) if isinstance(source, Mapping) else getattr(source, key, None)
    )


def _reported_count(value: Any) -> bool:
    """Distinguish an explicitly reported zero from an absent/invalid count."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _reported_details(details: Any) -> Iterator[tuple[str, int]]:
    """Yield valid explicit detail counts, excluding SDK model defaults."""
    supplied = getattr(details, "model_fields_set", None)
    if hasattr(details, "model_dump"):
        details = details.model_dump(exclude_none=True)
    elif not isinstance(details, Mapping) and hasattr(details, "__dict__"):
        details = vars(details)
    if isinstance(details, Mapping):
        for name, value in details.items():
            if isinstance(supplied, set) and name not in supplied:
                continue
            if _reported_count(value):
                yield name, value


def _has_reported_details(raw: Any) -> bool:
    """Accept sparse detail reports, including explicit zero corrections."""
    return any(
        next(_reported_details(_usage_value(raw, alias)), None) is not None
        for alias in (
            "input_tokens_details",
            "prompt_tokens_details",
            "output_tokens_details",
            "completion_tokens_details",
        )
    )


def _merge_provider_usage(
    previous: RequestUsage, raw: Any, normalized: RequestUsage
) -> RequestUsage:
    """Update supplied cumulative fields without adding repeated snapshots."""
    merged = replace(previous, details=dict(previous.details))
    components_updated: set[str] = set()
    for field, aliases in (
        ("input_tokens", ("input_tokens", "prompt_tokens")),
        ("output_tokens", ("output_tokens", "completion_tokens")),
    ):
        if any(_reported_count(_usage_value(raw, alias)) for alias in aliases):
            setattr(merged, field, getattr(normalized, field))
            components_updated.add(field)
    total = _usage_value(raw, "total_tokens")
    if _reported_count(total) and total > 0:
        merged.total_tokens = total
    elif components_updated and (
        len(components_updated) == 2
        or (merged.input_tokens > 0 and merged.output_tokens > 0)
        or previous.total_tokens == previous.input_tokens + previous.output_tokens
    ):
        # Derive from all known provider components, never a local input estimate.
        # Preserve an unexplained provider total until its components are known.
        merged.total_tokens = merged.input_tokens + merged.output_tokens

    for prefix, aliases, key, field in (
        (
            "input",
            ("input_tokens_details", "prompt_tokens_details"),
            "cached_tokens",
            "cached_input_tokens",
        ),
        (
            "output",
            ("output_tokens_details", "completion_tokens_details"),
            "reasoning_tokens",
            "reasoning_tokens",
        ),
    ):
        details = _usage_value(raw, aliases[0]) or _usage_value(raw, aliases[1])
        if _reported_count(_usage_value(details, key)):
            setattr(merged, field, getattr(normalized, field))
        for name, value in _reported_details(details):
            detail_key = f"{prefix}_{name}"
            if value:
                merged.details[detail_key] = value
            else:
                merged.details.pop(detail_key, None)
    return merged


def _capture_provider_usage(
    target: RequestUsage | None, raw_usage: Any
) -> RequestUsage | None:
    """Apply one usable provider snapshot and return its provider-only diagnostics.

    Missing/all-zero headline reports leave the current snapshot intact. Explicit
    details can clear prior counts; local estimates stay separate from accounting.
    """
    if target is None or raw_usage is None:
        return None
    normalized = extract_usage(raw_usage)
    if _has_token_count(normalized) or _has_reported_details(raw_usage):
        estimate = _local_estimate(target)
        provider = usage_for_accounting(target)
        assert provider is not None
        merged = _merge_provider_usage(provider, raw_usage, normalized)
        _copy_usage(target, merged)
        if estimate is not None and merged.input_tokens <= 0:
            target.input_tokens = estimate
            target.details[_LOCAL_ESTIMATE_DETAIL] = estimate
            target.details[_PARTIAL_PROVIDER_USAGE_DETAIL] = 1
    return normalized


def _trace_provider_usage(chat_log: Any, reported: RequestUsage) -> None:
    """Trace raw provider counts, never the in-flight context estimate."""
    chat_log.async_trace(
        {
            "stats": {
                "input_tokens": reported.input_tokens,
                "output_tokens": reported.output_tokens,
            }
        }
    )


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


# Fallback budgets are deliberately independent of encoded transport size.
# Provider-reported usage remains authoritative; model/image/PDF costs vary.
_IMAGE_INPUT_TOKEN_BUDGET = 2048
_FILE_INPUT_TOKEN_BUDGET = 4096


def _attachment_aware_input(input_value: Any) -> tuple[Any, int]:
    """Project user attachments out of text measurement without changing requests."""
    if not isinstance(input_value, list):
        return input_value, 0
    projected = input_value
    attachment_tokens = 0
    for index, item in enumerate(input_value):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        parts: list[Any] = []
        for part in content:
            if not isinstance(part, dict):
                parts.append(str(part))
            elif part.get("type") in {"input_image", "image_url"}:
                attachment_tokens += _IMAGE_INPUT_TOKEN_BUDGET
            elif part.get("type") in {"input_file", "file"}:
                attachment_tokens += _FILE_INPUT_TOKEN_BUDGET
            else:
                parts.append(part)
        if projected is input_value:
            projected = list(input_value)
        projected[index] = {**item, "content": parts}
    return projected, attachment_tokens


def measure_provider_input(
    input_value: Any,
    tools: Any = None,
    *,
    tool_measurement: tuple[int, int] | None = None,
) -> tuple[int, int, int]:
    """Return serialized input/tool characters and conservative context tokens."""
    measured, attachment_tokens = _attachment_aware_input(input_value)
    input_characters, input_non_ascii = _serialized_characters(measured)
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
        math.ceil(ascii_characters / 3) + (non_ascii * 2) + attachment_tokens,
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
    """Apply usage before the Chat parser can yield, fail or be closed."""
    async for chunk in result:
        raw_usage = getattr(chunk, "usage", None)
        reported = _capture_provider_usage(request_usage, raw_usage)
        if (
            reported is not None
            and _has_token_count(reported)
            and getattr(chunk, "choices", None)
        ):
            _trace_provider_usage(chat_log, reported)
        yield chunk


async def normalized_responses_stream(
    chat_log: Any, result: Any, request_usage: RequestUsage | None
) -> AsyncIterator[Any]:
    """Apply usage before the Responses parser can yield, fail or be closed."""
    async for event in result:
        response = getattr(event, "response", None)
        raw_usage = getattr(response, "usage", None)
        if raw_usage is None:
            raw_usage = getattr(event, "usage", None)
        reported = _capture_provider_usage(request_usage, raw_usage)
        if (
            reported is not None
            and _has_token_count(reported)
            and getattr(event, "type", "")
            not in {"response.completed", "response.incomplete"}
        ):
            _trace_provider_usage(chat_log, reported)
        yield event


def estimate_prepared_request(
    entity: Any, request_usage: RequestUsage, input_value: Any, tools: Any
) -> None:
    """Measure the already assembled provider round without rebuilding tools/history."""
    # Materialize once; the shared measurement handles encoded attachments for
    # both preflight pruning and request diagnostics.
    measured = input_value if isinstance(input_value, list) else list(input_value)
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
    except Exception as err:
        log_handled_failure(
            _LOGGER, "Unable to estimate provider input size", err, level=logging.DEBUG
        )
        return
    request_usage.input_tokens = estimate
    request_usage.total_tokens = estimate
    request_usage.details = {_LOCAL_ESTIMATE_DETAIL: estimate}
