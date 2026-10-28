"""Keep context measurements separate from provider-reported token accounting."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass, field as dataclass_field, replace
from hashlib import sha256
import json
import logging
import math
from typing import Any

from .usage import RequestUsage, extract_usage

_LOGGER = logging.getLogger(__name__)
_TRAILING_USAGE_TIMEOUT = 0.2
_TRAILING_USAGE_EVENT_LIMIT = 128


def prepared_input_revision(input_value: Any) -> str:
    """Fingerprint an outgoing input without retaining another payload copy."""
    digest = sha256()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, default=str)
    for chunk in encoder.iterencode(input_value):
        digest.update(chunk.encode())
    return digest.hexdigest()


async def async_prepared_input_revision(input_value: Any) -> str:
    """Fingerprint a stable prepared request off-loop, including attachment bytes."""
    return await asyncio.to_thread(prepared_input_revision, input_value)


@dataclass(frozen=True, slots=True)
class ContextInputMeasurement:
    """A request-local count, independent of cumulative usage accounting."""

    tokens: int
    history_length: int
    input_revision: str

    async def async_applies_to(self, prepared_prefix: Any) -> bool:
        """Allow appended output, but never a rewritten or reduced input prefix."""
        return (
            await async_prepared_input_revision(prepared_prefix) == self.input_revision
        )


@dataclass(slots=True)
class RequestTokenMeasurements:
    """One provider round's reported snapshot and separate context fallback.

    Only ``reported`` is eligible for accounting. Positive provider input retires
    the fallback permanently, including if a later report corrects input to zero.
    The immutable revision-bound context measurement is made after the round.
    """

    reported: RequestUsage = dataclass_field(default_factory=RequestUsage)
    context_estimate: int | None = None

    @property
    def context_tokens(self) -> int:
        """Retain the existing input, active-estimate, then provider-total policy."""
        return (
            self.reported.input_tokens
            or self.context_estimate
            or self.reported.total_tokens
        )


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
    target: RequestTokenMeasurements | None, raw_usage: Any
) -> RequestUsage | None:
    """Apply one usable provider snapshot and return its provider-only diagnostics.

    Missing/all-zero headline reports leave the current snapshot intact. Explicit
    details can clear prior counts; local estimates stay separate from accounting.
    """
    if target is None or raw_usage is None:
        return None
    normalized = extract_usage(raw_usage)
    if _has_token_count(normalized) or _has_reported_details(raw_usage):
        target.reported = _merge_provider_usage(target.reported, raw_usage, normalized)
        if target.reported.input_tokens > 0:
            target.context_estimate = None
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
) -> tuple[int, int, int]:
    """Return serialized input/tool characters and conservative context tokens."""
    measured, attachment_tokens = _attachment_aware_input(input_value)
    input_characters, input_non_ascii = _serialized_characters(measured)
    tool_characters = 0
    tool_non_ascii = 0
    if tools:
        tool_characters, tool_non_ascii = _serialized_characters(tools)

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


async def async_recover_trailing_usage(
    result: Any, usage: RequestTokenMeasurements
) -> None:
    """Read accounting only after an output interpretation failure, best effort.

    A separate task and bounded wait keep an uncooperative iterator from extending
    the deadline or delaying caller cancellation. Never await its cancellation;
    the request owner still closes the provider stream. Expired/cancelled reads
    cannot change accounting, even if an iterator suppresses cancellation.
    """
    owner = asyncio.current_task()
    if owner is not None and owner.cancelling():
        raise asyncio.CancelledError
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _TRAILING_USAGE_TIMEOUT
    active = True

    async def collect() -> None:
        try:
            iterator = aiter(result)
            for _ in range(_TRAILING_USAGE_EVENT_LIMIT):
                if not active or loop.time() >= deadline:
                    return
                event = await anext(iterator)
                if not active or loop.time() >= deadline:
                    return
                response = getattr(event, "response", None)
                raw = getattr(response, "usage", None)
                if raw is None:
                    raw = getattr(event, "usage", None)
                _capture_provider_usage(usage, raw)
        except Exception:
            # EOF, transport failures and malformed metadata cannot replace the
            # output error that caused recovery. No parsing/tracing runs here.
            return

    def finished(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    task = asyncio.create_task(collect())
    task.add_done_callback(finished)
    try:
        await asyncio.wait({task}, timeout=max(0, deadline - loop.time()))
    except Exception:
        # Recovery is optional; only caller cancellation may supersede failure.
        pass
    finally:
        active = False
        if not task.done():
            task.cancel()


async def async_validate_tool_call_ids_with_usage(
    calls: Any, result: Any, usage: RequestTokenMeasurements
) -> None:
    """Keep ambiguous calls fatal while recovering bounded provider accounting."""
    from homeassistant.exceptions import HomeAssistantError

    from .tool_exchange import validate_tool_call_ids

    try:
        validate_tool_call_ids(calls)
    except HomeAssistantError:
        await async_recover_trailing_usage(result, usage)
        raise


async def normalized_chat_stream(
    chat_log: Any, result: Any, request_usage: RequestTokenMeasurements | None
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
    chat_log: Any, result: Any, request_usage: RequestTokenMeasurements | None
) -> AsyncIterator[Any]:
    """Apply usage before the Responses parser can yield, fail or be closed."""
    iterator = aiter(result)
    try:
        async for event in iterator:
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

    finally:
        if (close := getattr(iterator, "aclose", None)) is not None:
            await close()
