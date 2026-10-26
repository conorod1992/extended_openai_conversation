"""Independent script-shape and budget contracts for the inspection extraction."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import request_rules as rr
from custom_components.extended_openai_conversation_responses.functions.script import (
    _reject_device_actions,
)
from homeassistant.exceptions import HomeAssistantError

SHAPES = [
    pytest.param(lambda leaf: {"sequence": leaf}, ["root", "leaf"], id="sequence"),
    pytest.param(lambda leaf: {"then": leaf}, ["root", "leaf"], id="then"),
    pytest.param(lambda leaf: {"else": leaf}, ["root", "leaf"], id="else"),
    pytest.param(lambda leaf: {"default": leaf}, ["root", "leaf"], id="default"),
    pytest.param(
        lambda leaf: {"choose": {"conditions": [], "sequence": leaf}},
        ["root", "leaf"],
        id="choose-mapping",
    ),
    pytest.param(
        lambda leaf: {"choose": [{"conditions": [], "sequence": leaf}]},
        ["root", "leaf"],
        id="choose-list",
    ),
    pytest.param(
        lambda leaf: {"repeat": {"count": 2, "sequence": leaf}},
        ["root", "leaf"],
        id="repeat",
    ),
    pytest.param(
        lambda leaf: {"parallel": leaf}, ["root", None, "leaf"], id="parallel-mapping"
    ),
    pytest.param(
        lambda leaf: {"parallel": [leaf]}, ["root", None, "leaf"], id="parallel-list"
    ),
    pytest.param(
        lambda leaf: {"parallel": [[leaf]]},
        ["root", None, "leaf"],
        id="parallel-list-of-sequences",
    ),
    pytest.param(
        lambda leaf: {"parallel": [{"alias": "branch", "sequence": leaf}]},
        ["root", "branch", "leaf"],
        id="parallel-explicit-branch",
    ),
]


@pytest.mark.parametrize("wrap, expected", SHAPES)
def test_normalized_shapes_have_independently_specified_inspection_order(
    wrap, expected
):
    leaf = {
        "alias": "leaf",
        "service_template": "alarm_control_panel.alarm_disarm",
        "data": {"sequence": {"device_id": "payload"}, "message": "{{ message }}"},
    }
    raw = [{"alias": "root", **wrap(leaf)}]
    original = deepcopy(raw)
    normalized = rr._normalize_script_containers(raw)
    actions = list(rr._iter_script_actions(normalized))
    assert [action.get("alias") for action in actions] == expected
    assert actions[-1]["service"] == "alarm_control_panel.alarm_disarm"
    assert actions[-1]["data"] == leaf["data"]
    assert rr.rule_has_sensitive_actions(
        {"action_type": "local_action", "action": {"actions": normalized}}
    )
    _reject_device_actions(normalized)
    assert raw == original
    normalized[0]["alias"] = "changed"
    assert raw == original


@pytest.mark.parametrize("wrap, _expected", SHAPES)
def test_device_check_rejects_nested_actions_in_ha_normalized_shapes(wrap, _expected):
    raw = [
        {
            "alias": "root",
            **wrap({"device_id": "forbidden", "domain": "light", "type": "turn_on"}),
        }
    ]
    with pytest.raises(HomeAssistantError, match="do not support device actions"):
        _reject_device_actions(rr._normalize_script_containers(raw))


def test_payload_and_condition_lookalikes_are_not_script_children():
    lookalike = {
        "sequence": [{"device_id": "payload"}],
        "choose": [{"sequence": [{"action": "lock.unlock"}]}],
        "parallel": [{"action": "lock.unlock"}],
    }
    actions = [
        {"action": "notify.send_message", "data": lookalike, "target": lookalike},
        {"variables": {"saved": lookalike}},
        {"condition": "and", "conditions": [lookalike]},
        {"choose": [{"conditions": [lookalike], "sequence": [{"delay": 0}]}]},
        {"repeat": {"while": [lookalike], "sequence": [{"delay": 0}]}},
    ]
    normalized = rr._normalize_script_containers(actions)
    inspected = list(rr._iter_script_actions(normalized))
    assert len(inspected) == 7
    assert not rr.rule_has_sensitive_actions(
        {"action_type": "local_action", "action": {"actions": normalized}}
    )
    _reject_device_actions(normalized)
    assert normalized[0]["data"] == lookalike
    assert normalized[3]["choose"][0]["conditions"] == [lookalike]


def test_action_budget_boundary_and_shared_counter():
    assert (
        len(list(rr._iter_script_actions([{}] * rr.MAX_SCRIPT_NODES)))
        == rr.MAX_SCRIPT_NODES
    )
    with pytest.raises(ValueError, match=f"exceeds {rr.MAX_SCRIPT_NODES} nodes"):
        list(rr._iter_script_actions([{}] * (rr.MAX_SCRIPT_NODES + 1)))
    budget = [rr.MAX_SCRIPT_NODES - 1]
    assert list(rr._iter_script_actions([{}], budget=budget)) == [{}]
    assert budget == [rr.MAX_SCRIPT_NODES]
    with pytest.raises(ValueError, match="nodes"):
        list(rr._iter_script_actions([{}], budget=budget))
    assert budget == [rr.MAX_SCRIPT_NODES + 1]


def test_depth_boundary_and_container_accounting():
    nested = [{"delay": 0}]
    for _ in range(rr.MAX_SCRIPT_DEPTH):
        nested = [{"sequence": nested}]
    assert len(list(rr._iter_script_actions(nested))) == rr.MAX_SCRIPT_DEPTH + 1
    with pytest.raises(ValueError, match="maximum depth"):
        list(rr._iter_script_actions([{"sequence": nested}]))
    assert list(rr._iter_script_actions([], depth=rr.MAX_SCRIPT_DEPTH)) == []
    with pytest.raises(ValueError, match="maximum depth"):
        list(rr._iter_script_actions([], depth=rr.MAX_SCRIPT_DEPTH + 1))
    # Parallel wrappers count as nodes; choose/repeat metadata mappings do not.
    assert (
        len(
            list(
                rr._iter_script_actions(
                    [
                        {
                            "parallel": [{"sequence": [{}]}],
                            "choose": [{"sequence": [{}]}],
                            "repeat": {"sequence": [{}]},
                        }
                    ]
                )
            )
        )
        == 5
    )


def test_payload_complexity_is_distinct_from_executable_action_count():
    root = {
        "action": "notify.send_message",
        "data": {"values": [None] * rr.MAX_SCRIPT_NODES},
    }
    assert list(rr._iter_script_actions([root])) == [root]
    with pytest.raises(ValueError, match="nodes"):
        rr._validate_script_complexity([root])
    assert (
        rr._validate_script_complexity([None] * (rr.MAX_SCRIPT_NODES - 1))
        == rr.MAX_SCRIPT_NODES
    )
    payload = None
    for _ in range(rr.MAX_SCRIPT_DEPTH + 1):
        payload = {"value": payload}
    root["data"] = payload
    assert list(rr._iter_script_actions([root])) == [root]
    with pytest.raises(ValueError, match="maximum depth"):
        rr._validate_script_complexity([root])


def test_canonical_inspection_skips_mixed_children_but_device_preflight_filters_them():
    root = {"sequence": ["ignored", {"device_id": "blocked"}]}
    assert list(rr._iter_script_actions([root])) == [root]
    with pytest.raises(HomeAssistantError, match="device actions"):
        _reject_device_actions([root])
    _reject_device_actions({"condition": "device", "device_id": "condition"})
    _reject_device_actions([{}] * (rr.MAX_SCRIPT_NODES + 1))
    nested = {"device_id": "blocked"}
    for _ in range(rr.MAX_SCRIPT_DEPTH + 1):
        nested = {"sequence": nested}
    with pytest.raises(HomeAssistantError, match="device actions"):
        _reject_device_actions(nested)


@pytest.mark.parametrize("outer, outer_order", SHAPES)
@pytest.mark.parametrize("inner, inner_order", SHAPES)
def test_nested_shapes_agree_across_function_inspection_consumers(
    outer, outer_order, inner, inner_order
):
    from custom_components.extended_openai_conversation_responses.function_dependency_integrity import (
        recursive_function_references,
    )
    from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
        request_rule_match_preview,
    )

    leaf = {
        "alias": "leaf",
        "action": "extended_openai_conversation_responses.call_function",
        "data": {
            "function": "lookup",
            "arguments": {},
            "sequence": [{"device_id": "payload"}],
        },
    }
    middle = {"alias": "middle", **inner(leaf)}
    outer_action = outer(middle)
    raw = [{"alias": "root", **outer_action}]
    canonical = rr._normalize_script_containers(raw)
    prefix = outer_order[:-1]
    parallel = outer_action.get("parallel")
    if (
        outer_order == ["root", None, "leaf"]
        and "sequence" in middle
        and (
            isinstance(parallel, dict)
            or (isinstance(parallel, list) and isinstance(parallel[0], dict))
        )
    ):
        # A sequence mapping is already a parallel branch, so no wrapper is added.
        prefix = ["root"]
    assert [node.get("alias") for node in rr._iter_script_actions(canonical)] == [
        *prefix,
        "middle",
        *inner_order[1:],
    ]
    rule = {
        "id": "nested",
        "name": "Nested",
        "action_type": "local_action",
        "match_type": "equals",
        "action": {"actions": canonical},
    }
    assert recursive_function_references(SimpleNamespace(_rules=[rule]), "lookup") == [
        {"id": "nested", "name": "Nested"}
    ]
    assert (
        recursive_function_references(SimpleNamespace(_rules=[rule]), "payload") == []
    )
    preview = request_rule_match_preview(rr.RuleMatch(rule, "run", False, 100.0))
    assert preview["would_do"]["functions"] == [
        {"name": "lookup", "result_alias": None}
    ]
    _reject_device_actions(canonical)
    nested_leaf = list(rr._iter_script_actions(canonical))[-1]
    nested_leaf["data"]["result_alias"] = "answer"
    with pytest.raises(ValueError, match="captured only by top-level steps"):
        rr._validate_result_dependencies({"actions": canonical}, set())
    nested_leaf["action"] = "lock.unlock"
    assert rr.rule_has_sensitive_actions(rule)


def test_explicit_shared_inspection_limits_do_not_change_global_defaults():
    from custom_components.extended_openai_conversation_responses.script_structure import (
        iter_script_actions,
    )

    assert list(iter_script_actions([{}], max_nodes=1, max_depth=0)) == [{}]
    with pytest.raises(ValueError, match="exceeds 1 nodes"):
        list(iter_script_actions([{}, {}], max_nodes=1))
    with pytest.raises(ValueError, match="maximum depth 0"):
        list(iter_script_actions([{"sequence": [{}]}], max_depth=0))
    assert len(list(iter_script_actions([{}, {}]))) == 2


def test_device_preflight_retains_its_repeat_before_choose_failure_order():
    # This malformed input must still fail at repeat before inspecting choose.
    with pytest.raises(AttributeError):
        _reject_device_actions({"repeat": None, "choose": [{"device_id": "blocked"}]})


def test_request_rule_consumers_forward_the_same_existing_limits(monkeypatch):
    from custom_components.extended_openai_conversation_responses.function_dependency_integrity import (
        recursive_function_references,
    )
    from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
        request_rule_match_preview,
    )

    rule = {
        "id": "limited",
        "name": "Limited",
        "action_type": "local_action",
        "match_type": "equals",
        "action": {"actions": [{"delay": 0}]},
    }
    monkeypatch.setattr(rr, "MAX_SCRIPT_NODES", 0)
    with pytest.raises(ValueError, match="exceeds 0 nodes"):
        list(rr._iter_script_actions(rule["action"]["actions"]))
    with pytest.raises(ValueError, match="exceeds 0 nodes"):
        recursive_function_references(SimpleNamespace(_rules=[rule]), "lookup")
    with pytest.raises(ValueError, match="exceeds 0 nodes"):
        request_rule_match_preview(rr.RuleMatch(rule, "run", False, 100.0))
    _reject_device_actions([{}])  # Device preflight still has no Request Rule quota.


@pytest.mark.parametrize("form", ["list", "and", "or", "not"])
def test_selector_walk_preserves_native_condition_shorthand_and_payload_boundaries(
    form,
):
    state = {"condition": "state", "entity_id": "{device}", "state": "on"}
    guard = {"condition": [state]} if form == "list" else {form: [state]}
    payload = {
        "scene": "{{ selected_scene }}",
        "condition": [{"entity_id": "{{ arbitrary_payload }}"}],
    }
    sequence = [{"if": [guard], "then": [{"event": "selected", "event_data": payload}]}]
    prepared = rr._prepare_concrete_captures(sequence, {"device": "light.kitchen"})
    prepared_guard = prepared[0]["if"][0]
    nested = (
        prepared_guard["condition"][0] if form == "list" else prepared_guard[form][0]
    )
    assert nested["entity_id"] == "light.kitchen"
    assert prepared[0]["then"][0]["event_data"] == payload
    assert sequence[0]["if"][0] == guard
    rr._validate_script_sequence(sequence, capture_names={"device"})
