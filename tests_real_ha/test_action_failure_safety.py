"""Action failures stop real rules and finalize local runs accurately."""

import asyncio
import json
import os
import shlex
import subprocess
import sys

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import (
    conversation as conversation_owner,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_FUNCTION_TOOLS,
    EVENT_CONVERSATION_FINISHED,
)
from custom_components.extended_openai_conversation_responses.functions import (
    BashFunction,
)
from custom_components.extended_openai_conversation_responses.local_intents import (
    CONF_LOCAL_INTENTS_ENABLED,
    LocalIntentResult,
)
from homeassistant.auth.const import GROUP_ID_ADMIN
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent
from tests_real_ha.test_cross_feature_acceptance import (
    _agent,
    _provider,
    _rule,
    _speech,
)
from tests_real_ha.test_entry_point_contract_matrix import _contract_agent, _management
from tests_real_ha.test_provider_input_history import _transport
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _chat_sse_tool_call


def _python_command(code):
    arguments = [sys.executable, "-c", code]
    return (
        subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)
    )


def _bash_step(directory, code):
    return {
        "type": "bash",
        "command": _python_command(code),
        "cwd": str(directory),
        "allow_unsafe_shell": True,
        "restrict_to_workspace": False,
    }


def _bash_workflow(directory, outcome):
    first = _bash_step(directory, "print('prepared')")
    if outcome == "timeout":
        first = _bash_step(directory, "import time; time.sleep(5)")
    elif outcome == "launch":
        first["cwd"] = str(directory / "missing-directory")
    elif outcome == "nonzero":
        first["command"] = _python_command("import sys; sys.exit(7)")
    elif outcome == "business":
        first = {
            "type": "template",
            "value_template": "{{ {'error': 'application data'} }}",
            "parse_result": True,
        }
    return {
        "type": "composite",
        "sequence": [
            first,
            _bash_step(
                directory,
                "from pathlib import Path; Path('later.txt').write_text('executed'); print('published')",
            ),
        ],
    }


@pytest.mark.parametrize("route", ["model", "rule"])
@pytest.mark.parametrize(
    "outcome", ["timeout", "launch", "success", "nonzero", "business"]
)
async def test_saved_bash_composite_stops_later_step_only_on_execution_failure(
    hass, tmp_path, route, outcome
):
    implementation = _bash_workflow(tmp_path, outcome)
    tool = {
        "spec": {
            "name": "bash_workflow",
            "description": "Prepare then publish",
            "parameters": {
                "type": "object",
                "properties": {"timeout": {"type": "number"}},
                "required": ["timeout"],
            },
        },
        "function": implementation,
    }
    agent = await _contract_agent(
        hass, memory_mode="off", function_tool_error_recovery=False
    )
    command = await _management(hass, agent)
    current = await command("get")
    saved = await command(
        "save", config={"functions": [tool]}, revision=current["revision"]
    )
    assert saved["valid"], saved
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, agent.entry.entry_id)
    failed = outcome in {"timeout", "launch"}
    if route == "rule":
        await agent._request_rules.async_create(
            _rule(
                "local_action",
                {
                    "actions": [
                        {
                            "type": "function",
                            "function": "bash_workflow",
                            "arguments": {"timeout": 1},
                        }
                    ],
                    "success_response": "Published",
                    "failure_response": "Preparation failed",
                },
                phrase="run workflow",
            )
        )
        replies = []
    else:
        replies = [
            _chat_sse_tool_call("bash-workflow-call", "bash_workflow", {"timeout": 1}),
            _chat_sse_text("Stopped" if failed else "Published"),
        ]
    async with _transport(agent.entry, replies) as wire:
        result = await _authenticated_say(hass, agent, "run workflow", is_admin=True)
        wire.assert_complete(len(replies))
    assert (tmp_path / "later.txt").exists() is (not failed)
    if route == "rule":
        assert _speech(result, successful=not failed) == (
            "Preparation failed" if failed else "Published"
        )
        assert agent._usage.runs[-1].successful is (not failed)
    else:
        assert _speech(result) == ("Stopped" if failed else "Published")
        output = next(
            json.loads(item["content"])
            for item in wire.requests[1]["body"]["messages"]
            if item.get("role") == "tool"
        )
        if failed:
            assert output["result"]["status"] == "error", output
            assert output["result"]["error"]
            assert "published" not in str(output)
            if outcome == "timeout":
                assert "Command timed out after 1 seconds" in str(output)
            else:
                assert "missing-directory" in str(output)
        else:
            assert output["result"]["exit_code"] == 0
            assert "published" in output["result"]["stdout"]


