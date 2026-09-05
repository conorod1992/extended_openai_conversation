"""Focused regression tests for PR17 Request Rule determinism."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
    DOMAIN,
    SERVICE_CALL_FUNCTION,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    RequestRuleRuntime,
    RequestRules,
    async_evaluate_rule,
    validate_rule,
    validate_wording_groups,
)
from homeassistant.exceptions import HomeAssistantError


class MemoryStore:
    """Minimal in-memory Store seam."""

    def __init__(self, data=None):
        self.data = deepcopy(data)
        self.saves = 0

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)
        self.saves += 1


def local_rule(
    name: str = "Good night",
    *,
    rule_id: str = "good-night",
    order: int = 0,
    phrases=None,
    match_type: str = "equals",
):
    return {
        "id": rule_id,
        "name": name,
        "enabled": True,
        "phrases": phrases or ["good night"],
        "match_type": match_type,
        "action_type": "local_action",
        "action": {
            "actions": [
                {
                    "domain": "script",
                    "service": "turn_on",
                    "target": {"entity_id": ["script.goodnight"]},
                    "data": {},
                }
            ],
            "success_response": "Done",
            "failure_response": "Failed safely",
        },
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": order,
    }


def routing_rule(
    *,
    rule_id: str = "routing",
    phrase: str = "think carefully",
    match_type: str = "starts_with",
    scope: str = "conversation",
    model: str | None = "gpt-5",
    effort: str | None = None,
    reset: bool = False,
):
    return {
        "id": rule_id,
        "name": "Routing",
        "enabled": True,
        "phrases": [phrase],
        "match_type": match_type,
        "action_type": "model_routing",
        "action": {
            "model": model,
            "reasoning_effort": effort,
            "scope": scope,
            "reset": reset,
            "success_response": "Updated",
        },
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": 0,
    }


async def manager(*rules):
    result = RequestRules(MemoryStore({"rules": list(rules)}))
    await result.async_initialize()
    return result


@pytest.mark.parametrize(
    "stored",
    [
        ["not", "an", "object"],
        {"rules": {"not": "a list"}},
        {"rules": 123},
    ],
)
async def test_malformed_store_container_self_heals(stored) -> None:
    store = MemoryStore(stored)
    rules = RequestRules(store)
    await rules.async_initialize()

    assert rules.snapshot()["rules"] == []
    assert store.saves == 1
    assert store.data["rules"] == []


async def test_duplicate_orders_are_reindexed_after_mutations() -> None:
    store = MemoryStore(
        {
            "rules": [
                local_rule("Zulu", rule_id="z", order=3),
                local_rule("Alpha", rule_id="a", order=3),
            ]
        }
    )
    rules = RequestRules(store)
    await rules.async_initialize()
    assert [rule["order"] for rule in rules.snapshot()["rules"]] == [0, 1]
    assert store.saves == 1

    created = await rules.async_create(local_rule("Middle", rule_id="m", order=0))
    assert created["id"] == "m"
    assert [rule["order"] for rule in rules.snapshot()["rules"]] == [0, 1, 2]

    await rules.async_update("z", {**local_rule("Zulu", rule_id="wrong"), "order": 0})
    assert [rule["order"] for rule in rules.snapshot()["rules"]] == [0, 1, 2]
    assert next(rule for rule in rules.snapshot()["rules"] if rule["id"] == "z")["id"] == "z"

    await rules.async_delete("m")
    assert [rule["order"] for rule in rules.snapshot()["rules"]] == [0, 1]


def test_wording_groups_reject_phrases_that_normalize_empty() -> None:
    with pytest.raises(ValueError, match="searchable text"):
        validate_wording_groups([{"canonical": "!!!", "alternatives": ["activate"]}])
    with pytest.raises(ValueError, match="searchable text"):
        validate_wording_groups([{"canonical": "activate", "alternatives": ["---"]}])


async def test_hassil_nested_group_with_wildcard_slot_uses_hassil_matcher() -> None:
    rules = await manager(
        local_rule(
            phrases=["((turn|switch) on|activate) {room} lights"],
            match_type="sentence_pattern",
        )
    )

    first = rules.match("switch on kitchen lights")
    second = rules.match("activate upstairs guest room lights")
    assert first is not None and first.slots == {"room": "kitchen"}
    assert second is not None and second.slots == {"room": "upstairs guest room"}


async def test_request_reset_does_not_clear_conversation_override() -> None:
    runtime = RequestRuleRuntime()
    runtime.set(
        "session",
        {CONF_CHAT_MODEL: "gpt-5", CONF_REASONING_EFFORT: "high"},
    )
    rule = routing_rule(
        phrase="use defaults",
        scope="request",
        model=None,
        effort=None,
        reset=True,
    )
    result = await async_evaluate_rule(
        SimpleNamespace(),
        await manager(rule),
        runtime,
        "use defaults for this request",
        "session",
    )
    assert result is not None
    assert runtime.get("session") == {
        CONF_CHAT_MODEL: "gpt-5",
        CONF_REASONING_EFFORT: "high",
    }
    defaults = {CONF_CHAT_MODEL: "gpt-4o", CONF_REASONING_EFFORT: "low"}
    assert runtime.effective_options(
        defaults, "session", result.request_override
    ) == defaults


async def test_conversation_reset_still_clears_conversation_override() -> None:
    runtime = RequestRuleRuntime()
    runtime.set("session", {CONF_CHAT_MODEL: "gpt-5"})
    rule = routing_rule(
        phrase="use defaults",
        match_type="equals",
        scope="conversation",
        model=None,
        effort=None,
        reset=True,
    )
    result = await async_evaluate_rule(
        SimpleNamespace(),
        await manager(rule),
        runtime,
        "use defaults",
        "session",
    )
    assert result is not None and result.consume
    assert runtime.get("session") == {}


async def test_duplicate_names_are_bounded_and_unique() -> None:
    source_name = "x" * 120
    rules = await manager(local_rule(source_name, rule_id="source"))
    first = await rules.async_duplicate("source")
    second = await rules.async_duplicate("source")

    assert len(first["name"]) <= 120
    assert len(second["name"]) <= 120
    assert first["name"].endswith(" copy")
    assert second["name"].endswith(" copy 2")
    assert first["name"] != second["name"]
    assert [rule["order"] for rule in rules.snapshot()["rules"]] == [0, 1, 2]


async def test_captured_routing_values_are_resolved_and_validated() -> None:
    runtime = RequestRuleRuntime()
    model_rule = routing_rule(
        rule_id="model",
        phrase="use {model_name}",
        match_type="sentence_pattern",
        model="{model_name}",
    )
    model_rule["action"]["success_response"] = "Using {model_name}"
    result = await async_evaluate_rule(
        SimpleNamespace(),
        await manager(model_rule),
        runtime,
        "use gpt-5",
        "session",
    )
    assert result is not None
    assert result.response == "Using gpt-5"
    assert runtime.get("session")[CONF_CHAT_MODEL] == "gpt-5"

    effort_rule = routing_rule(
        rule_id="effort",
        phrase="reason {effort}",
        match_type="sentence_pattern",
        model="gpt-5",
        effort="{effort}",
    )
    await async_evaluate_rule(
        SimpleNamespace(),
        await manager(effort_rule),
        runtime,
        "reason high",
        "session",
    )
    assert runtime.get("session")[CONF_REASONING_EFFORT] == "high"

    with pytest.raises(HomeAssistantError, match="Unsupported captured reasoning effort"):
        await async_evaluate_rule(
            SimpleNamespace(),
            await manager(effort_rule),
            runtime,
            "reason extreme",
            "session",
        )


def test_routing_captures_reject_jinja_and_partial_effort_templates() -> None:
    rule = routing_rule(
        phrase="use {model_name}",
        match_type="sentence_pattern",
        model="{{ model_name }}",
    )
    with pytest.raises(ValueError, match="simple .* references"):
        validate_rule(rule)

    rule = routing_rule(
        phrase="reason {effort}",
        match_type="sentence_pattern",
        model="gpt-5",
        effort="level-{effort}",
    )
    with pytest.raises(ValueError, match="single .* reference"):
        validate_rule(rule)


async def test_management_create_assigns_id_and_validates_canonical_function_reference(
    monkeypatch,
) -> None:
    store = MemoryStore()
    rules = RequestRules(store)
    await rules.async_initialize()
    configured_tool = {
        "spec": {
            "name": "remember",
            "description": "Remember a fact",
            "parameters": {
                "type": "object",
                "properties": {"fact": {"type": "string"}},
                "required": ["fact"],
            },
        },
        "function": {"type": "native", "name": "get_attributes"},
        "enabled": True,
    }
    subentry = SimpleNamespace(
        subentry_id="agent",
        subentry_type="conversation",
        data={"functions": [configured_tool]},
    )
    entry = SimpleNamespace(
        domain=DOMAIN,
        subentries={"agent": subentry},
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=lambda _entry_id: entry)
    )

    async def get_rules(*_args):
        return rules

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.async_get_request_rules",
        get_rules,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.configured_function_tools_from_data",
        lambda _data: [configured_tool],
    )

    rule = local_rule()
    rule.pop("id")
    rule["action"]["actions"] = [
        {
            "type": "function",
            "function": "remember",
            "arguments": {"fact": {"source": "fixed", "value": "bin day"}},
        }
    ]
    base = {
        "section": "request_rules",
        "entry_id": "entry",
        "subentry_id": "agent",
    }
    created = await async_management_command(
        hass,
        "admin",
        True,
        {**base, "action": "create", "rule": rule},
    )
    assert created["rule"]["id"]
    assert created["rule"]["action"]["actions"] == [
        {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "data": {"function": "remember", "arguments": {"fact": "bin day"}},
        }
    ]

    bad = deepcopy(rule)
    bad["action"]["actions"][0]["function"] = "missing_tool"
    with pytest.raises(HomeAssistantError, match="unavailable or disabled"):
        await async_management_command(
            hass,
            "admin",
            True,
            {**base, "action": "create", "rule": bad},
        )

    missing_input = deepcopy(rule)
    missing_input["action"]["actions"][0]["arguments"] = {}
    with pytest.raises(HomeAssistantError, match="needs input: fact"):
        await async_management_command(
            hass,
            "admin",
            True,
            {**base, "action": "create", "rule": missing_input},
        )

    updated_payload = deepcopy(created["rule"])
    updated_payload["id"] = "wrong-id"
    updated_payload["enabled"] = False
    updated = await async_management_command(
        hass,
        "admin",
        True,
        {
            **base,
            "action": "update",
            "rule_id": created["rule"]["id"],
            "rule": updated_payload,
        },
    )
    assert updated["rule"]["id"] == created["rule"]["id"]
    assert updated["rule"]["enabled"] is False
