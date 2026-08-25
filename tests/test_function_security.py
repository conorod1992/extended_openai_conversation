"""Adversarial classification tests for configured Guest capabilities."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
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


def test_composite_recursively_inherits_strongest_classification() -> None:
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
    assert (
        classify_tool(
            {
                "function": {
                    "type": "composite",
                    "sequence": [
                        {"type": "native", "name": "get_history"},
                        {
                            "type": "composite",
                            "sequence": [{"type": "native", "name": "add_automation"}],
                        },
                    ],
                }
            }
        )
        == FunctionSecurity.UNSCOPABLE
    )


def test_composite_classification_is_malformed_cycle_and_depth_safe() -> None:
    assert classify_function({"type": "composite", "sequence": "bad"}) == (
        FunctionSecurity.UNSCOPABLE
    )
    assert classify_function({"type": "composite", "sequence": [42]}) == (
        FunctionSecurity.UNSCOPABLE
    )
    cyclic = {"type": "composite", "sequence": []}
    cyclic["sequence"].append(cyclic)
    assert classify_function(cyclic) == FunctionSecurity.UNSCOPABLE

    deepest: dict = {"type": "native", "name": "get_history"}
    for _ in range(20):
        deepest = {"type": "composite", "sequence": [deepest]}
    assert classify_function(deepest) == FunctionSecurity.UNSCOPABLE


def test_native_classification_fails_closed() -> None:
    assert (
        classify_function({"type": "native", "name": "get_history"})
        == FunctionSecurity.SAFE
    )
    assert (
        classify_function({"type": "native", "name": "execute_service"})
        == FunctionSecurity.CONTROL
    )
    assert (
        classify_function({"type": "native", "name": "add_automation"})
        == FunctionSecurity.UNSCOPABLE
    )
    assert (
        classify_function({"type": "native", "name": "future_native"})
        == FunctionSecurity.UNSCOPABLE
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
    for domain in ("script", "automation", "scene"):
        assert (
            classify_function(
                {
                    "type": "script",
                    "sequence": [
                        {
                            "service": f"{domain}.turn_on",
                            "target": {"entity_id": f"{domain}.owner_routine"},
                        }
                    ],
                }
            )
            == FunctionSecurity.INDIRECT
        )


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
    for domain in ("script", "automation", "scene"):
        assert contains_indirect_service_call(
            {"nested": [{"domain": domain, "service": "turn_on"}]}
        )
        assert contains_indirect_service_call({"service": f"{domain}.turn_on"})
    assert not contains_indirect_service_call({"domain": "light", "service": "turn_on"})


def _execution_entity(tool: dict, policy: GuestCapabilityPolicy):
    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    entity.entry = SimpleNamespace(entry_id="entry")
    entity.subentry = SimpleNamespace(subentry_id="agent", data={})
    entity.hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_get_entry=lambda _entry_id: SimpleNamespace(
                subentries={"agent": SimpleNamespace(data={})}
            )
        )
    )
    entity._configured_function_tools_from_data = lambda _data: [tool]
    entity._effective_guest_policy = lambda: policy
    entity._filter_guest_entities = lambda _entities, *, control: []
    return entity


async def test_static_script_target_is_checked_again_at_execution(monkeypatch) -> None:
    tool = {
        "spec": {
            "name": "static_script",
            "description": "Run a static script",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "script",
            "sequence": [
                {
                    "service": "light.turn_on",
                    "target": {"entity_id": "light.guest"},
                }
            ],
        },
    }
    policy = GuestCapabilityPolicy(
        True,
        readable_entity_ids=frozenset({"light.guest"}),
        controllable_entity_ids=frozenset({"light.guest"}),
        configured_tool_names=frozenset({"static_script"}),
    )
    entity = _execution_entity(tool, policy)
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.conversation.get_exposed_entities",
        lambda _hass: [],
    )
    execute = AsyncMock(return_value="executed")
    monkeypatch.setattr(ExtendedOpenAIBaseLLMEntity, "_execute_function_tool", execute)
    tool_input = SimpleNamespace(
        tool_name="static_script", tool_args={}, id="call-static"
    )

    assert await entity._execute_function_tool(tool, tool_input, None, []) == "executed"
    execute.assert_awaited_once()

    tool["function"]["sequence"][0]["target"] = {"entity_id": "lock.private"}
    denied = await entity._execute_function_tool(tool, tool_input, None, [])
    assert json.loads(denied.tool_result["result"])["status"] == "unavailable"
    execute.assert_awaited_once()


async def test_direct_scoped_control_obeys_execution_target_policy(monkeypatch) -> None:
    tool = {
        "spec": {
            "name": "control",
            "description": "Control Home Assistant",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "execute_service"},
    }
    policy = GuestCapabilityPolicy(
        True,
        readable_entity_ids=frozenset({"light.guest"}),
        controllable_entity_ids=frozenset({"light.guest"}),
        configured_tool_names=frozenset({"control"}),
    )
    entity = _execution_entity(tool, policy)
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.conversation.get_exposed_entities",
        lambda _hass: [],
    )
    execute = AsyncMock(return_value="executed")
    monkeypatch.setattr(ExtendedOpenAIBaseLLMEntity, "_execute_function_tool", execute)

    allowed_input = SimpleNamespace(
        tool_name="control",
        tool_args={
            "domain": "light",
            "service": "turn_on",
            "service_data": {"entity_id": "light.guest"},
        },
        id="call-allowed",
    )
    assert await entity._execute_function_tool(tool, allowed_input, None, []) == (
        "executed"
    )
    execute.assert_awaited_once()

    denied_input = SimpleNamespace(
        tool_name="control",
        tool_args={
            "domain": "lock",
            "service": "unlock",
            "service_data": {"entity_id": "lock.private"},
        },
        id="call-denied",
    )
    denied = await entity._execute_function_tool(tool, denied_input, None, [])
    assert json.loads(denied.tool_result["result"])["status"] == "unavailable"
    execute.assert_awaited_once()


@pytest.mark.parametrize("domain", ["script", "automation", "scene"])
async def test_indirect_wrapper_is_rejected_at_execution(
    monkeypatch, domain: str
) -> None:
    tool = {
        "spec": {
            "name": "control",
            "description": "Control Home Assistant",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "execute_service"},
    }
    policy = GuestCapabilityPolicy(
        True,
        readable_entity_ids=frozenset({f"{domain}.guest"}),
        controllable_entity_ids=frozenset({f"{domain}.guest"}),
        configured_tool_names=frozenset({"control"}),
    )
    entity = _execution_entity(tool, policy)
    execute = AsyncMock(return_value="executed")
    monkeypatch.setattr(ExtendedOpenAIBaseLLMEntity, "_execute_function_tool", execute)

    denied = await entity._execute_function_tool(
        tool,
        SimpleNamespace(
            tool_name="control",
            tool_args={
                "domain": domain,
                "service": "turn_on",
                "service_data": {"entity_id": f"{domain}.guest"},
            },
            id=f"call-{domain}",
        ),
        None,
        [],
    )
    assert json.loads(denied.tool_result["result"])["status"] == "unavailable"
    execute.assert_not_awaited()
