"""Focused residual coverage for the guest-policy fast path."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses import guest_performance


def test_can_reuse_request_policy_requires_unrestricted_inactive_guest_mode() -> None:
    inactive_policy = SimpleNamespace(guest_active=False)
    active_policy = SimpleNamespace(guest_active=True)
    inactive_guest_mode = SimpleNamespace(is_active=lambda: False)
    active_guest_mode = SimpleNamespace(is_active=lambda: True)

    assert guest_performance.can_reuse_request_policy(inactive_policy, None) is True
    assert (
        guest_performance.can_reuse_request_policy(inactive_policy, inactive_guest_mode)
        is True
    )
    assert (
        guest_performance.can_reuse_request_policy(None, inactive_guest_mode) is False
    )
    assert (
        guest_performance.can_reuse_request_policy(active_policy, inactive_guest_mode)
        is False
    )
    assert (
        guest_performance.can_reuse_request_policy(inactive_policy, active_guest_mode)
        is False
    )
