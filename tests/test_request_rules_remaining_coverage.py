"""Focused coverage for meaningful residual Request Rules validation branches."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.const import DOMAIN, SERVICE_CALL_FUNCTION
from custom_components.extended_openai_conversation_responses.request_rules import (
    GuestCapabilityPolicy,
    MAX_SCRIPT_DEPTH,
    _guest_script_allowed,
    _iter_script_actions,
    _legacy_action_slots,
    _mask_script_templates,
    _migrate_slot_templates,
    _validate_local_action,
    _validate_script_sequence,
    _validate_total_pattern_states,
    rule_has_sensitive_actions,
)


def test_legacy_function_action_rejects_unknown_fields_and_invalid_sources() -> None:
    """Legacy configured-function migration fails closed on ambiguous input."""
    with pytest.raises(ValueError, match="unknown function action fields"):
        _validate_local_action(
            {
                "type": "function",
                "function": "weather",
                "arguments": {},
                "unexpected": True,
            }
        )

    with pytest.raises(ValueError, match="source must be fixed or slot"):
        _validate_local_action(
            {
                "type": "function",
                "function": "weather",
                "arguments": {"place": {"source": "dynamic", "value": "home"}},
            }
        )


def test_legacy_slot_migration_recurses_and_slot_discovery_ignores_native_actions() -> None:
    """Legacy slot syntax is migrated recursively without misclassifying native actions."""
    assert _migrate_slot_templates(
        {
            "text": "Weather in {place}",
            "nested": [{"value_from": "slot", "slot": "room"}],
        }
    ) == {
        "text": "Weather in {{ place }}",
        "nested": ["{{ room }}"],
    }

    assert _legacy_action_slots(
        {
            "actions": [
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}},
                {
                    "type": "function",
                    "function": "weather",
                    "arguments": {"place": {"source": "slot", "slot": "place"}},
                },
                "not-an-action",
            ]
        }
    ) == {"place"}
    assert _legacy_action_slots({"actions": "not-a-sequence"}) == set()


def test_script_template_masking_preserves_shape_and_masks_action_templates() -> None:
    """Schema preflight masks templates recursively, including templated service names."""
    assert _mask_script_templates(
        {
            "action": "{{ service_name }}",
            "data": {"message": "Hello {{ person }}"},
            "items": ["{% if ok %}yes{% endif %}", 3],
        }
    ) == {
        "action": "homeassistant.update_entity",
        "data": {"message": "request_rule_template"},
        "items": ["request_rule_template", 3],
    }


def test_script_iterator_skips_malformed_nested_shapes_and_bounds_depth() -> None:
    """Malformed optional branches are ignored while excessive valid nesting is rejected."""
    root = {
        "action": "light.turn_on",
        "sequence": "not-a-sequence",
        "choose": ["bad-branch", {"sequence": "bad-sequence"}],
        "repeat": {"sequence": "bad-sequence"},
    }
    assert list(_iter_script_actions([root])) == [root]

    nested = [{"action": "light.turn_on"}]
    for _ in range(MAX_SCRIPT_DEPTH + 1):
        nested = [{"sequence": nested}]
    with pytest.raises(ValueError, match="maximum depth"):
        list(_iter_script_actions(nested))


def test_sensitive_action_detection_handles_nonlocal_and_cover_controls() -> None:
    """Sensitivity detection distinguishes routing rules from security-relevant cover control."""
    assert not rule_has_sensitive_actions(
        {"action_type": "route_to_ai", "action": {"actions": [{"action": "lock.unlock"}]}}
    )
    assert rule_has_sensitive_actions(
        {
            "action_type": "local_action",
            "action": {"actions": [{"action": "cover.open_cover"}]},
        }
    )
    assert not rule_has_sensitive_actions(
        {
            "action_type": "local_action",
            "action": {"actions": [{"action": "cover.stop_cover"}]},
        }
    )


def test_guest_configured_function_preflight_rejects_missing_or_disallowed_tool() -> None:
    """Guest preflight rejects configured-function actions unless the named tool is allowed."""
    hass = SimpleNamespace()
    policy = GuestCapabilityPolicy(
        guest_active=True,
        allowed_configured_tools=frozenset({"safe_tool"}),
    )
    service = f"{DOMAIN}.{SERVICE_CALL_FUNCTION}"

    assert not _guest_script_allowed(
        hass,
        [{"action": service, "data": {"function": 42}}],
        policy,
    )
    assert not _guest_script_allowed(
        hass,
        [{"action": service, "data": {"function": "blocked_tool"}}],
        policy,
    )


def test_total_pattern_state_validation_ignores_disabled_and_invalid_inactive_rules() -> None:
    """Repairable inactive sentence rules do not poison aggregate-state validation."""
    disabled = {
        "id": "disabled",
        "name": "Disabled",
        "order": 0,
        "enabled": False,
        "match_type": "sentence_pattern",
        "phrases": "not-a-list",
    }
    inactive = {
        "id": "repair-me",
        "name": "Repair me",
        "order": 1,
        "enabled": True,
        "match_type": "sentence_pattern",
        "phrases": ["turn on {room}", "turn on {device}"],
    }

    _validate_total_pattern_states([disabled, inactive], inactive_rule_ids={"repair-me"})


def test_invalid_native_script_sequence_is_reported_as_value_error() -> None:
    """Home Assistant schema failures are normalized at the Request Rules boundary."""
    with pytest.raises(ValueError, match="invalid Home Assistant action sequence"):
        _validate_script_sequence([{"action": 12345}])
