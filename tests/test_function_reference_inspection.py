"""Independent recognition contract; mutation policy remains caller-owned."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.function_dependency_integrity import (
    _rule_script_actions,
    recursive_function_references,
    state_control_references,
)
from custom_components.extended_openai_conversation_responses.function_reference_inspection import (
    inspect_function_references,
)

# Intentionally explicit rather than derived from the production service table.
CASES = [
    ("extended_openai_conversation_responses.call_function", "call", "function"),
    (
        "extended_openai_conversation_responses.enable_function_tools",
        "enable",
        "functions",
    ),
    (
        "extended_openai_conversation_responses.disable_function_tools",
        "disable",
        "functions",
    ),
    (
        "extended_openai_conversation_responses.enable_function_groups",
        "enable",
        "function_groups",
    ),
    (
        "extended_openai_conversation_responses.disable_function_groups",
        "disable",
        "function_groups",
    ),
]


@pytest.mark.parametrize("service,operation,field", CASES)
@pytest.mark.parametrize("selector", ["action", "service", "service_template"])
@pytest.mark.parametrize("uppercase", [False, True])
def test_service_recognition_and_effective_payload(
    service, operation, field, selector, uppercase
):
    action = {
        selector: service.upper() if uppercase else service,
        "data": {field: "old", "config_entry": "entry"},
        "data_template": {field: "winning"},
    }
    before = deepcopy(action)
    (reference,) = inspect_function_references([action])
    assert (reference.operation, reference.field) == (operation, field)
    assert reference.payload == {field: "winning", "config_entry": "entry"}
    assert reference.action is action
    reference.payload[field] = "detached snapshot"
    assert action == before


@pytest.mark.parametrize("service,operation,field", CASES)
def test_selector_precedence_and_unknown_services(service, operation, field):
    assert (
        list(
            inspect_function_references(
                [{"action": "other.service", "service": service}]
            )
        )
        == []
    )
    assert (
        list(inspect_function_references([{"action": None, "service": service}])) == []
    )
    assert (
        list(
            inspect_function_references(
                [{"service": "{{ selected }}", "data": {"action": service}}]
            )
        )
        == []
    )
    assert list(inspect_function_references([{"action": service + "_lookalike"}])) == []
    (reference,) = inspect_function_references(
        [{"service": service, "service_template": "ignored.service"}]
    )
    assert reference.operation == operation


@pytest.mark.parametrize("payload", ["{{ payload }}", None, ["unknown"], 42])
@pytest.mark.parametrize("slot", ["data", "data_template"])
def test_known_service_with_unknown_whole_payload_is_not_lost(payload, slot):
    action = {"action": CASES[1][0], "data": {"functions": ["old"]}, slot: payload}
    (reference,) = inspect_function_references([action])
    assert reference.operation == "enable"
    assert reference.payload is None


def test_nested_locations_ignore_ordinary_payloads_and_conditions():
    def action(index):
        service, _operation, field = CASES[index]
        return {
            "action": service,
            "data": {field: "target", "result_alias": {"action": CASES[0][0]}},
        }

    leaves = [action(i) for i in range(5)]
    rule = {
        "action": {
            "actions": [
                {
                    "choose": [{"conditions": [action(0)], "sequence": [leaves[0]]}],
                    "default": [leaves[1]],
                },
                {"repeat": {"while": [action(0)], "sequence": [leaves[2]]}},
                {"if": [action(0)], "then": [leaves[3]], "else": [leaves[4]]},
                {"action": "other.service", "data": {"sequence": [action(0)]}},
            ]
        }
    }
    before = deepcopy(rule)
    records = list(inspect_function_references(_rule_script_actions(rule)))
    # Native traversal visits default before choose; do not change that contract.
    assert [r.action for r in records] == [leaves[1], leaves[0], *leaves[2:]]
    assert rule == before


def test_call_and_state_ownership_policies_are_not_accidentally_unified():
    call = {
        "action": CASES[0][0],
        "data": {"function": "target", "config_entry": "foreign", "agent_id": "other"},
    }
    state = {
        "action": CASES[1][0],
        "data": {
            "functions": ["target"],
            "config_entry": "foreign",
            "agent_id": "other",
        },
    }
    rule = {
        "id": "id",
        "name": "Policy",
        "enabled": False,
        "action": {"actions": [call, state]},
    }
    assert recursive_function_references(SimpleNamespace(_rules=[rule]), "target") == [
        {"id": "id", "name": "Policy"}
    ]
    assert (
        state_control_references(
            [rule], "functions", "target", "entry", "agent", lambda _: None
        )
        == []
    )


def test_inspection_retains_existing_traversal_budget():
    rule = {"action": {"actions": [{"action": "other.service"} for _ in range(501)]}}
    with pytest.raises(ValueError, match="500"):
        list(inspect_function_references(_rule_script_actions(rule)))
