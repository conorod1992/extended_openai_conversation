"""Tests for shared memory/archive identity resolution."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_VOICE_DEFAULT_USER_ID,
    CONF_VOICE_DEVICE_MAPPINGS,
    CONF_VOICE_SCOPE_POLICY,
    CONF_VOICE_UNMAPPED_POLICY,
    VOICE_POLICY_DEFAULT_USER,
    VOICE_POLICY_DEVICE_MAPPING,
    VOICE_POLICY_SHARED,
)
from custom_components.extended_openai_conversation_responses.scope import (
    LEGACY_ANONYMOUS_SCOPE_ID,
    SHARED_HOUSEHOLD_SCOPE_ID,
    UNRETAINED_SCOPE_ID,
    ResolvedDataScope,
    bind_active_voice_identity_users,
    legacy_anonymous_scope,
    memory_scope_id,
    resolve_data_scope,
)


def _context(user_id=None, device_id=None):
    return SimpleNamespace(
        context=SimpleNamespace(user_id=user_id), device_id=device_id
    )


def test_authenticated_user_has_priority_over_device_mapping() -> None:
    scope = resolve_data_scope(
        _context("alice", "kitchen"),
        {
            CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
            CONF_VOICE_DEVICE_MAPPINGS: {"kitchen": "user:bob"},
        },
    )
    assert scope.scope_id == "user:alice"
    assert scope.source == "authenticated_user"
    assert memory_scope_id(scope) == "alice"


def test_device_mapping_and_shared_mapping_are_explicit() -> None:
    personal = resolve_data_scope(
        _context(device_id="kitchen"),
        {
            CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
            CONF_VOICE_DEVICE_MAPPINGS: {"kitchen": "user:alice"},
        },
    )
    shared = resolve_data_scope(
        _context(device_id="hall"),
        {
            CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
            CONF_VOICE_DEVICE_MAPPINGS: {"hall": "shared"},
        },
    )
    assert (personal.user_id, personal.source) == ("alice", "device_mapping")
    assert shared.scope_id == SHARED_HOUSEHOLD_SCOPE_ID
    assert memory_scope_id(shared) == SHARED_HOUSEHOLD_SCOPE_ID


def test_default_owner_shared_and_unretained_fallbacks() -> None:
    default = resolve_data_scope(
        _context(device_id="bedroom"),
        {
            CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEFAULT_USER,
            CONF_VOICE_DEFAULT_USER_ID: "alice",
        },
    )
    shared = resolve_data_scope(
        _context(device_id="hall"),
        {CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_SHARED},
    )
    unretained = resolve_data_scope(
        _context(device_id="unmapped"),
        {
            CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
            CONF_VOICE_DEVICE_MAPPINGS: {},
            CONF_VOICE_UNMAPPED_POLICY: "unretained",
        },
    )
    assert (default.user_id, default.source) == ("alice", "agent_default_user")
    assert shared.scope_type == "shared"
    assert unretained.scope_type == "unretained"
    assert memory_scope_id(unretained) is None


def test_stale_device_mapping_follows_shared_unmapped_policy() -> None:
    with bind_active_voice_identity_users(frozenset()):
        scope = resolve_data_scope(
            _context(device_id="kitchen"),
            {
                CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
                CONF_VOICE_DEVICE_MAPPINGS: {"kitchen": "user:deleted-user"},
                CONF_VOICE_UNMAPPED_POLICY: VOICE_POLICY_SHARED,
            },
        )

    assert scope.scope_id == SHARED_HOUSEHOLD_SCOPE_ID
    assert scope.source == "shared_voice_policy"


def test_stale_device_mapping_can_fall_back_to_active_default_user() -> None:
    with bind_active_voice_identity_users(frozenset({"default-user"})):
        scope = resolve_data_scope(
            _context(device_id="kitchen"),
            {
                CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
                CONF_VOICE_DEVICE_MAPPINGS: {"kitchen": "user:deleted-user"},
                CONF_VOICE_UNMAPPED_POLICY: VOICE_POLICY_DEFAULT_USER,
                CONF_VOICE_DEFAULT_USER_ID: "default-user",
            },
        )

    assert (scope.user_id, scope.source) == ("default-user", "agent_default_user")


def test_stale_default_user_fails_closed_to_unretained() -> None:
    with bind_active_voice_identity_users(frozenset()):
        scope = resolve_data_scope(
            _context(device_id="bedroom"),
            {
                CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEFAULT_USER,
                CONF_VOICE_DEFAULT_USER_ID: "deleted-user",
            },
        )

    assert scope.scope_type == "unretained"
    assert memory_scope_id(scope) is None


def test_authenticated_user_remains_authoritative_when_configured_users_are_stale() -> None:
    with bind_active_voice_identity_users(frozenset()):
        scope = resolve_data_scope(
            _context("authenticated-user", "kitchen"),
            {
                CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
                CONF_VOICE_DEVICE_MAPPINGS: {"kitchen": "user:deleted-user"},
            },
        )

    assert (scope.user_id, scope.source) == (
        "authenticated-user",
        "authenticated_user",
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
