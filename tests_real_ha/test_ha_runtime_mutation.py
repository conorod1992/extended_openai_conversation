"""Real-HA runtime mutation acceptance for Request Rules and native scripts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
)
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from tests_real_ha.test_cross_feature_acceptance import (
    _agent,
    _provider,
    _rule,
    _say,
    _speech,
)
from tests_real_ha.test_native_service_disappearance import (
    _native_execute_service_tool,
    _say as _provider_say,
)
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
)
from tests_real_ha.test_provider_wire_e2e import _install_wire

_HELPER_ENTITY_ID = "input_boolean.runtime_rule_gate"
_SCRIPT_ENTITY_ID = "script.eoai_runtime_mutable"
_SCRIPT_OBJECT_ID = "eoai_runtime_mutable"


def _local_action(
    message: str,
    *,
    phrase: str = "run mutable rule",
    order: int = 0,
) -> dict[str, Any]:
    rule = _rule(
        "local_action",
        {
            "actions": [
                {
                    "action": "runtime_mutation_probe.record",
                    "data": {"message": message},
                }
            ],
            "success_response": message,
            "failure_response": "Runtime mutation action failed",
        },
        phrase=phrase,
    )
    rule["name"] = f"Runtime mutation {message}"
    rule["order"] = order
    return rule


@pytest.mark.asyncio
async def test_request_rule_condition_rebinds_after_entity_delete_and_same_id_recreate(
    hass,
    monkeypatch,
) -> None:
    """A cached native condition checker must observe the current entity generation."""
    agent = await _agent(hass, title="Request Rule Entity Recreation")
    _provider(monkeypatch, agent, [])
    registry = er.async_get(hass)

    original = registry.async_get_or_create(
        domain="input_boolean",
        platform="runtime_mutation_test",
        unique_id="runtime-rule-gate-generation-a",
        suggested_object_id="runtime_rule_gate",
    )
    assert original.entity_id == _HELPER_ENTITY_ID
    hass.states.async_set(_HELPER_ENTITY_ID, "on")
    await hass.async_block_till_done()

    calls: list[str] = []

    async def record(call) -> None:
        calls.append(call.data["message"])

    hass.services.async_register("runtime_mutation_probe", "record", record)

    conditioned = _local_action("conditioned", order=0)
    conditioned["conditions"] = [
        {
            "condition": "state",
            "entity_id": _HELPER_ENTITY_ID,
            "state": "on",
        }
    ]
    fallback = _local_action("fallback", order=1)

    await agent._request_rules.async_create(conditioned)
    await agent._request_rules.async_create(fallback)

    # Warm the rule's cached native HA condition checker on generation A.
    first = await _say(hass, agent, "run mutable rule")
    assert _speech(first) == "conditioned"
    assert calls == ["conditioned"]
    cached = agent._request_rules._condition_checkers
    assert conditioned["id"] if "id" in conditioned else cached
    assert cached

    # Delete both state and registry identity. The cached checker must treat the
    # condition as false, skip this rule entirely and allow the next match.
    hass.states.async_remove(_HELPER_ENTITY_ID)
    registry.async_remove(_HELPER_ENTITY_ID)
    await hass.async_block_till_done()
    assert hass.states.get(_HELPER_ENTITY_ID) is None
    assert registry.async_get(_HELPER_ENTITY_ID) is None

    missing = await _say(hass, agent, "run mutable rule")
    assert _speech(missing) == "fallback"
    assert calls == ["conditioned", "fallback"]

    # Recreate a different registry object with the exact same visible entity ID.
    replacement = registry.async_get_or_create(
        domain="input_boolean",
        platform="runtime_mutation_test",
        unique_id="runtime-rule-gate-generation-b",
        suggested_object_id="runtime_rule_gate",
    )
    assert replacement.entity_id == _HELPER_ENTITY_ID
    assert replacement.id != original.id
    hass.states.async_set(_HELPER_ENTITY_ID, "on")
    await hass.async_block_till_done()

    recreated = await _say(hass, agent, "run mutable rule")
    assert _speech(recreated) == "conditioned"
    assert calls == ["conditioned", "fallback", "conditioned"]


def _script(marker: str) -> dict[str, Any]:
    return {
        "alias": "EOAI runtime mutable",
        "sequence": [
            {
                "action": "runtime_mutation_probe.record",
                "data": {"message": marker},
            }
        ],
    }


def _script_arguments() -> dict[str, Any]:
    return {
        "list": [
            {
                "domain": "script",
                "service": "turn_on",
                "service_data": {"entity_id": [_SCRIPT_ENTITY_ID]},
            }
        ]
    }


def _tool_result(body: dict[str, Any], call_id: str) -> dict[str, Any]:
    message = next(
        item
        for item in body["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == call_id
    )
    return json.loads(message["content"])


@pytest.mark.asyncio
async def test_function_tool_uses_reloaded_native_script_body_without_agent_reload(
    hass,
    monkeypatch,
) -> None:
    """An unchanged EOAI Function Tool must execute the latest HA script body."""
    config_dir = Path(hass.config.config_dir)
    scripts_file = config_dir / "scripts.yaml"
    configuration_file = config_dir / "configuration.yaml"

    initial = {_SCRIPT_OBJECT_ID: _script("script-body-v1")}
    scripts_file.write_text(yaml.safe_dump(initial, sort_keys=False), encoding="utf-8")
    configuration_file.write_text(
        "script: !include scripts.yaml\n",
        encoding="utf-8",
    )

    calls: list[str] = []

    async def record(call) -> None:
        calls.append(call.data["message"])

    hass.services.async_register("runtime_mutation_probe", "record", record)
    assert await async_setup_component(hass, "script", {"script": initial})
    await hass.async_block_till_done()
    assert hass.states.get(_SCRIPT_ENTITY_ID) is not None
    async_expose_entity(hass, conversation.DOMAIN, _SCRIPT_ENTITY_ID, True)

    entry_options = {
        CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
        CONF_CHAT_MODEL: "gpt-5.6",
        CONF_FUNCTION_TOOLS: [_native_execute_service_tool()],
    }
    from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry

    entry = _make_entry(
        "Native Script Body Reload",
        include_ai_task=False,
        conversation_options=entry_options,
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    first_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "call-script-body-v1",
                "execute_service",
                _script_arguments(),
            ),
            _chat_sse_text("Ran native script version one."),
        ],
    )
    first = await _provider_say(hass, entry.entry_id, "Run the mutable native script")
    assert _speech(first) == "Ran native script version one."
    assert calls == ["script-body-v1"]
    assert _tool_result(
        first_wire.requests[1]["body"], "call-script-body-v1"
    )["result"][0]["success"] is True

    # Change only Home Assistant's script definition. EOAI and its Function Tool
    # stay loaded and unchanged while the native script integration reloads itself.
    updated = {_SCRIPT_OBJECT_ID: _script("script-body-v2")}
    scripts_file.write_text(yaml.safe_dump(updated, sort_keys=False), encoding="utf-8")
    assert await hass.services.async_call("script", "reload", blocking=True) is None
    await hass.async_block_till_done()
    assert hass.states.get(_SCRIPT_ENTITY_ID) is not None
    assert conversation.async_get_agent(hass, entry.entry_id) is agent

    second_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "call-script-body-v2",
                "execute_service",
                _script_arguments(),
            ),
            _chat_sse_text("Ran native script version two."),
        ],
    )
    second = await _provider_say(
        hass,
        entry.entry_id,
        "Run the same mutable native script after reload",
    )
    assert _speech(second) == "Ran native script version two."
    assert calls == ["script-body-v1", "script-body-v2"]
    assert _tool_result(
        second_wire.requests[1]["body"], "call-script-body-v2"
    )["result"][0]["success"] is True

    configured = next(
        tool
        for tool in agent.subentry.data[CONF_FUNCTION_TOOLS]
        if tool["spec"]["name"] == "execute_service"
    )
    assert configured["enabled"] is True
