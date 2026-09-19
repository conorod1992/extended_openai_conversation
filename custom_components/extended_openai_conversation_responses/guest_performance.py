"""Cheap Guest Mode fast path for normal owner requests."""

from __future__ import annotations

from typing import Any


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
