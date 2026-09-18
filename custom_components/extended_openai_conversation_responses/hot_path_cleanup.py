"""Very low-risk conversation hot-path cleanup.

These optimizations remove bookkeeping or redundant matching work that cannot change
provider input, tool availability, or the selected deterministic Request Rule.
"""

from __future__ import annotations

import json
import time
from typing import Any

_INSTALLED = False


def install_hot_path_cleanup() -> None:
    """Install post-lifecycle hot-path optimizations once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_debug_single_conversion()
    _install_broadcast_cold_path_guard()
    _INSTALLED = True


def _debug_event_has_text(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    event_type = str(data.get("type", ""))
    if "output_text.delta" in event_type and data.get("delta"):
        return True
    choices = data.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict) and delta.get("content"):
                return True
    return False


def _debug_event_has_action(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    event_type = str(data.get("type", ""))
    if "function_call" in event_type or "web_search_call" in event_type:
        return True
    item = data.get("item")
    return isinstance(item, dict) and item.get("type") in {
        "function_call",
        "web_search_call",
    }


def _debug_usage(data: Any) -> dict[str, int] | None:
    if not isinstance(data, dict):
        return None
    usage = data.get("usage")
    response = data.get("response")
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if not isinstance(usage, dict):
        return None

    def integer(value: Any) -> int:
        return value if isinstance(value, int) and value >= 0 else 0

    input_tokens = integer(usage.get("input_tokens")) or integer(
        usage.get("prompt_tokens")
    )
    output_tokens = integer(usage.get("output_tokens")) or integer(
        usage.get("completion_tokens")
    )
    total_tokens = integer(usage.get("total_tokens")) or input_tokens + output_tokens
    input_details = usage.get("input_tokens_details") or usage.get(
        "prompt_tokens_details"
    )
    output_details = usage.get("output_tokens_details") or usage.get(
        "completion_tokens_details"
    )
    cached = (
        integer(input_details.get("cached_tokens"))
        if isinstance(input_details, dict)
        else 0
    )
    reasoning = (
        integer(output_details.get("reasoning_tokens"))
        if isinstance(output_details, dict)
        else 0
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": cached,
        "reasoning_tokens": reasoning,
    }


def _install_debug_single_conversion() -> None:
    """Convert each provider debug event once instead of repeatedly walking it."""
    from . import debug

    request_type: Any = debug.DebugProviderRequest

    def add_event(request: Any, event: Any) -> None:
        now_ms = int((time.monotonic() - request._started_monotonic) * 1000)
        if request.first_event_ms is None:
            request.first_event_ms = now_ms

        serialized = debug._jsonable(event)
        if request.first_text_ms is None and _debug_event_has_text(serialized):
            request.first_text_ms = now_ms
        if request.first_action_ms is None and _debug_event_has_action(serialized):
            request.first_action_ms = now_ms
        if usage := _debug_usage(serialized):
            request.usage = usage
        if request.response_events_truncated:
            return

        # ``serialized`` is already bounded and JSON-safe, so do not recursively
        # normalize it again merely to determine its retained size.
        event_size = len(
            json.dumps(
                serialized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        if request._event_bytes + event_size > debug.DEBUG_MAX_EVENT_BYTES:
            request.response_events_truncated = True
            return
        request.response_events.append(serialized)
        request._event_bytes += event_size

    request_type.add_event = add_event


def _install_broadcast_cold_path_guard() -> None:
    """Do not initialize Intercom for requests that cannot be targeted broadcasts."""
    from . import local_intents

    original = local_intents._async_try_targeted_broadcast

    async def try_targeted_broadcast(hass: Any, user_input: Any) -> Any:
        text = getattr(user_input, "text", None)
        if (
            not isinstance(text, str)
            or not text.strip()
            or not local_intents.is_targeted_broadcast_request(text)
        ):
            return None
        return await original(hass, user_input)

    local_intents._async_try_targeted_broadcast = try_targeted_broadcast
