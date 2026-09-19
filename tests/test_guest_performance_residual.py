"""Focused residual coverage for the guest-policy fast path."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation,
    guest_performance,
    management_loading_performance,
    request_static_cache,
)


def _install_with_controlled_original(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Any]:
    calls: list[Any] = []

    def original_effective_guest_policy(entity: Any) -> str:
        calls.append(entity)
        return "effective-original"

    monkeypatch.setattr(
        conversation.ExtendedOpenAIAgentEntity,
        "_effective_guest_policy",
        original_effective_guest_policy,
    )
    monkeypatch.setattr(guest_performance, "_INSTALLED", False)
    monkeypatch.setattr(
        request_static_cache, "install_request_static_caching", lambda: None
    )
    monkeypatch.setattr(
        management_loading_performance,
        "install_management_loading_optimizations",
        lambda: None,
    )

    guest_performance.install_guest_policy_fast_path()
    assert guest_performance._INSTALLED is True
    return calls


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


def test_installed_fast_path_reuses_active_request_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_with_controlled_original(monkeypatch)
    request_policy = SimpleNamespace(guest_active=False)
    entity = SimpleNamespace(_guest_mode=SimpleNamespace(is_active=lambda: False))
    token = conversation._ACTIVE_GUEST_POLICY.set(request_policy)
    try:
        assert (
            conversation.ExtendedOpenAIAgentEntity._effective_guest_policy(entity)
            is request_policy
        )
    finally:
        conversation._ACTIVE_GUEST_POLICY.reset(token)

    assert calls == []


def test_installed_fast_path_delegates_when_guest_mode_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_with_controlled_original(monkeypatch)
    request_policy = SimpleNamespace(guest_active=False)
    entity = SimpleNamespace(_guest_mode=SimpleNamespace(is_active=lambda: True))
    token = conversation._ACTIVE_GUEST_POLICY.set(request_policy)
    try:
        assert (
            conversation.ExtendedOpenAIAgentEntity._effective_guest_policy(entity)
            == "effective-original"
        )
    finally:
        conversation._ACTIVE_GUEST_POLICY.reset(token)

    assert calls == [entity]


def test_installed_fast_path_delegates_without_request_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_with_controlled_original(monkeypatch)
    entity = SimpleNamespace(_guest_mode=None)

    assert (
        conversation.ExtendedOpenAIAgentEntity._effective_guest_policy(entity)
        == "effective-original"
    )
    assert calls == [entity]


def test_install_guest_policy_fast_path_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_with_controlled_original(monkeypatch)
    wrapper = conversation.ExtendedOpenAIAgentEntity._effective_guest_policy

    guest_performance.install_guest_policy_fast_path()

    assert conversation.ExtendedOpenAIAgentEntity._effective_guest_policy is wrapper
