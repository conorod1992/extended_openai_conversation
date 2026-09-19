"""Very low-risk conversation hot-path cleanup.

These optimizations remove bookkeeping or redundant matching work that cannot change
provider input, tool availability, or the selected deterministic Request Rule.
"""

from __future__ import annotations

from typing import Any


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
