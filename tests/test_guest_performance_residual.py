"""Focused residual coverage for the guest request-policy fast path."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    guest_performance,
    request as request_module,
)
from custom_components.extended_openai_conversation_responses.request import (
    ExtendedOpenAIAgentEntity,
)


def _install_with_controlled_originals(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[Any], list[Any]]:
    effective_calls: list[Any] = []
    resolve_calls: list[Any] = []

    def original_effective(entity: Any) -> str:
        effective_calls.append(entity)
        return "effective-original"

    def original_resolve(entity: Any) -> str:
        resolve_calls.append(entity)
        return "resolve-original"

    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity,
        "_effective_request_policy",
        original_effective,
    )
    monkeypatch.setattr(request_module, "_resolve_request_policy", original_resolve)
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity,
        guest_performance._PATCHED,
        False,
        raising=False,
    )

    assert guest_performance.install_guest_policy_fast_path() is True
    return effective_calls, resolve_calls


def test_eligible_non_owner_guest_reuses_stable_owner_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effective_calls, resolve_calls = _install_with_controlled_originals(monkeypatch)
    owner_policy = object()
    entity = SimpleNamespace(
        _request_policy=owner_policy,
        _compatibility_restricted_features=None,
    )
    monkeypatch.setattr(
        guest_performance,
        "current_request_context",
        lambda: SimpleNamespace(is_guest=True, is_entry_owner=False),
    )

    assert ExtendedOpenAIAgentEntity._effective_request_policy(entity) is owner_policy
    assert request_module._resolve_request_policy(entity) is owner_policy
    assert effective_calls == []
    assert resolve_calls == []


def test_compatibility_restrictions_disable_guest_fast_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effective_calls, resolve_calls = _install_with_controlled_originals(monkeypatch)
    entity = SimpleNamespace(
        _request_policy=object(),
        _compatibility_restricted_features={"reasoning"},
    )
    monkeypatch.setattr(
        guest_performance,
        "current_request_context",
        lambda: SimpleNamespace(is_guest=True, is_entry_owner=False),
    )

    assert ExtendedOpenAIAgentEntity._effective_request_policy(entity) == "effective-original"
    assert request_module._resolve_request_policy(entity) == "resolve-original"
    assert effective_calls == [entity]
    assert resolve_calls == [entity]


@pytest.mark.parametrize(
    "context",
    [
        None,
        SimpleNamespace(is_guest=False, is_entry_owner=False),
        SimpleNamespace(is_guest=True, is_entry_owner=True),
    ],
)
def test_ineligible_contexts_delegate_to_original_policy_resolution(
    monkeypatch: pytest.MonkeyPatch,
    context: Any,
) -> None:
    effective_calls, resolve_calls = _install_with_controlled_originals(monkeypatch)
    entity = SimpleNamespace(
        _request_policy=object(),
        _compatibility_restricted_features=None,
    )
    monkeypatch.setattr(
        guest_performance,
        "current_request_context",
        lambda: context,
    )

    assert ExtendedOpenAIAgentEntity._effective_request_policy(entity) == "effective-original"
    assert request_module._resolve_request_policy(entity) == "resolve-original"
    assert effective_calls == [entity]
    assert resolve_calls == [entity]


def test_install_guest_policy_fast_path_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_with_controlled_originals(monkeypatch)
    effective_wrapper = ExtendedOpenAIAgentEntity._effective_request_policy
    resolve_wrapper = request_module._resolve_request_policy

    assert guest_performance.install_guest_policy_fast_path() is False
    assert ExtendedOpenAIAgentEntity._effective_request_policy is effective_wrapper
    assert request_module._resolve_request_policy is resolve_wrapper
