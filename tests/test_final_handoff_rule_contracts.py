"""Captured native selectors and retained routing remain valid after edits."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import request_rules as rr
from homeassistant.exceptions import HomeAssistantError
from tests.test_request_rules import MemoryStore, local_rule
from tests.test_request_rule_routing_semantics import _rule


@pytest.mark.parametrize(
    "selector", ["{room}", "{{ room }}", "{{ request.slots.room }}"]
)
async def test_capture_condition_survives_create_and_reload(hass, selector):
    rule = local_rule("Check", ["check {room}"], "sentence_pattern")
    rule["action"]["actions"] = [
        {"condition": "state", "entity_id": "light." + selector, "state": "on"},
        {"stop": "done"},
    ]
    store = MemoryStore()
    manager = rr.RequestRules(store)
    await manager.async_initialize()
    await manager.async_create(rule)
    assert not manager._diagnostics
    assert await manager.async_match(hass, "check kitchen") is not None
    loaded = rr.RequestRules(store)
    await loaded.async_initialize()
    assert not loaded._diagnostics
    assert await loaded.async_match(hass, "check kitchen") is not None


def test_unavailable_selector_variable_still_rejected(hass):
    rule = local_rule("Check", ["check {room}"], "sentence_pattern")
    rule["action"]["actions"] = [
        {"condition": "state", "entity_id": "light.{{ missing }}", "state": "on"}
    ]
    with pytest.raises(ValueError, match="capture|selector"):
        rr.validate_rule(rule)


@pytest.mark.parametrize("selector", ["{scene_id}", "{{ request.slots.scene_id }}"])
async def test_scene_capture_uses_real_native_schema(hass, selector):
    from homeassistant.helpers.script import async_validate_actions_config

    rule = local_rule("Scene", ["activate {scene_id}"], "sentence_pattern")
    rule["action"]["actions"] = [{"scene": selector}]
    validated = rr.validate_rule(rule)
    concrete = rr._prepare_concrete_captures(
        validated["action"]["actions"], {"scene_id": "scene.movie"}
    )
    actions = await async_validate_actions_config(hass, concrete)
    assert actions[0]["scene"] == "scene.movie"


@pytest.mark.parametrize("request_reset", [True, False])
async def test_chained_route_validates_retained_combination_before_commit(
    hass, request_reset
):
    first = _rule(
        "first",
        "route",
        action={
            "reset": request_reset,
            "model": None if request_reset else "gpt-5.2",
            "reasoning_effort": None if request_reset else "low",
            "scope": "request",
            "continue_to_ai": False,
            "success_response": "Updated",
        },
    )
    first["continue_matching"] = True
    second = _rule(
        "second",
        "route",
        order=1,
        action={
            "model": "gpt-5",
            "reasoning_effort": None,
            "scope": "conversation",
            "continue_to_ai": True,
            "success_response": "Updated",
        },
    )
    manager = rr.RequestRules(MemoryStore({"rules": [first, second]}))
    await manager.async_initialize()
    runtime = rr.RequestRuleRuntime()
    previous = {"chat_model": "gpt-5.2", "reasoning_effort": "xhigh"}
    runtime.set("session", previous)
    options = {
        "chat_model": "gpt-5.2",
        "reasoning_effort": "low",
        "api_mode": "responses",
        "max_function_calls_per_conversation": 0,
        "continue_conversation": "ha_default",
    }
    with pytest.raises((HomeAssistantError, ValueError), match="reasoning|xhigh"):
        await rr.async_evaluate_rule(
            hass,
            manager,
            runtime,
            "route",
            "session",
            "gpt-5.2",
            request_options=options,
            entry_data={},
        )
    assert runtime.get("session") == previous
    # Supplying a compatible retained effort permits the same chain.
    second["action"]["reasoning_effort"] = "high"
    valid = rr.RequestRules(MemoryStore({"rules": [deepcopy(first), second]}))
    await valid.async_initialize()
    await rr.async_evaluate_rule(
        hass,
        valid,
        runtime,
        "route",
        "session",
        "gpt-5.2",
        request_options=options,
        entry_data={},
    )
    assert runtime.get("session") == {"chat_model": "gpt-5", "reasoning_effort": "high"}
