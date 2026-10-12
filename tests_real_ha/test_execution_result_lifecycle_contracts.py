"""Effects, aliases and concrete captures through genuine HA/provider entry points."""

import asyncio
from copy import deepcopy
from xml.etree.ElementTree import ParseError

import pytest

from custom_components.extended_openai_conversation_responses import (
    entity as entity_module,
    model_tool_results,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
    DOMAIN,
    SERVICE_CALL_FUNCTION,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    _ACTIVE_FUNCTION_RESULTS,
)
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.setup import async_setup_component
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _say, _speech
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _install_wire,
    _responses_sse_text,
    _responses_sse_tool_call,
)
from tests_real_ha.test_request_rules_script_semantics import _local, _record_action


def _tool(config):
    return {
        "spec": {
            "name": "effect",
            "description": "Counter effect",
            "parameters": {
                "type": "object",
                "properties": {
                    "delay": {
                        "type": ["object", "null"],
                        "properties": {
                            unit: {"type": ["number", "null"]}
                            for unit in ("hours", "minutes", "seconds")
                        },
                    }
                },
            },
        },
        "function": config,
    }


async def _counter(hass, monkeypatch, mode, depth=0, *, partial=False, observer=False):
    config = {"type": "native", "name": "execute_service_single"}
    for _ in range(depth):
        config = {"type": "composite", "sequence": [config]}
    if partial:
        config = {
            "type": "composite",
            "sequence": [config, {"type": "template", "value_template": "{{ 1 / 0 }}"}],
        }
    if observer:
        config = {
            "type": "composite",
            "sequence": [
                config,
                {"type": "template", "value_template": "{{ data is defined }}"},
            ],
        }
    agent = await _agent(
        hass,
        api_mode=mode,
        **{CONF_FUNCTION_TOOLS: [_tool(config)]},
        function_tool_error_recovery=True,
    )
    assert await async_setup_component(
        hass, "counter", {"counter": {"receipt": {"initial": 0}}}
    )
    await hass.async_block_till_done()
    async_expose_entity(hass, "conversation", "counter.receipt", True)
    tool_call = (
        _chat_sse_tool_call if mode == "chat_completions" else _responses_sse_tool_call
    )
    text = _chat_sse_text if mode == "chat_completions" else _responses_sse_text
    return agent, tool_call, text


