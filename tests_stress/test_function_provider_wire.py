"""On-demand Function Group loads and executes a real local tool on SDK wire."""

from __future__ import annotations

import asyncio
import json

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_tool_call,
    _chat_tool_result,
    _tool_names,
)
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _raw_client,
    _speech,
)
from tests_stress.conftest import record


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
@pytest.mark.parametrize(
    "case",
    [
        "integer-duplicate",
        "boolean-duplicate",
        "object-duplicate",
        "enum-boolean",
        "const-boolean",
        "healthy-mixed",
    ],
)
async def test_normalized_arguments_guard_public_function_dispatch(
    hass, monkeypatch, stress_trace, api_mode, case
):
    """Invalid normalized arguments never reach an actual native service action."""
    from jsonschema import Draft202012Validator

    from tests_real_ha.test_provider_wire_e2e import (
        _responses_sse_text,
        _responses_sse_tool_call,
    )
    from tests_stress.test_function_provider_wire_remaining import _provider_result

    if case == "integer-duplicate":
        field = {"type": "array", "items": {"type": "integer"}, "uniqueItems": True}
        invalid, healthy, expected = ["01", 1], ["01", "2"], [1, 2]
    elif case == "boolean-duplicate":
        field = {"type": "array", "items": {"type": "boolean"}, "uniqueItems": True}
        invalid, healthy, expected = ["false", False], ["false", "true"], [False, True]
    elif case == "object-duplicate":
        field = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"n": {"type": "integer"}},
                "required": ["n"],
                "additionalProperties": False,
            },
            "uniqueItems": True,
        }
        invalid, healthy, expected = (
            [{"n": "01"}, {"n": 1}],
            [{"n": "01"}, {"n": "2"}],
            [{"n": 1}, {"n": 2}],
        )
    elif case in {"enum-boolean", "const-boolean"}:
        keyword = case.split("-")[0]
        field = {
            "type": ["integer", "boolean"],
            keyword: [0] if keyword == "enum" else 0,
        }
        invalid, healthy, expected = False, "0", 0
    else:
        field = {
            "type": "array",
            "items": {"type": ["integer", "boolean"]},
            "uniqueItems": True,
        }
        invalid = healthy = expected = [0, False]
    schema = {
        "type": "object",
        "properties": {"values": field},
        "required": ["values"],
        "additionalProperties": False,
    }
    tool = {
        "spec": {
            "name": "normalized_values",
            "description": "Record validated values",
            "parameters": schema,
        },
        "function": {
            "type": "script",
            "sequence": [
                {
                    "action": "normalized_probe.record",
                    "data": {"values": "{{ values }}"},
                }
            ],
        },
    }
    effects = []

    async def effect(call):
        effects.append(dict(call.data))

    hass.services.async_register("normalized_probe", "record", effect)
    owner = await hass.auth.async_create_user("Normalization owner")
    entry = _make_entry(
        "Normalized Function validation",
        include_ai_task=False,
        conversation_options={CONF_API_MODE: api_mode, CONF_FUNCTION_TOOLS: [tool]},
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)

    async def say(value, call_id):
        call = (
            _responses_sse_tool_call if api_mode == "responses" else _chat_sse_tool_call
        )(call_id, "normalized_values", {"values": value})
        text = _responses_sse_text if api_mode == "responses" else _chat_sse_text
        wire = _install_wire(
            monkeypatch, agent, [call, text("Validated values recorded")]
        )
        result = await conversation.async_converse(
            hass=hass,
            text="Record these values",
            conversation_id=None,
            context=Context(user_id=owner.id),
            language="en",
            agent_id=entry.entry_id,
        )
        return result, wire

    result, wire = await say(invalid, "initial-normalization")
    if case != "healthy-mixed":
        assert result.response.error_code is not None
        assert result.response.as_dict()["speech"]["plain"]["speech"]
        assert effects == []
        assert len(wire.requests) == 1, "Rejected arguments must not execute or replay"
    else:
        assert result.response.error_code is None
        assert _speech(result) == "Validated values recorded"
        assert effects == [{"values": expected}]
        Draft202012Validator(schema).validate(effects[0])
    result, wire = await say(healthy, "healthy-normalization")
    assert result.response.error_code is None
    assert _speech(result) == "Validated values recorded"
    assert effects == [{"values": expected}] * (2 if case == "healthy-mixed" else 1)
    for data in effects:
        Draft202012Validator(schema).validate(data)
    assert (
        _provider_result(wire.requests[1], api_mode, "healthy-normalization")
        == "Success"
    )
    record(
        stress_trace,
        "summary",
        normalized_schema_dispatch_cases=1,
        normalized_schema_rejections=int(case != "healthy-mixed"),
        normalized_schema_healthy_dispatches=1,
    )


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("outcome", ["valid", "bad-pattern", "bad-const", "bad-enum"])
async def test_schema_pattern_names_and_literals_survive_public_function_execution(
    hass, monkeypatch, stress_trace, api_mode, outcome
):
    from copy import deepcopy

    from custom_components.extended_openai_conversation_responses import regex_execution
    from tests_real_ha.test_provider_wire_e2e import (
        _responses_sse_text,
        _responses_sse_tool_call,
    )
    from tests_stress.test_function_provider_wire_remaining import _provider_result

    literal = {"pattern": "keep", "nested": {"pattern": "preserve"}}
    schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "label": {"type": "string", "pattern": "^ok$"},
            "payload": {
                "type": "object",
                "const": literal,
                "properties": {
                    "pattern": {"type": "string"},
                    "nested": {
                        "type": "object",
                        "properties": {"pattern": {"type": "string"}},
                        "required": ["pattern"],
                        "additionalProperties": False,
                    },
                },
                "required": ["pattern", "nested"],
                "additionalProperties": False,
            },
            "choice": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
                "additionalProperties": False,
                "enum": [{"pattern": "keep"}, {"pattern": "alternative"}],
            },
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"pattern": {"type": "string", "pattern": "^ok$"}},
                    "required": ["pattern"],
                    "additionalProperties": False,
                },
            },
            "count": {"type": "integer"},
            "enabled": {"type": "boolean"},
        },
        "required": [
            "pattern",
            "label",
            "payload",
            "choice",
            "items",
            "count",
            "enabled",
        ],
        "additionalProperties": False,
    }
    tool = {
        "spec": {
            "name": "schema_payload",
            "description": "Record validated schema data",
            "parameters": schema,
        },
        "function": {
            "type": "script",
            "sequence": [
                {
                    "action": "schema_probe.record",
                    "data": {
                        field: "{{ " + field + " }}" for field in schema["properties"]
                    },
                }
            ],
        },
    }
    effects = []

    async def effect(call):
        effects.append(dict(call.data))

    hass.services.async_register("schema_probe", "record", effect)
    owner = await hass.auth.async_create_user("Schema owner")
    entry = _make_entry(
        "Schema boundaries",
        include_ai_task=False,
        conversation_options={CONF_API_MODE: api_mode, CONF_FUNCTION_TOOLS: [tool]},
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    regex_calls = []
    bounded_search = regex_execution.async_search_configured_patterns

    async def observed_search(hass, checks):
        regex_calls.append(checks)
        return await bounded_search(hass, checks)

    monkeypatch.setattr(
        regex_execution, "async_search_configured_patterns", observed_search
    )
    healthy_args = {
        "pattern": "business name",
        "label": "ok",
        "payload": literal,
        "choice": {"pattern": "keep"},
        "items": [{"pattern": "ok"}],
        "count": "0",
        "enabled": False,
    }
    expected = {**healthy_args, "count": 0}
    args = deepcopy(healthy_args)
    if outcome == "bad-pattern":
        args["items"][0]["pattern"] = "bad"
    elif outcome == "bad-const":
        args["payload"]["nested"]["pattern"] = "wrong"
    elif outcome == "bad-enum":
        args["choice"]["pattern"] = "unknown"

    async def say(arguments, call_id):
        call = (
            _responses_sse_tool_call(call_id, "schema_payload", arguments)
            if api_mode == "responses"
            else _chat_sse_tool_call(call_id, "schema_payload", arguments)
        )
        text = _responses_sse_text if api_mode == "responses" else _chat_sse_text
        wire = _install_wire(monkeypatch, agent, [call, text("Schema result handled")])
        result = await conversation.async_converse(
            hass=hass,
            text="Execute the schema tool",
            conversation_id=None,
            context=Context(user_id=owner.id),
            language="en",
            agent_id=entry.entry_id,
        )
        advertised = next(
            item if api_mode == "responses" else item["function"]
            for item in wire.requests[0]["body"]["tools"]
            if (item if api_mode == "responses" else item["function"])["name"]
            == "schema_payload"
        )["parameters"]
        assert advertised["properties"]["pattern"] == schema["properties"]["pattern"]
        assert advertised["properties"]["payload"]["const"] == literal
        assert (
            advertised["properties"]["choice"]["enum"]
            == schema["properties"]["choice"]["enum"]
        )
        return result, wire

    initial, wire = await say(args, "initial-schema")
    if outcome == "valid":
        assert initial.response.error_code is None
        assert effects == [expected]
        assert (
            _provider_result(wire.requests[1], api_mode, "initial-schema") == "Success"
        )
    else:
        assert initial.response.error_code is not None
        assert effects == []
        assert len(wire.requests) == 1
    healthy, wire = await say(healthy_args, "healthy-schema")
    assert healthy.response.error_code is None
    assert effects == [expected] * (2 if outcome == "valid" else 1)
    assert _provider_result(wire.requests[1], api_mode, "healthy-schema") == "Success"
    assert len(regex_calls) == (2 if outcome in {"valid", "bad-pattern"} else 1)
    assert all(len(checks) == 2 for checks in regex_calls)
    record(
        stress_trace,
        "summary",
        schema_pattern_boundary_cases=1,
        schema_contract_rejections=int(outcome != "valid"),
        schema_healthy_tool_recoveries=1,
        schema_bounded_regex_exchanges=len(regex_calls),
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "edit",
        "delete_recreate_changed",
        "delete_recreate_same",
        "tool_aba",
        "group_rebind",
        "group_aba",
        "tool_group_aba",
    ],
)
async def test_stale_function_identity_is_not_rebound_after_provider_reply(
    hass: HomeAssistant, monkeypatch, stress_trace: list[dict], mutation: str
) -> None:
    """Provider arguments advertised against A never dispatch another generation."""
    name = "stale_provider_tool"
    original = {
        "spec": {
            "name": name,
            "description": "Original",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "ORIGINAL"},
        "enabled": True,
    }
    edited = {
        **original,
        "spec": {**original["spec"], "description": "Edited"},
        "function": {"type": "template", "value_template": "MUST_NOT_EXECUTE"},
    }
    group_a = {
        "id": "stale-provider-group-a",
        "name": "Original group",
        "description": "Original binding",
        "loading_mode": "always",
        "functions": [name],
        "enabled": True,
    }
    group_b = {
        **group_a,
        "id": "stale-provider-group-b",
        "name": "Replacement group",
    }
    grouped = mutation.startswith("group") or mutation == "tool_group_aba"
    entry = _make_entry(
        "Stale provider tool",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_FUNCTION_TOOLS: [original],
            CONF_FUNCTION_GROUPS: [group_a] if grouped else [],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call("stale-call", name, {}),
            _chat_sse_text("unexpected continuation"),
        ],
    )
    send = wire.send
    entered, release = asyncio.Event(), asyncio.Event()

    async def suspended_send(request, *args, **kwargs):
        if not wire.requests:
            entered.set()
            await release.wait()
        return await send(request, *args, **kwargs)

    monkeypatch.setattr(_raw_client(agent)._client, "send", suspended_send)
    turn = asyncio.create_task(
        conversation.async_converse(
            hass=hass,
            text="Call the tool",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=10)
    subentry = next(iter(entry.subentries.values()))
    original_data = subentry.data

    def replace(tools: list[dict], groups: list[dict]) -> None:
        current = next(iter(entry.subentries.values()))
        hass.config_entries.async_update_subentry(
            entry,
            current,
            data={
                **current.data,
                CONF_FUNCTION_TOOLS: tools,
                CONF_FUNCTION_GROUPS: groups,
            },
        )

    if mutation.startswith("delete_recreate"):
        replace([], [])
        replace([original if mutation.endswith("same") else edited], [])
    elif mutation == "tool_aba":
        replace([edited], [])
        replace([original], [])
    elif mutation == "group_rebind":
        replace([original], [group_b])
    elif mutation == "group_aba":
        replace([original], [group_b])
        replace([original], [group_a])
    elif mutation == "tool_group_aba":
        replace([edited], [group_b])
        replace([original], [group_a])
    else:
        replace([edited], [])
    latest_data = next(iter(entry.subentries.values())).data
    assert latest_data is not original_data
    if mutation in {"delete_recreate_same", "tool_aba", "group_aba", "tool_group_aba"}:
        assert latest_data == original_data
    release.set()
    result = await asyncio.wait_for(turn, timeout=10)
    assert result.response.error_code is not None
    assert len(wire.requests) == 1
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    record(
        stress_trace,
        "summary",
        layer="real SDK wire",
        mutation=mutation,
        stale_tool_rejections=1,
    )


async def test_group_load_tool_execution_result_and_session_isolation(
    hass: HomeAssistant,
    monkeypatch,
    stress_trace: list[dict],
) -> None:
    tool_name = "enhanced_local_status"
    group_id = "enhanced-status-group"
    entry = _make_entry(
        "Enhanced function execution",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_FUNCTION_TOOLS: [
                {
                    "spec": {
                        "name": tool_name,
                        "description": "Return the local status marker",
                        "parameters": {"type": "object", "properties": {}},
                    },
                    "function": {
                        "type": "template",
                        "value_template": "LOCAL-TOOL-RESULT-東京",
                    },
                    "enabled": True,
                }
            ],
            CONF_FUNCTION_GROUPS: [
                {
                    "id": group_id,
                    "name": "Enhanced status",
                    "description": "Nightly local result",
                    "loading_mode": "on_demand",
                    "functions": [tool_name],
                    "enabled": True,
                }
            ],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "call-load", "load_function_groups", {"groups": [group_id]}
            ),
            _chat_sse_tool_call("call-status", tool_name, {}),
            _chat_sse_text("Status delivered"),
            _chat_sse_text("Fresh session"),
        ],
    )

    async def say(text: str, conversation_id=None):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=conversation_id,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )

    first = await say("Load and execute the local status tool")
    assert _speech(first) == "Status delivered"
    assert len(wire.requests) == 3
    names = [
        _tool_names(request["body"], API_MODE_CHAT_COMPLETIONS)
        for request in wire.requests
    ]
    assert "load_function_groups" in names[0] and tool_name not in names[0]
    assert tool_name in names[1] and tool_name in names[2]
    loaded = _chat_tool_result(wire.requests[1]["body"], "call-load")
    assert loaded["status"] == "success"
    tool_message = next(
        item
        for item in wire.requests[2]["body"]["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == "call-status"
    )
    executed = json.loads(tool_message["content"])
    assert executed["result"] == "LOCAL-TOOL-RESULT-東京"

    other = await say("Start a separate conversation")
    assert _speech(other) == "Fresh session"
    assert other.conversation_id != first.conversation_id
    assert tool_name not in _tool_names(
        wire.requests[3]["body"], API_MODE_CHAT_COMPLETIONS
    )
    record(
        stress_trace,
        "summary",
        layer="provider-wire",
        public_turns=2,
        provider_requests=4,
        actual_tool_executions=2,
        local_function_executions=1,
        template_function_executions=1,
    )


async def test_script_function_executes_local_ha_service_on_provider_wire(
    hass: HomeAssistant,
    monkeypatch,
    stress_trace: list[dict],
) -> None:
    tool_name = "enhanced_script_wire"
    entity_id = "light.enhanced_script_wire"
    calls = []

    async def turn_off(call):
        calls.append(call)

    hass.services.async_register("light", "turn_off", turn_off)
    hass.states.async_set(entity_id, "on")
    entry = _make_entry(
        "Enhanced script provider execution",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_FUNCTION_TOOLS: [
                {
                    "spec": {
                        "name": tool_name,
                        "description": "Turn off one local fixture light",
                        "parameters": {"type": "object", "properties": {}},
                    },
                    "function": {
                        "type": "script",
                        "sequence": [
                            {
                                "action": "light.turn_off",
                                "data": {"entity_id": entity_id},
                            }
                        ],
                    },
                    "enabled": True,
                }
            ],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call("call-enhanced-script", tool_name, {}),
            _chat_sse_text("Script executed"),
        ],
    )
    result = await conversation.async_converse(
        hass=hass,
        text="Run the local script",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )
    assert _speech(result) == "Script executed"
    assert len(wire.requests) == 2
    assert tool_name in _tool_names(wire.requests[0]["body"], API_MODE_CHAT_COMPLETIONS)
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == entity_id
    message = next(
        item
        for item in wire.requests[1]["body"]["messages"]
        if item.get("role") == "tool"
        and item.get("tool_call_id") == "call-enhanced-script"
    )
    assert "result" in json.loads(message["content"])
    record(
        stress_trace,
        "summary",
        layer="provider-wire",
        public_turns=1,
        provider_requests=2,
        actual_function_executions=1,
        script_function_executions=1,
        ha_service_calls=1,
    )


