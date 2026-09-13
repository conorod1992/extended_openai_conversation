"""Residual coverage for conservative function security classification."""

from __future__ import annotations

from custom_components.extended_openai_conversation_responses.functions.security import (
    FunctionSecurity,
    classify_function,
    classify_tool,
    contains_indirect_service_call,
)


def test_classify_tool_requires_mapping_function() -> None:
    assert classify_tool({}) is FunctionSecurity.UNSCOPABLE
    assert classify_tool({"function": "native"}) is FunctionSecurity.UNSCOPABLE


def test_native_function_classification_covers_known_and_unknown_names() -> None:
    assert classify_function({"type": "native", "name": "execute_service"}) is FunctionSecurity.CONTROL
    assert classify_function({"type": "native", "name": "get_history"}) is FunctionSecurity.SAFE
    assert classify_function({"type": "native", "name": "unknown"}) is FunctionSecurity.UNSCOPABLE


def test_composite_requires_sequence_of_mappings_and_uses_highest_risk() -> None:
    assert classify_function({"type": "composite", "sequence": "bad"}) is FunctionSecurity.UNSCOPABLE
    assert classify_function({"type": "composite", "sequence": [None]}) is FunctionSecurity.UNSCOPABLE

    composite = {
        "type": "composite",
        "sequence": [
            {"type": "native", "name": "get_history"},
            {"type": "native", "name": "execute_service"},
        ],
    }
    assert classify_function(composite) is FunctionSecurity.CONTROL


def test_composite_cycle_and_depth_are_unscopable() -> None:
    cyclic: dict = {"type": "composite"}
    cyclic["sequence"] = [cyclic]
    assert classify_function(cyclic) is FunctionSecurity.UNSCOPABLE

    current: dict = {"type": "native", "name": "get_history"}
    for _ in range(13):
        current = {"type": "composite", "sequence": [current]}
    assert classify_function(current) is FunctionSecurity.UNSCOPABLE


def test_script_rejects_invalid_service_and_target_shapes() -> None:
    assert classify_function({"type": "script", "sequence": "bad"}) is FunctionSecurity.UNSCOPABLE
    assert classify_function({"type": "script", "sequence": [None]}) is FunctionSecurity.UNSCOPABLE
    assert classify_function(
        {"type": "script", "sequence": [{"service": 123, "target": {"entity_id": "light.a"}}]}
    ) is FunctionSecurity.UNSCOPABLE
    assert classify_function(
        {"type": "script", "sequence": [{"service": "{{ service }}", "target": {"entity_id": "light.a"}}]}
    ) is FunctionSecurity.UNSCOPABLE
    assert classify_function(
        {"type": "script", "sequence": [{"service": "light", "target": {"entity_id": "light.a"}}]}
    ) is FunctionSecurity.UNSCOPABLE
    assert classify_function(
        {"type": "script", "sequence": [{"service": "light.turn_on", "target": {}}]}
    ) is FunctionSecurity.UNSCOPABLE
    assert classify_function(
        {"type": "script", "sequence": [{"service": "light.turn_on", "target": {"entity_id": "{{ entity }}"}}]}
    ) is FunctionSecurity.UNSCOPABLE


def test_script_classifies_direct_and_indirect_static_actions() -> None:
    direct = {
        "type": "script",
        "sequence": [{"action": "light.turn_on", "target": {"entity_id": ["light.a", "light.b"]}}],
    }
    indirect = {
        "type": "script",
        "sequence": [{"service": "script.turn_on", "target": {"entity_id": "script.demo"}}],
    }
    assert classify_function(direct) is FunctionSecurity.CONTROL
    assert classify_function(indirect) is FunctionSecurity.INDIRECT


def test_static_target_rejects_unknown_empty_and_non_string_values() -> None:
    bad_targets = [
        {"entity_id": []},
        {"entity_id": ""},
        {"entity_id": 123},
        {"entity_id": "light.a", "unsupported": "x"},
    ]
    for target in bad_targets:
        assert classify_function(
            {"type": "script", "sequence": [{"service": "light.turn_on", "target": target}]}
        ) is FunctionSecurity.UNSCOPABLE


def test_unknown_function_types_are_unscopable() -> None:
    assert classify_function({"type": "template"}) is FunctionSecurity.UNSCOPABLE
    assert classify_function({}) is FunctionSecurity.UNSCOPABLE


def test_contains_indirect_service_call_detects_nested_domain_and_service() -> None:
    assert contains_indirect_service_call({"domain": "automation"}) is True
    assert contains_indirect_service_call({"service": "scene.turn_on"}) is True
    assert contains_indirect_service_call({"action": "script.turn_on"}) is True
    assert contains_indirect_service_call(
        {"outer": [{"safe": True}, {"nested": {"domain": "script"}}]}
    ) is True

    assert contains_indirect_service_call(
        {"domain": "light", "service": "light.turn_on", "nested": [1, "scene.turn_on"]}
    ) is False
    assert contains_indirect_service_call("automation.turn_on") is False
