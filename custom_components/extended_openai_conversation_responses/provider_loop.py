"""Provider-round safety budgeting for multi-step tool conversations."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError

from .const import MAX_FUNCTION_GROUP_LOAD_ROUNDS

# Absolute anti-loop ceiling. The normal per-request limit is derived from the
# configured function budget and is usually much smaller than this.
MAX_PROVIDER_REQUESTS = 64
_FINAL_RESPONSE_REQUESTS = 1
_CONDITIONAL_FINALIZATION_RETRY_REQUESTS = 1


def provider_request_limit(
    max_function_calls: int, *, conditional_continue: bool
) -> int:
    """Return enough provider rounds to honor the configured action-tool budget.

    Function Group loader rounds and Conditional Continue finalization are internal
    orchestration and do not consume the user-facing function-call budget, so their
    bounded round allowances are reserved separately.
    """
    if max_function_calls < 0:
        # Preserve the direct/legacy negative-limit convention for unlimited tool
        # calls while still retaining an absolute provider-loop safety boundary.
        return MAX_PROVIDER_REQUESTS

    requested = (
        max_function_calls
        + MAX_FUNCTION_GROUP_LOAD_ROUNDS
        + _FINAL_RESPONSE_REQUESTS
        + (
            _CONDITIONAL_FINALIZATION_RETRY_REQUESTS
            if conditional_continue
            else 0
        )
    )
    return min(MAX_PROVIDER_REQUESTS, max(1, requested))


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
