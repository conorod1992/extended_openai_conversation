"""Confirmed management requests exercise real HA and the SDK provider wire."""

import asyncio

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from tests_real_ha.test_provider_wire_e2e import _agent, _chat_sse_text, _install_wire


@pytest.mark.parametrize("matched", [False, True])
async def test_live_rule_request_reaches_provider(hass, monkeypatch, matched):
    agent = await _agent(hass, API_MODE_CHAT_COMPLETIONS)
    if matched:
        await agent._request_rules.async_create(
            {
                "name": "AI route",
                "phrases": ["hello"],
                "match_type": "equals",
                "action_type": "model_routing",
                "action": {
                    "model": "gpt-5.6",
                    "scope": "request",
                    "continue_to_ai": True,
                },
            }
        )
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text("Live provider response")])
    owner = await hass.auth.async_create_user(
        "Live request admin", group_ids=["system-admin"]
    )
    result = await async_management_command(
        hass,
        owner.id,
        True,
        {
            "section": "request_rules",
            "action": "test",
            "confirm": True,
            "entry_id": agent.entry.entry_id,
            "subentry_id": agent.subentry.subentry_id,
            "text": "hello",
        },
    )
    assert result["response"] == "Live provider response"
    assert result["conversation_id"]
    assert len(wire.requests) == 1


@pytest.mark.parametrize("executing", [False, True])
async def test_native_rule_edit_during_wait_keeps_original_outcome(
    hass, monkeypatch, executing
):
    from tests_real_ha.test_cross_feature_acceptance import _say, _speech
    from tests_real_ha.test_request_rules_script_semantics import _local, _record_action

    agent = await _agent(hass, API_MODE_CHAT_COMPLETIONS)
    wire = _install_wire(monkeypatch, agent, [])
    entered, release = asyncio.Event(), asyncio.Event()
    markers = []

    async def wait(_call):
        entered.set()
        await release.wait()

    async def record(call):
        markers.append(call.data["message"])

    hass.services.async_register("rule_probe", "wait", wait)
    hass.services.async_register("rule_probe", "record", record)
    original = await agent._request_rules.async_create(
        _local(
            [
                {"action": "rule_probe.wait"},
                _record_action("original marker"),
            ]
        )
    )
    other = await agent._request_rules.async_create(
        _local([_record_action("unrelated")], phrase="other")
    )
    pending = asyncio.create_task(_say(hass, agent, "run rule"))
    await asyncio.wait_for(entered.wait(), 5)
    target = original if executing else other
    await agent._request_rules.async_update(
        target["id"],
        {
            **target,
            "action": {
                **target["action"],
                "actions": [_record_action("replacement marker")],
                "success_response": "Future requests only",
            },
        },
    )
    release.set()
    result = await asyncio.wait_for(pending, 5)
    assert _speech(result) == "Done"
    assert markers == ["original marker"]
    assert wire.requests == []


async def _manage_rule_boundary(hass, agent, section, action, **fields):
    from custom_components.extended_openai_conversation_responses.management_ui import (
        persisted_config_projection,
    )

    revision = (
        agent._request_rules.revision()
        if section == "request_rules"
        else persisted_config_projection(agent.subentry).revision
    )
    return await async_management_command(
        hass,
        None,
        True,
        {
            "entry_id": agent.entry.entry_id,
            "subentry_id": agent.subentry.subentry_id,
            "section": section,
            "action": action,
            "revision": revision,
            **fields,
        },
    )