def _arguments(*, equivalent=False, service="increment"):
    return {
        "domain": "counter",
        "service": service,
        ("service_data" if equivalent else "data"): {
            "entity_id": ["counter.receipt"] if equivalent else "counter.receipt"
        },
    }


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("depth", [0, 1, 3])
@pytest.mark.parametrize("seeded", [False, True])
async def test_equivalent_native_and_nested_composite_retries_do_not_repeat_effect(
    hass, monkeypatch, mode, depth, seeded
):
    agent, call, text = await _counter(hass, monkeypatch, mode, depth)
    _install_wire(monkeypatch, agent, [text("Ready")])
    seed = await _say(hass, agent, "hello") if seeded else None
    _install_wire(
        monkeypatch,
        agent,
        [
            call("first", "effect", _arguments()),
            (
                400,
                {
                    "error": {
                        "message": "continuation failed",
                        "type": "invalid_request_error",
                    }
                },
            ),
        ],
    )
    first = await _say(hass, agent, "increment", seed.conversation_id if seed else None)
    assert first.response.error_code is not None
    assert hass.states.get("counter.receipt").state == "1"
    wire = _install_wire(
        monkeypatch, agent, [call("retry", "effect", _arguments(equivalent=True))]
    )
    retry = await _say(hass, agent, "retry", first.conversation_id)
    assert retry.response.error_code is not None
    assert len(wire.requests) == 1
    assert hass.states.get("counter.receipt").state == "1"
    # A successful, effect-free turn resolves ambiguity. Intentional repetition works.
    _install_wire(monkeypatch, agent, [text("Acknowledged")])
    resolved = await _say(hass, agent, "acknowledge", first.conversation_id)
    assert resolved.response.error_code is None
    _install_wire(
        monkeypatch,
        agent,
        [call("intentional", "effect", _arguments(equivalent=True)), text("Done")],
    )
    assert (
        await _say(hass, agent, "increment again", first.conversation_id)
    ).response.error_code is None
    assert hass.states.get("counter.receipt").state == "2"


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("failure", ["unexpected", "cancelled", "presentation"])
async def test_post_effect_failures_preserve_receipts_and_unexpected_errors(
    hass, monkeypatch, mode, failure
):
    agent, call, text = await _counter(hass, monkeypatch, mode)
    _install_wire(monkeypatch, agent, [text("Ready")])
    seed = await _say(hass, agent, "hello")
    original = entity_module._normalize_function_result
    original_projection = model_tool_results.project_tool_result
    if failure == "presentation":

        async def fail(_):
            raise ValueError("presentation failed")

        monkeypatch.setattr(model_tool_results, "project_tool_result", fail)
        error = ValueError
    else:
        error = ParseError if failure == "unexpected" else asyncio.CancelledError

        def fail(_):
            raise error("after effect")

        monkeypatch.setattr(entity_module, "_normalize_function_result", fail)
    _install_wire(monkeypatch, agent, [call("first", "effect", _arguments())])
    with pytest.raises(error):
        await _say(hass, agent, "increment", seed.conversation_id)
    assert hass.states.get("counter.receipt").state == "1"
    monkeypatch.setattr(entity_module, "_normalize_function_result", original)
    monkeypatch.setattr(model_tool_results, "project_tool_result", original_projection)
    _install_wire(
        monkeypatch, agent, [call("retry", "effect", _arguments(equivalent=True))]
    )
    retry = await _say(hass, agent, "retry", seed.conversation_id)
    assert retry.response.error_code is not None
    assert hass.states.get("counter.receipt").state == "1"


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_partial_composite_does_not_authorize_changed_argument_retry(
    hass, monkeypatch, mode
):
    agent, call, text = await _counter(hass, monkeypatch, mode, partial=True)
    _install_wire(monkeypatch, agent, [text("Ready")])
    seed = await _say(hass, agent, "hello")
    _install_wire(monkeypatch, agent, [call("first", "effect", _arguments())])
    first = await _say(hass, agent, "increment", seed.conversation_id)
    assert first.response.error_code is not None
    assert hass.states.get("counter.receipt").state == "1"
    _install_wire(
        monkeypatch, agent, [call("retry", "effect", _arguments(service="reset"))]
    )
    retry = await _say(hass, agent, "try another argument", first.conversation_id)
    assert retry.response.error_code is not None
    assert hass.states.get("counter.receipt").state == "1"


@pytest.mark.parametrize("second", ["disabled", "failed"])
@pytest.mark.parametrize(
    "consumer", ["unconsumed", "action", "conversation_response", "success_response"]
)
async def test_shared_response_variable_cannot_publish_another_invocations_alias(
    hass, monkeypatch, second, consumer
):
    tools = [
        {
            "spec": {
                "name": name,
                "description": name,
                "parameters": {"type": "object"},
            },
            "function": {"type": "template", "value_template": value},
        }
        for name, value in [("first", "First result"), ("second", "{{ 1 / 0 }}")]
    ]
    agent = await _agent(hass, **{CONF_FUNCTION_TOOLS: tools})
    original = agent._async_execute_request_rule_function

    async def invoke(name, arguments, context):
        if name == "second":
            from homeassistant.exceptions import HomeAssistantError

            raise HomeAssistantError("recoverable invocation failure")
        return await original(name, arguments, context)

    monkeypatch.setattr(agent, "_async_execute_request_rule_function", invoke)
    _provider(monkeypatch, agent, [])
    observed = []

    async def record(call):
        observed.append(
            (deepcopy(_ACTIVE_FUNCTION_RESULTS.get()), call.data["message"])
        )

    hass.services.async_register("rule_probe", "record", record)
    actions = [
        {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "response_variable": "shared",
            "data": {"function": "first", "arguments": {}, "result_alias": "a"},
        },
        {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "response_variable": "shared",
            "data": {"function": "second", "arguments": {}, "result_alias": "b"},
            **(
                {"enabled": False}
                if second == "disabled"
                else {"continue_on_error": True}
            ),
        },
        _record_action("{{ shared.result }}"),
    ]
    if consumer == "action":
        actions.append(_record_action("{b}"))
    if consumer == "conversation_response":
        actions.append({"set_conversation_response": "{b}"})
    await agent._request_rules.async_create(
        _local(
            actions, success="{b}" if consumer == "success_response" else "Recovered"
        )
    )
    result = await _say(hass, agent, "run rule")
    assert observed == [({"a": "First result"}, "First result")]
    if consumer == "success_response":
        assert "response construction failed (missing variable b)" in _speech(
            result, successful=False
        )
        return
    assert _speech(result, successful=consumer == "unconsumed") == (
        "Recovered" if consumer == "unconsumed" else "Failed safely"
    )


