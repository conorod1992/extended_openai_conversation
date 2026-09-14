"""Focused residual coverage for data-scope helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_VOICE_DEVICE_MAPPINGS,
    CONF_VOICE_SCOPE_POLICY,
    CONF_VOICE_UNMAPPED_POLICY,
    VOICE_POLICY_DEVICE_MAPPING,
    VOICE_POLICY_SHARED,
)
from custom_components.extended_openai_conversation_responses.scope import (
    LEGACY_ANONYMOUS_SCOPE_ID,
    SHARED_HOUSEHOLD_SCOPE_ID,
    UNRETAINED_SCOPE_ID,
    ResolvedDataScope,
    legacy_anonymous_scope,
    memory_scope_id,
    resolve_data_scope,
)


def test_resolved_data_scope_as_dict_includes_all_fields() -> None:
    """Scope serialization preserves every stable field."""
    scope = ResolvedDataScope(
        scope_id="user:alice",
        scope_type="user",
        source="device_mapping",
        user_id="alice",
        device_id="device-1",
        display_name="Alice",
    )

    assert scope.as_dict() == {
        "scope_id": "user:alice",
        "scope_type": "user",
        "source": "device_mapping",
        "user_id": "alice",
        "device_id": "device-1",
        "display_name": "Alice",
    }


def test_legacy_anonymous_scope_uses_legacy_memory_owner() -> None:
    """Preserved anonymous data keeps its legacy persistent owner key."""
    scope = legacy_anonymous_scope()

    assert scope.scope_id == LEGACY_ANONYMOUS_SCOPE_ID
    assert memory_scope_id(scope) == LEGACY_ANONYMOUS_SCOPE_ID


@pytest.mark.parametrize("mapped_scope", ["unretained", UNRETAINED_SCOPE_ID])
def test_unretained_device_mapping_falls_back_to_unmapped_policy(
    mapped_scope: str,
) -> None:
    """Unretained mapping sentinels defer to the configured unmapped policy."""
    context = SimpleNamespace(context=None, device_id="device-1")
    options = {
        CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
        CONF_VOICE_DEVICE_MAPPINGS: {"device-1": mapped_scope},
        CONF_VOICE_UNMAPPED_POLICY: VOICE_POLICY_SHARED,
    }

    scope = resolve_data_scope(context, options)

    assert scope.scope_id == SHARED_HOUSEHOLD_SCOPE_ID
    assert scope.scope_type == "shared"
    assert scope.source == "shared_voice_policy"
    assert scope.device_id == "device-1"
