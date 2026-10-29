"""Independent contracts for script transformer migration."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import request_rules as rr

CONTAINERS = [
    pytest.param(lambda leaf: {"sequence": [leaf]}, (0, "sequence"), id="sequence"),
    pytest.param(
        lambda leaf: {"if": "{{ true }}", "then": [leaf]}, (0, "then"), id="then"
    ),
    pytest.param(
        lambda leaf: {"if": "{{ false }}", "then": [], "else": [leaf]},
        (0, "else"),
        id="else",
    ),
    pytest.param(
        lambda leaf: {"choose": [], "default": [leaf]}, (0, "default"), id="default"
    ),
    pytest.param(
        lambda leaf: {"parallel": [{"alias": "branch", "sequence": [leaf]}]},
        (0, "parallel", 0, "sequence"),
        id="parallel",
    ),
    pytest.param(
        lambda leaf: {
            "choose": [
                {"alias": "branch", "conditions": "{{ true }}", "sequence": [leaf]}
            ]
        },
        (0, "choose", 0, "sequence"),
        id="choose",
    ),
    pytest.param(
        lambda leaf: {"repeat": {"count": 2, "sequence": [leaf]}},
        (0, "repeat", "sequence"),
        id="repeat",
    ),
]


def at(value, path):
    for key in path:
        value = value[key]
    return value


@pytest.mark.parametrize("wrap, path", CONTAINERS)
@pytest.mark.parametrize("enabled", [None, "{{ enabled }}"])
def test_stop_expansion_keeps_native_scope_metadata_and_payload(
    wrap, path, enabled, monkeypatch
):
    monkeypatch.setattr(rr, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    leaf = {
        "stop": "finished",
        "alias": "Stop",
        "continue_on_error": True,
        "error": False,
    }
    if enabled is not None:
        leaf["enabled"] = enabled
    raw = [wrap(leaf)]
    original = deepcopy(raw)
    result, completed, stopped = rr._outcome_probes(raw)
    assert result[0] == {"variables": {completed: False, stopped: False}}
    assert result[-1] == {"variables": {completed: True}}
    children = at(result[1:-1], path)
    marker = {"variables": {stopped: True}}
    if enabled is None:
        assert children == [marker, leaf]
    else:
        assert children == [
            {
                "sequence": [
                    marker,
                    {key: value for key, value in leaf.items() if key != "enabled"},
                ],
                "enabled": enabled,
                "alias": "Stop",
                "continue_on_error": True,
            }
        ]
    assert raw == original


@pytest.mark.parametrize("wrap, path", CONTAINERS)
def test_live_guards_stay_adjacent_to_original_effect_and_retain_enabled(wrap, path):
    leaf = {
        "event": "native_event",
        "enabled": "{{ enabled }}",
        "event_data": {"sequence": [{"stop": "payload"}]},
    }
    raw = [wrap(leaf)]
    original = deepcopy(raw)
    children = at(rr._guard_native_actions(raw), path)
    assert children == [
        {
            "action": f"{rr.DOMAIN}.{rr._GUARD_SERVICE}",
            "data": {"pending_action": {"event": True}},
            "enabled": leaf["enabled"],
        },
        leaf,
    ]
    assert "event_data" not in children[0]["data"]["pending_action"]
    assert raw == original


@pytest.mark.parametrize("wrap, path", CONTAINERS)
def test_effect_mapper_transforms_payload_lookalikes_but_not_branch_conditions(
    wrap, path
):
    condition = {
        "condition": "template",
        "value_template": "{{ states('sensor.secret') }}",
    }
    payload = {
        "sequence": [{"stop": "payload"}],
        "choose": [{"conditions": "secret", "sequence": []}],
    }
    leaf = {"action": "notify.send_message", "data": payload}
    raw = [wrap(leaf)]
    at(raw, path).insert(0, condition)
    original = deepcopy(raw)
    seen = []

    def transform(value):
        seen.append(value)
        return ("mapped", value)

    mapped = rr._map_script_effect_templates(raw, transform)
    children = at(mapped, path)
    assert children[0] == condition
    assert children[0]["value_template"] is condition["value_template"]
    assert children[1] == {
        "action": ("mapped", leaf["action"]),
        "data": ("mapped", payload),
    }
    assert payload in seen
    if "choose" in raw[0] and raw[0]["choose"]:
        assert mapped[0]["choose"][0]["conditions"] == "{{ true }}"
        assert mapped[0]["choose"][0]["alias"] == "branch"
    assert raw == original


def test_repeat_condition_and_input_roles_keep_callback_order_and_identity():
    trusted = [{"condition": "template", "value_template": "{{ secret }}"}]
    raw = [
        {
            "alias": "outer",
            "repeat": {
                "count": "{{ count }}",
                "while": trusted,
                "until": trusted,
                "for_each": [1, 2],
                "sequence": [{"variables": {"answer": "{{ answer }}"}}],
            },
        }
    ]
    seen = []

    def transform(value):
        seen.append(value)
        return value

    mapped = rr._map_script_effect_templates(raw, transform)
    assert seen == ["outer", "{{ count }}", [1, 2], {"answer": "{{ answer }}"}]
    assert mapped[0]["repeat"]["while"] is trusted
    assert mapped[0]["repeat"]["until"] is trusted


def test_transformers_do_not_acquire_new_action_budgets(monkeypatch):
    raw = [{"delay": 0}] * (rr.MAX_SCRIPT_NODES + 1)
    assert len(rr._guard_native_actions(raw)) == len(raw)
    assert len(rr._outcome_probes(raw)[0]) == len(raw) + 2
    assert len(rr._map_script_effect_templates(raw, lambda value: value)) == len(raw)


def test_nested_payloads_and_non_list_structural_values_remain_opaque():
    payload = {"stop": "payload", "sequence": [{"action": "payload"}]}
    raw = [
        {
            "action": "notify.send_message",
            "data": payload,
            "repeat": {"sequence": None},
        },
        {"sequence": ["literal", {"delay": 0}]},
    ]
    guarded = rr._guard_native_actions(raw)
    assert guarded[0]["data"] is payload
    assert guarded[0]["repeat"]["sequence"] is None
    assert guarded[1]["sequence"][0] == "literal"
    probes, _, _ = rr._outcome_probes(raw)
    assert probes[1]["data"] is payload
    assert probes[1]["repeat"]["sequence"] is None
    assert probes[2]["sequence"][0] == "literal"


def test_shared_rewriter_supports_drop_expansion_and_nonrecursive_replacement():
    from custom_components.extended_openai_conversation_responses.script_structure import (
        rewrite_script_actions,
    )

    raw = [
        {
            "sequence": [
                {"alias": "drop", "delay": 1},
                {"alias": "expand", "delay": 2},
                {"alias": "replace", "sequence": [{"alias": "unvisited", "delay": 3}]},
            ]
        }
    ]
    original = deepcopy(raw)
    visited = []

    def rewrite(action, kind, descend):
        visited.append(action.get("alias", "root"))
        if action.get("alias") == "drop":
            return []
        if action.get("alias") == "replace":
            return [{"sequence": [{"alias": "generated", "delay": 4}]}]
        if action.get("alias") == "expand":
            return [{"variables": {"marker": True}}, descend()]
        return [descend()]

    rewritten = rewrite_script_actions(raw, rewrite)
    assert visited == ["root", "drop", "expand", "replace"]
    assert rewritten == [
        {
            "sequence": [
                {"variables": {"marker": True}},
                {"alias": "expand", "delay": 2},
                {"sequence": [{"alias": "generated", "delay": 4}]},
            ]
        }
    ]
    assert raw == original
