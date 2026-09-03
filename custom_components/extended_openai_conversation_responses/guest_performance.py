"""Cheap Guest Mode fast path for normal owner requests."""

from __future__ import annotations

from typing import Any

_INSTALLED = False


def can_reuse_request_policy(request_policy: Any, guest_mode: Any) -> bool:
    """Return whether the request-stable unrestricted policy is still sufficient.

    A request that started in Guest Mode must remain pinned to that restriction.
    A normal owner request may reuse its already-resolved policy while Guest Mode is
    still inactive. If a scheduled or model-triggered Guest interval becomes active
    mid-request, fall back to the full resolver so permissions can only tighten.
    """
    return bool(
        request_policy is not None
        and not request_policy.guest_active
        and (guest_mode is None or not guest_mode.is_active())
    )


def install_guest_policy_fast_path() -> None:
    """Avoid rebuilding the complete Guest capability policy on every helper call."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from . import conversation

    original_effective_guest_policy = (
        conversation.ExtendedOpenAIAgentEntity._effective_guest_policy
    )

    def effective_guest_policy_fast(self: Any) -> Any:
        request_policy = conversation._ACTIVE_GUEST_POLICY.get()
        if can_reuse_request_policy(request_policy, self._guest_mode):
            return request_policy
        return original_effective_guest_policy(self)

    conversation.ExtendedOpenAIAgentEntity._effective_guest_policy = (  # type: ignore[method-assign]
        effective_guest_policy_fast
    )

    # Request-scoped tool reuse relies on this fast path returning the same resolved
    # policy object while permissions remain unchanged, so install it afterwards.
    from .runtime_cleanup import install_runtime_cleanup

    install_runtime_cleanup()

    # Static request caching intentionally wraps the final runtime tool snapshot so
    # policy/group changes remain authoritative invalidation boundaries.
    from .request_static_cache import install_request_static_caching

    install_request_static_caching()

    # Management loading optimizations also need to be installed before the panel
    # and websocket endpoints are registered. Reuse this existing startup
    # performance hook rather than adding another integration lifecycle callback.
    from .management_loading_performance import install_management_loading_optimizations

    install_management_loading_optimizations()