@pytest.mark.parametrize(
    "safe_batch", [False, True], ids=["serial-prefix", "safe-atomic"]
)
async def test_public_budget_loading_multicall_and_completed_replay(
    hass, monkeypatch, stress_trace, safe_batch
):
    """Loader exemption and request-local limits compose with completed-call refusal."""
    from custom_components.extended_openai_conversation_responses.const import (
        CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    )
    from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
        is_tool_result_content,
    )
    from tests_stress.test_function_provider_wire_remaining import _multicall_reply
    from pytest_homeassistant_custom_component.common import MockUser

    owner = MockUser(id="budget-owner", name="Budget owner", is_owner=True).add_to_hass(
        hass
    )
    effects, results = [], []

    async def effect(call):
        effects.append(call.data["marker"])

    hass.services.async_register("budget_probe", "record", effect)
    add = conversation.ChatLog.async_add_assistant_content_without_tools

    def capture(log, content):
        if is_tool_result_content(content):
            results.append(content.tool_call_id)
        add(log, content)

    monkeypatch.setattr(
        conversation.ChatLog, "async_add_assistant_content_without_tools", capture
    )
    tools = []
    for marker in ("warm", "a", "b"):
        function = {
            "type": "script",
            "sequence": [{"action": "budget_probe.record", "data": {"marker": marker}}],
        }
        if safe_batch and marker != "warm":
            function = {"type": "native", "name": "get_user_from_user_id"}
        tools.append(
            {
                "spec": {
                    "name": f"budget_{marker}",
                    "description": "Budget boundary probe",
                    "parameters": {"type": "object", "properties": {}},
                },
                "function": function,
            }
        )
    group_id = "budget-group"
    entry = _make_entry(
        "Budget composition",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION: 2,
            CONF_FUNCTION_TOOLS: tools,
            CONF_FUNCTION_GROUPS: [
                {
                    "id": group_id,
                    "name": "Budget group",
                    "description": "Load local probes",
                    "loading_mode": "on_demand",
                    "functions": [tool["spec"]["name"] for tool in tools],
                    "enabled": True,
                }
            ],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)

    async def say(cid=None):
        return await conversation.async_converse(
            hass=hass,
            text="Use budget probes",
            conversation_id=cid,
            context=Context(user_id=owner.id),
            language="en",
            agent_id=entry.entry_id,
        )

    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call("load", "load_function_groups", {"groups": [group_id]}),
            _chat_sse_tool_call("warm", "budget_warm", {}),
            _multicall_reply(
                API_MODE_CHAT_COMPLETIONS,
                [("batch-a", "budget_a", {}), ("batch-b", "budget_b", {})],
            ),
        ],
    )
    failed = await say()
    assert failed.response.error_code is not None
    assert "Function call limit" in str(failed.response.as_dict())
    assert len(wire.requests) == 3
    assert effects == (["warm"] if safe_batch else ["warm", "a"])
    assert results == ["load", "warm", "batch-a", "batch-b"]
    # A fresh turn gets a fresh budget; a completed acknowledged id still cannot replay.
    _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "fresh-load", "load_function_groups", {"groups": [group_id]}
            ),
            _chat_sse_tool_call("completed", "budget_warm", {}),
            _chat_sse_text("Acknowledged"),
        ],
    )
    acknowledged = await say()
    assert _speech(acknowledged) == "Acknowledged"
    before = list(effects)
    _install_wire(
        monkeypatch, agent, [_chat_sse_tool_call("completed", "budget_warm", {})]
    )
    replayed = await say(acknowledged.conversation_id)
    assert replayed.response.error_code is not None
    assert "completed tool call" in str(replayed.response.as_dict())
    assert effects == before
    _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "last-load", "load_function_groups", {"groups": [group_id]}
            ),
            _multicall_reply(
                API_MODE_CHAT_COMPLETIONS,
                [("fresh-a", "budget_a", {}), ("fresh-b", "budget_b", {})],
            ),
            _chat_sse_text("Fresh budget healthy"),
        ],
    )
    assert _speech(await say()) == "Fresh budget healthy"
    assert effects == (before if safe_batch else before + ["a", "b"])
    record(
        stress_trace,
        "summary",
        layer="provider-wire",
        budget_loading_replay_cases=1,
        budget_atomic_cases=int(safe_batch),
        budget_serial_prefix_cases=int(not safe_batch),
        completed_replay_rejections=1,
        recovery_conversations=1,
    )


