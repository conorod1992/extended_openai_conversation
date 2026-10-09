"""PR-time witnesses for the existing generated Function/Request Rule campaign."""

from copy import deepcopy
import json

from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from custom_components.extended_openai_conversation_responses.live_subentry_updates import (
    update_live_subentry,
)
from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SERVICE_CALL_FUNCTION,
    GUEST_POLICY_VERSION,
)
from tests.functions.behaviour_generators import execution_cases, assert_typed_value
from tests_real_ha.test_cross_feature_acceptance import (
    _agent,
    _provider,
    _speech,
)
from tests_real_ha.test_conversation_runtime_lifecycle import _tool
from tests_real_ha.test_request_rules_script_semantics import _local, _record_action


async def run_execution_workflows(hass, monkeypatch, cases):
    owner = await hass.auth.async_create_user(
        "Workflow owner", group_ids=["system-admin"]
    )
    hass.states.async_set("light.workflow_probe", "on")
    async_expose_entity(hass, "conversation", "light.workflow_probe", True)

    def record_action(marker):
        return {
            **_record_action(marker),
            "action": "light.turn_on",
            "target": {"entity_id": "light.workflow_probe"},
        }

    tools = []
    for index, case in enumerate(cases):
        inner = [
            {"type": "script", "sequence": [record_action(marker)]}
            for marker in (
                ["inner-last", "inner-first"]
                if case.reverse
                else ["inner-first", "inner-last"]
            )
        ]
        if case.failure != "none":
            inner.insert(
                0 if case.failure == "before" else 1,
                {
                    "type": "bash",
                    "command": "echo disabled",
                    "allow_unsafe_shell": False,
                },
            )
        # Guest control tools must be entity-scoped; templates are unscopable.
        if case.guest == "off":
            inner.append({"type": "template", "value_template": "{{ value }}"})
        tools.append(
            {
                "spec": {
                    "name": f"workflow_{index}",
                    "description": "Generated workflow probe",
                    "parameters": {
                        "type": "object",
                        "properties": {"value": {}},
                        "required": ["value"],
                    },
                },
                "guest_allowed": True,
                "function": {"type": "composite", "sequence": inner},
            }
        )
    agent = await _agent(
        hass,
        functions=tools,
        memory_mode="manual",
        memory_auto_retrieve_limit=0,
        archive_enabled=True,
        guest_policy_version=GUEST_POLICY_VERSION,
        guest_function_policy="on",
        voice_scope_policy="device_mapping",
        voice_unmapped_policy="default_user",
        voice_default_user_id=owner.id,
        voice_device_mappings={"workflow-device": owner.id},
    )
    await agent._memory.async_add(
        owner.id,
        "Private workflow calibration token: workflow-cobalt.",
        "calibration",
        "explicit",
    )
    effects, captured = [], []

    async def observe(call):
        if call.data.get("message") == "captured":
            captured.append(call.data["value"])
        else:
            effects.append(call.data["message"])

    from tests_real_ha.entity_service_probe import register_entity_service_probe
    register_entity_service_probe(hass, "light", "turn_on", observe)

    async def say(text, conversation_id=None):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=conversation_id,
            context=Context(),
            language="en",
            agent_id=agent.entry.entry_id,
            device_id="workflow-device",
        )

    evidence = []
    for index, case in enumerate(cases):
        effects.clear()
        captured.clear()
        update_live_subentry(
            hass,
            agent.entry,
            agent.subentry,
            data={
                **agent.subentry.data,
                "voice_device_mappings": {
                    "workflow-device": "unretained" if case.unretained else owner.id
                },
                "guest_function_policy": "off" if case.guest == "denied" else "on",
                "advanced_options": bool(index % 2),
            },
        )
        if case.guest == "off":
            await agent._guest_mode.async_disable_trusted()
        else:
            await agent._guest_mode.async_update_trusted(indefinite=True)
        capture = {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "continue_on_error": case.continuation,
            "data": {
                "function": f"workflow_{index}",
                "arguments": {"value": deepcopy(case.value)},
            },
        }
        # A failed service has no response to capture, even with native continuation.
        if case.failure == "none" and case.guest == "off":
            capture["data"]["result_alias"] = "answer"
        actions = [record_action("outer-first"), capture]
        if case.failure == "none" and case.guest == "off":
            actions.append(
                {
                    "action": "light.turn_on",
                    "target": {"entity_id": "light.workflow_probe"},
                    "data": "{{ {'message': 'captured', 'value': answer} }}",
                }
            )
        actions.append(record_action("outer-last"))
        await agent._request_rules.async_create(
            _local(actions, phrase=f"workflow {index}")
        )
        _provider(monkeypatch, agent, [])
        archive_before = agent._archive.stats()["turn_count"]
        result = await say(f"workflow {index}")
        assert effects == case.effects, (index, case, effects)
        if case.failure == "none" and case.guest == "off":
            assert len(captured) == 1
            assert_typed_value(captured[0], case.value)
        else:
            assert captured == []
        # Modify an unrelated configuration field, then continue the same session.
        update_live_subentry(
            hass,
            agent.entry,
            agent.subentry,
            data={**agent.subentry.data, "shorten_tool_call_id": bool(index % 2)},
        )
        sent = _provider(
            monkeypatch,
            agent,
            [
                *(
                    []
                    if (case.guest != "off" or case.unretained)
                    else [
                        _tool(
                            "memory_search",
                            {
                                "query": "workflow calibration",
                                "scope": "personal",
                                "limit": 5,
                            },
                        )
                    ]
                ),
                "Memory reviewed.",
            ],
        )
        continued = await say("Read workflow calibration", result.conversation_id)
        assert continued.response.error_code is None, (
            index,
            case,
            continued.response.as_dict(),
        )
        assert _speech(continued) == "Memory reviewed."
        assert len(sent) == (1 if case.guest != "off" or case.unretained else 2)
        body = json.dumps(sent[-1])
        assert ("workflow-cobalt" in body) is (
            case.guest == "off" and not case.unretained
        ), (index, case)
        if case.guest != "off" or case.unretained:
            assert agent._archive.stats()["turn_count"] == archive_before
        assert len(await agent._memory.async_list(owner.id)) == 1
        evidence.append(
            {
                "case": index,
                "failure": case.failure,
                "continuation": case.continuation,
                "guest": case.guest,
                "unretained": case.unretained,
                "result_type": type(case.value).__name__,
                "effects": len(effects),
            }
        )
    return evidence


async def test_generated_execution_and_privacy_pr_witnesses(hass, monkeypatch):
    evidence = await run_execution_workflows(
        hass, monkeypatch, execution_cases(20261008, 7)
    )
    assert len(evidence) == 7