@pytest.mark.parametrize(
    "selector,service",
    [
        ("action", "EXTENDED_OPENAI_CONVERSATION_RESPONSES.call_function"),
        ("service", "extended_openai_conversation_responses.CALL_FUNCTION"),
        ("service_template", "Extended_OpenAI_Conversation_Responses.Call_Function"),
    ],
)
@pytest.mark.parametrize("payload", ["data", "data_template", "mixed"])
async def test_mixed_case_function_dependency_lifecycle(
    hass, monkeypatch, selector, service, payload
):
    from copy import deepcopy

    from custom_components.extended_openai_conversation_responses import (
        request_rules as rr,
    )
    from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
        request_rule_match_preview,
    )
    from homeassistant.exceptions import HomeAssistantError
    from tests.test_request_rules import MemoryStore
    from tests_real_ha.test_cross_feature_acceptance import _agent, _say, _speech
    from tests_real_ha.test_request_rules_script_semantics import _local

    tool = {
        "spec": {
            "name": "echo",
            "description": "Echo a value",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string", "minLength": 1}},
                "required": ["message"],
                "additionalProperties": False,
            },
        },
        "function": {
            "type": "template",
            "parse_result": True,
            "value_template": "{{ {'value': message} }}",
        },
    }
    agent = await _agent(hass, functions=[tool])
    # An invalid argument cannot bypass validation simply by changing service case.
    raw = _local([{selector: service, "data": {"function": "echo", "arguments": {}}}])
    with pytest.raises(HomeAssistantError, match="Missing required"):
        await _manage_rule_boundary(hass, agent, "request_rules", "create", rule=raw)
    call = {selector: service}
    data = {
        "function": "echo",
        "arguments": {"message": "Keep This Case"},
        "result_alias": "answer",
    }
    if payload == "mixed":
        call.update(data={"function": "unused", "arguments": {}}, data_template=data)
    else:
        call[payload] = data
    await _manage_rule_boundary(
        hass,
        agent,
        "request_rules",
        "create",
        rule=_local([call], success="Value {answer.value}"),
    )
    rules = agent._request_rules
    snapshot = rules.snapshot()
    rule = snapshot["rules"][0]
    assert rules.function_references("echo") == [
        {"id": rule["id"], "name": rule["name"]}
    ]
    match = rules.match("run rule")
    assert request_rule_match_preview(match)["would_do"]["functions"] == [
        {"name": "echo", "result_alias": "answer"}
    ]
    assert _speech(await _say(hass, agent, "run rule")) == "Value Keep This Case"
    with pytest.raises(HomeAssistantError, match="Request Rules"):
        await _manage_rule_boundary(
            hass, agent, "tools", "delete", name="echo", confirm=True
        )
    renamed = deepcopy(tool)
    renamed["spec"]["name"] = "renamed_echo"
    result = await _manage_rule_boundary(
        hass, agent, "tools", "save", original_name="echo", tool=renamed
    )
    assert result["renamed_references"]["request_rules"] == 1
    assert not rules.function_references("echo")
    assert rules.function_references("renamed_echo")
    # Persisted reload sees the same reference and captured-result semantics.
    loaded = rr.RequestRules(MemoryStore(await rules.async_backup_data()))
    await loaded.async_initialize()
    assert not loaded._diagnostics
    assert loaded.function_references("renamed_echo")
    agent._request_rules = loaded
    assert _speech(await _say(hass, agent, "run rule")) == "Value Keep This Case"
    with pytest.raises(HomeAssistantError, match="Request Rules"):
        await _manage_rule_boundary(
            hass, agent, "tools", "delete", name="renamed_echo", confirm=True
        )