@pytest.mark.parametrize("outcome", ["timeout", "launch"])
async def test_direct_bash_execution_failure_keeps_legacy_error_response(
    hass, tmp_path, outcome
):
    function = BashFunction()
    config = function.validate_schema(_bash_workflow(tmp_path, outcome)["sequence"][0])
    result = await function.execute(hass, config, {"timeout": 1}, None, [])
    assert set(result) == {"error"}
    if outcome == "timeout":
        assert result["error"] == "Command timed out after 1 seconds"
    else:
        assert "missing-directory" in result["error"]


async def test_origin_only_targeted_broadcast_is_handled_failure(hass, monkeypatch):
    from custom_components.extended_openai_conversation_responses.const import (
        SERVICE_PROCESS,
    )
    from custom_components.extended_openai_conversation_responses.intercom import (
        ANNOUNCE_FEATURE,
        async_get_intercom,
    )
    from homeassistant.const import ATTR_FRIENDLY_NAME, ATTR_SUPPORTED_FEATURES
    from homeassistant.helpers import area_registry as ar, entity_registry as er
    from tests_real_ha.test_service_registry_acceptance import (
        _conversation_entity_id,
        _response_service_call,
    )

    agent = await _agent(
        hass, **{CONF_LOCAL_INTENTS_ENABLED: True, CONF_ARCHIVE_ENABLED: True}
    )
    sent = _provider(monkeypatch, agent, ["Unexpected fallback"])
    manager = await async_get_intercom(hass)
    await manager.async_set_enabled(True)
    area = ar.async_get(hass).async_create("Kitchen")
    satellite = er.async_get(hass).async_get_or_create(
        "assist_satellite", "audit_probe", "kitchen"
    )
    er.async_get(hass).async_update_entity(satellite.entity_id, area_id=area.id)
    hass.states.async_set(
        satellite.entity_id,
        "responding",
        {
            ATTR_FRIENDLY_NAME: "Kitchen Voice",
            ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE,
        },
    )
    user = MockUser(id="broadcast-user", name="Broadcast User")
    user.add_to_hass(hass)
    finished = []
    hass.bus.async_listen(EVENT_CONVERSATION_FINISHED, finished.append)
    result = await _response_service_call(
        hass,
        SERVICE_PROCESS,
        {
            "text": "broadcast to kitchen dinner is ready",
            "agent_id": _conversation_entity_id(hass, agent.entry),
            "satellite_id": satellite.entity_id,
            "language": "en",
        },
        user_id=user.id,
    )
    await hass.async_block_till_done()
    assert "No matching announcement-capable" in str(result)
    assert sent == []
    assert manager.history() == []
    assert len(agent._usage.runs) == 1
    assert agent._usage.runs[0].successful is False
    assert len(finished) == 1
    assert finished[0].data["status"] == "error"
    turns = [turn for values in agent._archive._turns.values() for turn in values]
    assert len(turns) == 1
    assert turns[0].successful is False


async def _authenticated_say(hass, agent, text, *, is_admin=False):
    if is_admin:
        user = await hass.auth.async_create_user(
            "Action Admin", group_ids=[GROUP_ID_ADMIN]
        )
    else:
        user = MockUser(id="action-user", name="Action User")
        user.add_to_hass(hass)
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(user_id=user.id),
        language="en",
        agent_id=agent.entry.entry_id,
    )