@pytest.mark.parametrize("value", ["001", "false", "null"])
async def test_captured_condition_reopens_and_preserves_later_text_values(
    hass, monkeypatch, value
):
    agent = await _agent(hass)
    _provider(monkeypatch, agent, [])
    hass.states.async_set("light.kitchen", "on")
    seen = []

    async def record(call):
        seen.append(call.data["message"])

    hass.services.async_register("rule_probe", "record", record)
    rule = await agent._request_rules.async_create(
        _local(
            [
                {"variables": {"later": "kept"}},
                {
                    "if": [
                        {
                            "condition": "state",
                            "entity_id": ["light.{room}"],
                            "state": "on",
                        }
                    ],
                    "then": [_record_action("{value}"), _record_action("{{ later }}")],
                },
            ],
            phrase="check {room} with {value}",
            match_type="sentence_pattern",
        )
    )
    entry_id = agent.entry.entry_id
    assert await hass.config_entries.async_reload(entry_id)
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, entry_id)
    _provider(monkeypatch, agent, [])
    assert _speech(await _say(hass, agent, f"check kitchen with {value}")) == "Done"
    assert seen == [value, "kept"]
    assert rule["id"] in {
        item["id"] for item in agent._request_rules.snapshot()["rules"]
    }


async def test_ordinary_service_result_alias_input_and_authored_response_work(
    hass, monkeypatch
):
    from homeassistant.core import SupportsResponse

    agent = await _agent(hass)
    _provider(monkeypatch, agent, [])

    async def ordinary(call):
        assert call.data["result_alias"] == "ordinary"
        return {"different": "shape"}

    hass.services.async_register(
        "rule_probe", "ordinary", ordinary, supports_response=SupportsResponse.ONLY
    )
    seen = []

    async def record(call):
        seen.append(call.data["message"])

    hass.services.async_register("rule_probe", "record", record)
    await agent._request_rules.async_create(
        _local(
            [
                {
                    "action": "rule_probe.ordinary",
                    "data": {"result_alias": "ordinary"},
                    "response_variable": "authored",
                },
                _record_action("{{ authored.different }}"),
            ]
        )
    )
    assert _speech(await _say(hass, agent, "run rule")) == "Done"
    assert seen == ["shape"]


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize(
    "delay_pair",
    [
        (None, {}),
        ({}, {"minutes": None}),
        ({"minutes": 60}, {"seconds": 3600, "minutes": None}),
    ],
)
async def test_schedule_equivalence_counts_durable_records(
    hass, monkeypatch, mode, delay_pair
):
    from custom_components.extended_openai_conversation_responses.delayed_tools import (
        DATA_DELAYED_TOOL_MANAGER,
    )

    agent, call, _text = await _counter(hass, monkeypatch, mode, depth=2)
    first_delay, retry_delay = delay_pair
    initial = _arguments()
    if first_delay is not None:
        initial["delay"] = first_delay
    _install_wire(
        monkeypatch,
        agent,
        [
            call("first", "effect", initial),
            (
                400,
                {
                    "error": {
                        "message": "failed continuation",
                        "type": "invalid_request_error",
                    }
                },
            ),
        ],
    )
    first = await _say(hass, agent, "increment later")
    assert first.response.error_code is not None
    retry_args = {**_arguments(equivalent=True), "delay": retry_delay}
    _install_wire(monkeypatch, agent, [call("retry", "effect", retry_args)])
    assert (
        await _say(hass, agent, "retry", first.conversation_id)
    ).response.error_code is not None
    manager = hass.data[DOMAIN][DATA_DELAYED_TOOL_MANAGER]
    scheduled = bool(first_delay and any(first_delay.values()))
    assert len(manager._records) == int(scheduled)
    assert hass.states.get("counter.receipt").state == ("0" if scheduled else "1")
    if scheduled:
        disk = await manager._store.async_load()
        assert len(disk["calls"]) == 1


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("envelopes", [0, 1, 2])
async def test_ha_source_defaults_are_actual_replay_defaults(
    hass, monkeypatch, mode, envelopes
):
    from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
        new_reference_tool,
    )
    from homeassistant.helpers import llm
    from tests_real_ha.test_ha_llm_tool_acceptance import (
        AcceptanceAPI,
        AcceptanceEchoTool,
    )

    source = AcceptanceEchoTool()
    if envelopes:
        result_type = getattr(llm, "ToolResult", None)
        if result_type is None:
            pytest.skip("HA version predates tool result envelopes")
        original_call = source.async_call

        async def wrapped_call(hass, tool_input, llm_context):
            result = await original_call(hass, tool_input, llm_context)
            for _ in range(envelopes):
                result = result_type(data=result)
            return result

        monkeypatch.setattr(source, "async_call", wrapped_call)
    api = AcceptanceAPI(hass, source)
    llm.async_register_api(hass, api)
    reference = {
        "type": "ha_llm",
        "source_type": "api",
        "source_id": f"{type(source).__module__}.{type(source).__qualname__}",
        "api_id": api.id,
        "tool_name": source.name,
    }
    tool = new_reference_tool(reference, set())
    agent = await _agent(hass, api_mode=mode, **{CONF_FUNCTION_TOOLS: [tool]})
    call = (
        _chat_sse_tool_call if mode == "chat_completions" else _responses_sse_tool_call
    )
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            call("first", tool["spec"]["name"], {"value": "one"}),
            (
                400,
                {
                    "error": {
                        "message": "failed continuation",
                        "type": "invalid_request_error",
                    }
                },
            ),
        ],
    )
    first = await _say(hass, agent, "echo")
    assert first.response.error_code is not None
    assert len(source.calls) == 1
    assert source.calls[0][0].tool_args["repeat"] == 1
    import json

    body = wire.requests[-1]["body"]
    if mode == "chat_completions":
        message = next(
            item for item in body["messages"] if item.get("tool_call_id") == "first"
        )
        payload = json.loads(message["content"])
    else:
        item = next(
            item
            for item in body["input"]
            if item.get("type") == "function_call_output"
            and item.get("call_id") == "first"
        )
        payload = json.loads(item["output"])
    assert payload["result"] == {"echo": "one", "user_id": None}
    _install_wire(
        monkeypatch,
        agent,
        [call("retry", tool["spec"]["name"], {"value": "one", "repeat": 1})],
    )
    assert (
        await _say(hass, agent, "retry", first.conversation_id)
    ).response.error_code is not None
    assert len(source.calls) == 1


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_knowledge_search_wire_pages_reconstruct_ranked_matches(
    hass, monkeypatch, mode
):
    import json

    from tests_real_ha.test_knowledge_provider_wire_e2e import (
        _chat_tool_result,
        _knowledge_agent,
        _responses_tool_result,
    )

    agent = await _knowledge_agent(hass, mode)
    for index in range(13):
        await agent._knowledge.async_create(
            f"Reference {index}", 'Escaped \\" description', "match " + ('\\"\n' * 700)
        )
    expected = await agent._knowledge.async_search("match", limit=10)
    expected += await agent._knowledge.async_search("match", limit=10, offset=10)
    call = (
        _chat_sse_tool_call if mode == "chat_completions" else _responses_sse_tool_call
    )
    text = _chat_sse_text if mode == "chat_completions" else _responses_sse_text
    decode = _chat_tool_result if mode == "chat_completions" else _responses_tool_result
    found = []
    offset = 0
    while True:
        identity = f"search-{offset}"
        wire = _install_wire(
            monkeypatch,
            agent,
            [
                call(
                    identity,
                    "knowledge_search",
                    {"query": "match", "limit": 10, "offset": offset},
                ),
                text("Found"),
            ],
        )
        result = await _say(hass, agent, "search reference")
        assert result.response.error_code is None
        page = decode(wire.requests[-1]["body"], identity)
        assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":"))) <= 32000
        found.extend(item["source_id"] for item in page["results"])
        if not page["has_more"]:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert found == [item.source_id for item in expected]
    assert len(found) == 13