@pytest.mark.parametrize(
    "template", ["{amount}", "{{ amount }}", "{{ request.slots.amount }}"]
)
@pytest.mark.parametrize("array", [False, True])
async def test_inferred_object_rule_arguments_save_reload_and_execute(
    hass, monkeypatch, template, array
):
    from copy import deepcopy

    from custom_components.extended_openai_conversation_responses import (
        request_rules as rr,
    )
    from homeassistant.exceptions import HomeAssistantError
    from tests.test_request_rules import MemoryStore
    from tests_real_ha.test_cross_feature_acceptance import _agent, _say, _speech
    from tests_real_ha.test_request_rules_script_semantics import _local

    values = {"dynamic": template, "fixed": 2}
    child = {
        "additionalProperties": {"type": "integer", "minimum": 1},
        "minProperties": 2,
    }
    if array:
        values, child = [values], {"items": child, "minItems": 1}
    tool = {
        "spec": {
            "name": "counts",
            "description": "Counts",
            "parameters": {
                "type": "object",
                "properties": {"values": child},
                "required": ["values"],
                "additionalProperties": False,
            },
        },
        "function": {
            "type": "template",
            "parse_result": True,
            "value_template": "{{ values }}",
        },
    }
    agent = await _agent(hass, functions=[tool])
    rule = _local(
        [
            {
                "action": "extended_openai_conversation_responses.CALL_FUNCTION",
                "data": {
                    "function": "counts",
                    "arguments": {"values": values},
                    "result_alias": "answer",
                },
            }
        ],
        phrase="count {amount=1..9}",
        match_type="sentence_pattern",
        success="Count {answer.0.dynamic}" if array else "Count {answer.dynamic}",
    )
    invalid = deepcopy(rule)
    invalid_value = invalid["action"]["actions"][0]["data"]["arguments"]["values"]
    (invalid_value[0] if array else invalid_value)["fixed"] = 0
    with pytest.raises(HomeAssistantError, match="at least 1"):
        await _manage_rule_boundary(
            hass, agent, "request_rules", "create", rule=invalid
        )
    await _manage_rule_boundary(hass, agent, "request_rules", "create", rule=rule)
    loaded = rr.RequestRules(
        MemoryStore(await agent._request_rules.async_backup_data())
    )
    await loaded.async_initialize()
    assert not loaded._diagnostics
    agent._request_rules = loaded
    assert _speech(await _say(hass, agent, "count 3")) == "Count 3"


@pytest.mark.parametrize("bound", [True, False])
async def test_azure_rule_snapshot_reports_underlying_capability_model(hass, bound):
    from homeassistant.components import conversation
    from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry

    entry = _make_entry(
        include_ai_task=False,
        data={"api_provider": "azure"},
        conversation_options={
            "chat_model": "home-deployment" if bound else "gpt-4.1",
            "azure_model": "gpt-5.6" if bound else "",
            "reasoning_effort": "low" if bound else None,
            "api_mode": "responses",
            "functions": [],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    result = await _manage_rule_boundary(hass, agent, "request_rules", "list")
    assert result["api_provider"] == "azure"
    assert result["effective_model"] == ("gpt-5.6" if bound else "gpt-4.1")
    assert result["azure_model"] == ("gpt-5.6" if bound else None)


@pytest.mark.parametrize("allowed", [False, True])
async def test_mixed_case_function_actions_preserve_guest_permissions(hass, allowed):
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
    )
    from homeassistant.setup import async_setup_component
    from tests_real_ha.test_cross_feature_acceptance import _agent, _say, _speech
    from tests_real_ha.test_request_rules_script_semantics import _local

    tool = {
        "spec": {
            "name": "public_switch",
            "description": "Switch",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        },
        "function": {"type": "native", "name": "execute_service_single"},
    }
    agent = await _agent(
        hass,
        functions=[tool],
        guest_policy_version=2,
        guest_function_policy="custom",
        guest_allowed_function_names=["public_switch"] if allowed else [],
    )
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"public_switch": {}}}
    )
    await hass.async_block_till_done()
    entity_id = "input_boolean.public_switch"
    async_expose_entity(hass, "conversation", entity_id, True)
    await agent._request_rules.async_create(
        _local(
            [
                {
                    "action": "EXTENDED_OPENAI_CONVERSATION_RESPONSES.CALL_FUNCTION",
                    "data": {
                        "function": "public_switch",
                        "arguments": {
                            "domain": "input_boolean",
                            "service": "turn_on",
                            "service_data": {"entity_id": entity_id},
                        },
                        "result_alias": "answer",
                    },
                }
            ],
            success="Permitted action",
        )
    )
    await agent._guest_mode.async_update_trusted(indefinite=True)
    result = await _say(hass, agent, "run rule")
    assert (result.response.error_code is None) is allowed
    assert hass.states.get(entity_id).state == ("on" if allowed else "off")
    if allowed:
        assert _speech(result) == "Permitted action"