@pytest.mark.parametrize("initial", ["chat_completions", "responses"])
async def test_api_transition_continues_completed_tool_history_without_replay(
    hass, monkeypatch, stress_trace, initial
):
    """A live API transition retains protocol-valid completed tool history."""
    from custom_components.extended_openai_conversation_responses.const import (
        CONF_CONVERSATION_CONTINUITY,
        CONVERSATION_CONTINUITY_USER,
    )
    from pytest_homeassistant_custom_component.common import MockUser
    from tests_stress.test_function_provider_wire_remaining import _provider_replies
    from tests_real_ha.test_provider_wire_e2e import _responses_sse_text
    from custom_components.extended_openai_conversation_responses.live_subentry_updates import (
        update_live_subentry,
    )

    owner = MockUser(id="api-switch-owner", is_owner=True).add_to_hass(hass)
    effects = []

    async def effect(call):
        effects.append("completed")

    hass.services.async_register("api_switch_probe", "record", effect)
    tool = {
        "spec": {
            "name": "switch_effect",
            "description": "Complete one action",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "script",
            "sequence": [{"action": "api_switch_probe.record"}],
        },
    }
    entry = _make_entry(
        "API history transition",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: initial,
            CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_USER,
            CONF_FUNCTION_TOOLS: [tool],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)

    async def say(text, cid=None):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=cid,
            context=Context(user_id=owner.id),
            language="en",
            agent_id=entry.entry_id,
        )

    wire = _install_wire(
        monkeypatch,
        agent,
        _provider_replies(
            initial,
            "completed-switch-call",
            "switch_effect",
            "COMPLETED-HISTORY-MARKER",
        ),
    )
    first = await say("RETAINED-USER-HISTORY")
    assert _speech(first) == "COMPLETED-HISTORY-MARKER" and effects == ["completed"]
    switched = "responses" if initial == "chat_completions" else "chat_completions"
    subentry = agent.subentry
    update_live_subentry(
        hass, entry, subentry, data={**subentry.data, CONF_API_MODE: switched}
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    reply = _responses_sse_text if switched == "responses" else _chat_sse_text
    followup_wire = _install_wire(monkeypatch, agent, [reply("Transition healthy")])
    result = await say("FOLLOWUP-AFTER-SWITCH", first.conversation_id)
    assert _speech(result) == "Transition healthy" and effects == ["completed"]
    body = followup_wire.requests[0]["body"]
    serialized = json.dumps(body)
    assert (
        "RETAINED-USER-HISTORY" in serialized
        and "COMPLETED-HISTORY-MARKER" in serialized
    )
    if switched == "responses":
        assert [
            item["call_id"]
            for item in body["input"]
            if item.get("type") == "function_call"
        ] == ["completed-switch-call"]
        assert [
            item["call_id"]
            for item in body["input"]
            if item.get("type") == "function_call_output"
        ] == ["completed-switch-call"]
    else:
        assert [
            call["id"]
            for item in body["messages"]
            for call in item.get("tool_calls", [])
        ] == ["completed-switch-call"]
        assert [
            item["tool_call_id"]
            for item in body["messages"]
            if item.get("role") == "tool"
        ] == ["completed-switch-call"]
    record(
        stress_trace,
        "summary",
        layer="provider-wire",
        populated_api_transition_cases=1,
        completed_tool_effects=1,
        recovery_conversations=1,
    )