async def test_canonical_durable_records_reload_and_reexport_actual_store(hass):
    from dataclasses import asdict
    from datetime import timedelta

    from custom_components.extended_openai_conversation_responses.knowledge import (
        HomeAssistantKnowledgeStorage,
        KnowledgeLibrary,
    )
    from custom_components.extended_openai_conversation_responses.temporary_memory import (
        TemporaryMemory,
        TemporaryMemoryStore,
    )
    from homeassistant.util import dt as dt_util

    stamp = dt_util.utcnow().isoformat()
    raw = {
        "source_id": "retained",
        "title": " Title\n here ",
        "description": " Description\n here ",
        "content": " One\r\nTwo \r\n",
        "enabled": True,
        "created_at": stamp,
        "updated_at": stamp,
    }
    store = HomeAssistantKnowledgeStorage(hass, "contract-entry", "contract-agent")
    await store.async_save({"sources": [raw]})
    library = KnowledgeLibrary(store)
    await library.async_initialize()
    expected = asdict(KnowledgeLibrary.validate_backup_data({"sources": [raw]})[0])
    assert (await store.async_load())["sources"] == [expected]
    fresh = KnowledgeLibrary(
        HomeAssistantKnowledgeStorage(hass, "contract-entry", "contract-agent")
    )
    await fresh.async_initialize()
    assert await fresh.async_backup_data() == {"sources": [expected]}
    temp_store = TemporaryMemoryStore(
        hass, 1, "contract-temporary", private=True, atomic_writes=True
    )
    fact = {
        "memory_id": "retained",
        "scope_id": "user:owner",
        "owner_scope_id": "user:owner",
        "content": " Fact\n here ",
        "category": " Category\n here ",
        "source": "manual",
        "expires_at": (dt_util.utcnow() + timedelta(hours=1)).isoformat(),
        "created_at": stamp,
        "updated_at": stamp,
    }
    await temp_store.async_save({"records": [fact]})
    temporary = TemporaryMemory(temp_store)
    await temporary.async_initialize()
    expected_fact = asdict(TemporaryMemory.validate_backup_data({"records": [fact]})[0])
    assert (await temp_store.async_load())["records"] == [expected_fact]
    fresh_temp = TemporaryMemory(
        TemporaryMemoryStore(
            hass, 1, "contract-temporary", private=True, atomic_writes=True
        )
    )
    await fresh_temp.async_initialize()
    assert await fresh_temp.async_backup_data() == {"records": [expected_fact]}


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_composite_original_representation_observer_is_not_canonicalized_away(
    hass, monkeypatch, mode
):
    agent, call, text = await _counter(hass, monkeypatch, mode, observer=True)
    _install_wire(
        monkeypatch,
        agent,
        [
            call("first", "effect", _arguments()),
            (
                400,
                {
                    "error": {
                        "message": "failed continuation",
                        "type": "invalid_request_error",
                    }
                },
            ),
        ],
    )
    first = await _say(hass, agent, "increment")
    assert hass.states.get("counter.receipt").state == "1"
    _install_wire(
        monkeypatch,
        agent,
        [call("changed", "effect", _arguments(equivalent=True)), text("Done")],
    )
    # The Template child sees a different representation, so these whole calls
    # are intentionally distinct even though their native child is equivalent.
    result = await _say(
        hass, agent, "perform the different operation", first.conversation_id
    )
    assert result.response.error_code is None
    assert hass.states.get("counter.receipt").state == "2"


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_preexecution_service_rejection_does_not_poison_retry(
    hass, monkeypatch, mode
):
    agent, call, text = await _counter(hass, monkeypatch, mode)
    arguments = _arguments(service="becomes_available")
    _install_wire(monkeypatch, agent, [call("first", "effect", arguments)])
    first = await _say(hass, agent, "increment")
    assert first.response.error_code is not None
    assert hass.states.get("counter.receipt").state == "0"

    async def available(call):
        await hass.services.async_call(
            "counter",
            "increment",
            {"entity_id": "counter.receipt"},
            blocking=True,
            context=call.context,
        )

    hass.services.async_register("counter", "becomes_available", available)
    _install_wire(
        monkeypatch, agent, [call("retry", "effect", arguments), text("Done")]
    )
    retry = await _say(hass, agent, "retry", first.conversation_id)
    assert retry.response.error_code is None
    assert hass.states.get("counter.receipt").state == "1"


