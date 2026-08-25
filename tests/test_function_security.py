"""Adversarial classification tests for configured Guest capabilities."""

from custom_components.extended_openai_conversation_responses.functions.security import (
    FunctionSecurity,
    classify_function,
    classify_tool,
    contains_indirect_service_call,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
    guest_arguments_allowed_runtime,
)


def test_composite_recursively_inherits_unscopable_native() -> None:
    tool = {
        "function": {
            "type": "composite",
            "sequence": [
                {"type": "native", "name": "get_history"},
                {"type": "native", "name": "add_automation"},
            ],
        }
    }
    assert classify_tool(tool) == FunctionSecurity.UNSCOPABLE


def test_nested_composite_cannot_hide_unscopable_capability() -> None:
    function = {
        "type": "composite",
        "sequence": [
            {
                "type": "composite",
                "sequence": [{"type": "native", "name": "add_automation"}],
            }
        ],
    }
    assert classify_function(function) == FunctionSecurity.UNSCOPABLE


def test_read_only_and_control_composites_keep_strongest_classification() -> None:
    assert (
        classify_function(
            {
                "type": "composite",
                "sequence": [
                    {"type": "native", "name": "get_history"},
                    {"type": "native", "name": "get_statistics"},
                ],
            }
        )
        == FunctionSecurity.SAFE
    )
    assert (
        classify_function(
            {
                "type": "composite",
                "sequence": [
                    {"type": "native", "name": "get_history"},
                    {"type": "native", "name": "execute_service"},
                ],
            }
        )
        == FunctionSecurity.CONTROL
    )


def test_script_static_control_dynamic_and_indirect_classification() -> None:
    assert (
        classify_function(
            {
                "type": "script",
                "sequence": [
                    {
                        "service": "lock.unlock",
                        "target": {"entity_id": "lock.front_door"},
                    }
                ],
            }
        )
        == FunctionSecurity.CONTROL
    )
    assert (
        classify_function(
            {
                "type": "script",
                "sequence": [
                    {
                        "service": "{{ requested_service }}",
                        "target": {"entity_id": "lock.front_door"},
                    }
                ],
            }
        )
        == FunctionSecurity.UNSCOPABLE
    )
    assert (
        classify_function(
            {
                "type": "script",
                "sequence": [
                    {
                        "service": "script.turn_on",
                        "target": {"entity_id": "script.owner_routine"},
                    }
                ],
            }
        )
        == FunctionSecurity.INDIRECT
    )


def test_classification_is_cycle_and_depth_safe() -> None:
    cyclic = {"type": "composite", "sequence": []}
    cyclic["sequence"].append(cyclic)
    assert classify_function(cyclic) == FunctionSecurity.UNSCOPABLE


def test_static_script_target_is_revalidated_against_guest_policy(hass) -> None:
    policy = GuestCapabilityPolicy(
        True,
        readable_entity_ids=frozenset({"light.guest"}),
        controllable_entity_ids=frozenset({"light.guest"}),
    )
    script = {
        "type": "script",
        "sequence": [
            {
                "service": "lock.unlock",
                "target": {"entity_id": "lock.front_door"},
            }
        ],
    }
    assert not guest_arguments_allowed_runtime(hass, script, policy, control=True)


def test_generic_wrapper_arguments_are_explicitly_indirect() -> None:
    assert contains_indirect_service_call(
        {"list": [{"domain": "automation", "service": "trigger"}]}
    )
    assert contains_indirect_service_call({"service": "scene.turn_on"})
    assert not contains_indirect_service_call({"domain": "light", "service": "turn_on"})
