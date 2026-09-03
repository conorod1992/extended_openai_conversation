"""Absolute provider-loop safety for multi-step tool conversations."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError

# Ordinary model-requested functions are already constrained independently by the
# configured per-conversation execution budget. This is deliberately a separate,
# larger emergency ceiling for pathological/non-compliant provider loops.
MAX_PROVIDER_REQUESTS = 64


def assert_provider_loop_completed(chat_log: object, request_limit: int) -> None:
    """Turn provider-loop exhaustion into an explicit failure."""
    outstanding = getattr(chat_log, "unresponded_tool_results", None)
    if not outstanding:
        return
    try:
        count = len(outstanding)
    except TypeError:
        count = 1
    raise HomeAssistantError(
        "Provider tool loop exceeded the safety limit of "
        f"{request_limit} requests with {count} unresolved tool result(s)"
    )
