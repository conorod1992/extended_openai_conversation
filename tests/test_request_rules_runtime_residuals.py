"""Targeted Request Rules residual coverage for runtime and validation boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.extended_openai_conversation_responses import request_rules as rr
from homeassistant.exceptions import HomeAssistantError


def test_total_pattern_state_validation_handles_inactive_and_mismatched_rules() -> None:
    """Inactive broken patterns are repairable while active broken patterns fail."""
    invalid = {
        "id": "broken",
        "name": "Broken",
        "enabled": True,
        "order": 0,
        "match_type": "sentence_pattern",
        "phrases": ["first", "second"],
    }
    first = SimpleNamespace(capture_names={"room"}, state_count=1)
    second = SimpleNamespace(capture_names={"device"}, state_count=1)

    with patch.object(rr, "compile_sentence_pattern", side_effect=[first, second]):
        rr._validate_total_pattern_states([invalid], inactive_rule_ids={"broken"})

    with (
        patch.object(rr, "compile_sentence_pattern", side_effect=[first, second]),
        pytest.raises(ValueError, match="same slots"),
    ):
        rr._validate_total_pattern_states([invalid])

    # Disabled and malformed phrase containers are deliberately ignored here;
    # rule-level validation owns their shape errors.
    rr._validate_total_pattern_states(
        [
            {**invalid, "id": "disabled", "enabled": False},
            {**invalid, "id": "not-pattern", "match_type": "equals"},
            {**invalid, "id": "bad-phrases", "phrases": "not-a-sequence"},
        ]
    )


def test_legacy_action_migration_covers_unknown_source_and_home_assistant_type() -> None:
    """Legacy actions reject unknown bindings and strip the HA type wrapper."""
    with pytest.raises(ValueError, match="unknown function action fields"):
        rr._validate_local_action(
            {
                "type": "function",
                "function": "demo",
                "arguments": {},
                "unexpected": True,
            }
        )

    with pytest.raises(ValueError, match="source must be fixed or slot"):
        rr._validate_local_action(
            {
                "type": "function",
                "function": "demo",
                "arguments": {"value": {"source": "dynamic", "value": 1}},
            }
        )

    assert rr._validate_local_action(
        {
            "type": "home_assistant",
            "domain": "light",
            "service": "turn_on",
            "target": {"entity_id": "light.kitchen"},
            "data": {},
        }
    ) == {
        "action": "light.turn_on",
        "target": {"entity_id": "light.kitchen"},
        "data": {},
    }


def test_legacy_slot_migration_recurses_and_slot_discovery_ignores_native_actions() -> None:
    """Legacy slot syntax is migrated recursively without misclassifying native actions."""
    assert rr._migrate_slot_templates(
        {
            "text": "Weather in {place}",
            "nested": [{"value_from": "slot", "slot": "room"}],
        }
    ) == {
        "text": "Weather in {{ place }}",
        "nested": ["{{ room }}"],
    }

    assert rr._legacy_action_slots(
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
    assert rr._legacy_action_slots({"actions": "not-a-sequence"}) == set()


def test_script_template_masking_preserves_shape_and_masks_action_templates() -> None:
    """Schema-only masking replaces templates without mutating their container shape."""
    value = {
        "action": "{{ request.slots.action }}",
        "data": {
            "message": "Hello {{ request.slots.name }}",
            "items": ["plain", "{% if true %}templated{% endif %}"],
        },
    }

    masked = rr._mask_script_templates(value)

    assert masked == {
        "action": "homeassistant.update_entity",
        "data": {
            "message": "request_rule_template",
            "items": ["plain", "request_rule_template"],
        },
    }
    # The stored input remains untouched; masking is only for context-free schema checks.
    assert value["action"] == "{{ request.slots.action }}"


def test_script_iterator_skips_malformed_nested_shapes_and_bounds_depth() -> None:
    """Malformed optional branches are ignored while excessive valid nesting is rejected."""
    root = {
        "action": "light.turn_on",
        "sequence": "not-a-sequence",
        "choose": ["bad-branch", {"sequence": "bad-sequence"}],
        "repeat": {"sequence": "bad-sequence"},
    }
    assert list(rr._iter_script_actions([root])) == [root]

    nested = [{"action": "light.turn_on"}]
    for _ in range(rr.MAX_SCRIPT_DEPTH + 1):
        nested = [{"sequence": nested}]
    with pytest.raises(ValueError, match="maximum depth"):
        list(rr._iter_script_actions(nested))


def test_sensitive_action_detection_handles_nonlocal_and_cover_controls() -> None:
    """Sensitivity detection distinguishes routing rules from security-relevant cover control."""
    assert not rr.rule_has_sensitive_actions(
        {"action_type": "route_to_ai", "action": {"actions": [{"action": "lock.unlock"}]}}
    )
    assert rr.rule_has_sensitive_actions(
        {
            "action_type": "local_action",
            "action": {"actions": [{"action": "cover.open_cover"}]},
        }
    )
    assert not rr.rule_has_sensitive_actions(
        {
            "action_type": "local_action",
            "action": {"actions": [{"action": "cover.stop_cover"}]},
        }
    )


def test_guest_preflight_accepts_allowed_configured_tool() -> None:
    """The positive configured-tool path is authorized without HA service dispatch."""
    policy = SimpleNamespace(allows_configured_tool=lambda name: name == "weather")
    assert rr._guest_script_allowed(
        object(),
        [
            {
                "action": f"{rr.DOMAIN}.{rr.SERVICE_CALL_FUNCTION}",
                "data": {"function": "weather", "arguments": {}},
            }
        ],
        policy,
    )


def test_guest_preflight_rejects_missing_or_disallowed_configured_tool() -> None:
    """Guest preflight rejects configured-function actions unless the named tool is allowed."""
    policy = rr.GuestCapabilityPolicy(
        guest_active=True,
        configured_tool_names=frozenset({"safe_tool"}),
    )
    service = f"{rr.DOMAIN}.{rr.SERVICE_CALL_FUNCTION}"

    assert not rr._guest_script_allowed(
        SimpleNamespace(),
        [{"action": service, "data": {"function": 42}}],
        policy,
    )
    assert not rr._guest_script_allowed(
        SimpleNamespace(),
        [{"action": service, "data": {"function": "blocked_tool"}}],
        policy,
    )


def test_invalid_native_script_sequence_is_reported_as_value_error() -> None:
    """Home Assistant schema failures are normalized at the Request Rules boundary."""
    with pytest.raises(ValueError, match="invalid Home Assistant action sequence"):
        rr._validate_script_sequence([{"action": 12345}])


@pytest.mark.asyncio
async def test_local_action_failure_unloads_script_and_clears_active_executor() -> None:
    """A failing HA script cannot leak its function executor into later requests."""
    rule = {
        "id": "runtime-cleanup",
        "action_type": "local_action",
        "action": {
            "actions": [{"action": "light.turn_on"}],
            "success_response": "Done",
            "failure_response": "Failed {room}",
        },
    }
    match = rr.RuleMatch(rule, "run", False, 1.0, {"room": "kitchen"})
    rules = SimpleNamespace(async_match=AsyncMock(return_value=match))
    runtime = rr.RequestRuleRuntime()
    executor = AsyncMock(return_value={"ok": True})

    script = Mock()
    script.async_run = AsyncMock(side_effect=RuntimeError("boom"))
    script.async_unload = AsyncMock()

    with (
        patch.object(rr.cv, "SCRIPT_SCHEMA", side_effect=lambda actions: actions),
        patch.object(
            rr,
            "async_validate_actions_config",
            new=AsyncMock(side_effect=lambda _hass, actions: actions),
        ),
        patch.object(rr, "Script", return_value=script),
    ):
        result = await rr.async_evaluate_rule(
            Mock(),
            rules,
            runtime,
            "run",
            "session",
            function_executor=executor,
        )

    assert result is not None
    assert result.successful is False
    assert result.response == "Failed kitchen"
    script.async_run.assert_awaited_once()
    script.async_unload.assert_awaited_once_with()

    with pytest.raises(HomeAssistantError, match="only available"):
        await rr.async_call_active_function("should-not-leak", {})


@pytest.mark.asyncio
async def test_guest_authorization_exception_fails_closed_before_script_creation() -> None:
    """Unexpected Guest preflight errors deny execution rather than bypassing policy."""
    rule = {
        "id": "guest-preflight",
        "action_type": "local_action",
        "action": {
            "actions": [{"action": "light.turn_on"}],
            "success_response": "Done",
            "failure_response": "Failed",
        },
    }
    match = rr.RuleMatch(rule, "run", False, 1.0, {})
    rules = SimpleNamespace(async_match=AsyncMock(return_value=match))
    policy = SimpleNamespace(guest_active=True)

    with (
        patch.object(rr, "_resolve_guest_slot_templates", side_effect=RuntimeError("bad")),
        patch.object(rr, "Script") as script_cls,
    ):
        result = await rr.async_evaluate_rule(
            Mock(),
            rules,
            rr.RequestRuleRuntime(),
            "run",
            "session",
            guest_policy=policy,
        )

    assert result is not None
    assert result.successful is False
    assert result.response == rr.GUEST_MODE_UNAVAILABLE
    script_cls.assert_not_called()