@pytest.mark.parametrize("kind", ["raise", "error", "success", "unmatched"])
async def test_local_dispatch_has_one_accurate_run_and_completion(
    hass, monkeypatch, kind
):
    agent = await _agent(
        hass, **{CONF_LOCAL_INTENTS_ENABLED: True, CONF_ARCHIVE_ENABLED: True}
    )
    sent = _provider(monkeypatch, agent, ["Provider reply"])
    response = intent.IntentResponse(language="en")
    if kind == "error":
        response.async_set_error(
            intent.IntentResponseErrorCode.UNKNOWN, "Local failure"
        )
    else:
        response.async_set_speech("Local reply")

    async def handle(*args, **kwargs):
        await asyncio.sleep(0.02)
        if kind == "raise":
            raise HomeAssistantError("Local failure")
        return (
            None
            if kind == "unmatched"
            else LocalIntentResult(response=response, intent_name="AuditLocalIntent")
        )

    monkeypatch.setattr(conversation_owner, "async_try_handle_local_intent", handle)
    finished = []
    hass.bus.async_listen(EVENT_CONVERSATION_FINISHED, finished.append)
    result = await _authenticated_say(hass, agent, "test local handling")
    await hass.async_block_till_done()
    failed = kind in {"raise", "error"}
    assert bool(result.response.error_code) == failed
    if kind != "raise" and kind != "unmatched":
        assert result.response is response
    assert len(sent) == (1 if kind == "unmatched" else 0)
    assert len(agent._usage.runs) == 1
    run = agent._usage.runs[0]
    assert run.successful is not failed
    assert run.duration_ms >= 15
    turns = [turn for values in agent._archive._turns.values() for turn in values]
    assert len(turns) == 1
    assert turns[0].successful is not failed
    assert len(finished) == 1
    if failed:
        assert finished[0].data["status"] == "error"
        assert finished[0].data["handled_locally"] is True


@pytest.mark.parametrize("capture", [False, True])
@pytest.mark.parametrize("composite", [False, True])
@pytest.mark.parametrize("backend", ["native", "file", "business"])
async def test_real_rule_stops_after_backend_failure(
    hass, monkeypatch, capture, composite, backend
):
    effects = []

    async def record(call):
        effects.append(call.data["marker"])

    async def fail(call):
        raise HomeAssistantError("Controlled service failure")

    hass.services.async_register("audit_probe", "record", record)
    hass.services.async_register("light", "turn_on", fail)
    hass.states.async_set("light.audit", "off")
    async_expose_entity(hass, "conversation", "light.audit", True)
    function = {
        "native": {"type": "native", "name": "execute_service"},
        "file": {"type": "read_file", "path": "missing-audit-file.txt"},
        "business": {
            "type": "template",
            "value_template": "{{ {'error': 'business value'} }}",
            "parse_result": True,
        },
    }[backend]
    if composite:
        function = {
            "type": "composite",
            "sequence": [
                function,
                {
                    "type": "script",
                    "sequence": [
                        {
                            "action": "audit_probe.record",
                            "data": {"marker": "inside_after"},
                        }
                    ],
                },
            ],
        }
    tool = {
        "spec": {
            "name": "audit_function",
            "description": "Audit action",
            "parameters": {
                "type": "object",
                "properties": {"list": {"type": "array", "items": {"type": "object"}}},
            },
        },
        "function": function,
    }
    agent = await _agent(hass, **{CONF_FUNCTION_TOOLS: [tool]})
    sent = _provider(monkeypatch, agent, ["Unexpected provider fallback"])
    arguments = (
        {
            "list": [
                {
                    "domain": "light",
                    "service": "turn_on",
                    "service_data": {"entity_id": "light.audit"},
                }
            ]
        }
        if backend == "native"
        else {}
    )
    action = {"type": "function", "function": "audit_function", "arguments": arguments}
    if capture:
        action["result_alias"] = "captured"
    await agent._request_rules.async_create(
        _rule(
            "local_action",
            {
                "actions": [
                    {"action": "audit_probe.record", "data": {"marker": "before"}},
                    action,
                    {"action": "audit_probe.record", "data": {"marker": "after"}},
                ],
                "success_response": "Done",
                "failure_response": "Failed",
            },
            phrase="run audit",
        )
    )
    # The probe is a global service with no entity target, so its caller must
    # be allowed to authorize targetless actions. Restricted Script callers
    # are exercised separately by the user-permission acceptance tests.
    result = await _authenticated_say(hass, agent, "run audit", is_admin=True)
    assert _speech(result, successful=backend == "business") == (
        "Done" if backend == "business" else "Failed"
    )
    assert effects == (
        ["before", "inside_after", "after"]
        if composite and backend == "business"
        else ["before", "after"]
        if backend == "business"
        else ["before"]
    )
    assert len(agent._usage.runs) == 1
    assert agent._usage.runs[0].successful is (backend == "business")
    assert sent == []
