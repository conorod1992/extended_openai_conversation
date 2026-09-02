"""Tests for the Guest Mode hot-path optimization."""

from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses.guest_performance import (
    can_reuse_request_policy,
)


class _GuestMode:
    def __init__(self, active: bool) -> None:
        self.active = active
        self.calls = 0

    def is_active(self) -> bool:
        self.calls += 1
        return self.active


def test_unrestricted_request_reuses_policy_while_guest_mode_inactive() -> None:
    policy = SimpleNamespace(guest_active=False)
    guest_mode = _GuestMode(False)

    assert can_reuse_request_policy(policy, guest_mode) is True
    assert guest_mode.calls == 1


def test_unrestricted_request_rechecks_when_guest_mode_activates() -> None:
    policy = SimpleNamespace(guest_active=False)
    guest_mode = _GuestMode(True)

    assert can_reuse_request_policy(policy, guest_mode) is False
    assert guest_mode.calls == 1


def test_guest_request_never_expands_even_if_schedule_is_now_inactive() -> None:
    policy = SimpleNamespace(guest_active=True)
    guest_mode = _GuestMode(False)

    assert can_reuse_request_policy(policy, guest_mode) is False
    # Short-circuiting does not even consult the current schedule for a request that
    # must remain pinned to its original restriction.
    assert guest_mode.calls == 0