@pytest.mark.parametrize(
    "value",
    [False, 0, None, "", [], {}, {"status": "error"}, '{\n  "value": [1, 2]\n}\n'],
)
async def test_typed_and_exact_text_results_survive_local_and_provider_consumers(
    hass, monkeypatch, value
):
    import json
    from unittest.mock import AsyncMock

    from custom_components.extended_openai_conversation_responses.functions import (
        get_function,
    )

    # Control the actual backend return, independently of HA's Template parsing
    # and trailing-newline rules. EOAI dispatch, HA services/scripts and provider
    # serialization all remain the production paths under test.
    execute = AsyncMock(return_value=value)
    monkeypatch.setattr(get_function("template"), "execute", execute)
    tool = {
        "spec": {
            "name": "produce",
            "description": "Produce",
            "parameters": {"type": "object"},
        },
        "function": {"type": "template", "value_template": "controlled backend"},
    }
    agent = await _agent(hass, **{CONF_FUNCTION_TOOLS: [tool]})
    wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_tool_call("typed", "produce", {}), _chat_sse_text("Done")],
    )
    assert (await _say(hass, agent, "produce a result")).response.error_code is None
    message = next(
        message
        for message in wire.requests[-1]["body"]["messages"]
        if message.get("tool_call_id") == "typed"
    )
    projected = json.loads(message["content"])["result"]
    assert type(projected) is type(value) and projected == value
    seen = []

    async def record(call):
        seen.append((call.data["value"], deepcopy(_ACTIVE_FUNCTION_RESULTS.get())))

    hass.services.async_register("rule_probe", "typed", record)
    await agent._request_rules.async_create(
        _local(
            [
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "response_variable": "authored",
                    "data": {
                        "function": "produce",
                        "arguments": {},
                        "result_alias": "result_one",
                    },
                },
                {"action": "rule_probe.typed", "data": {"value": "{result_one}"}},
            ]
        )
    )
    assert _speech(await _say(hass, agent, "run rule")) == "Done"
    assert len(seen) == 1
    actual, results = seen[0]
    assert type(actual) is type(value) and actual == value
    assert type(results["result_one"]) is type(value) and results["result_one"] == value
    assert execute.await_count == 2
