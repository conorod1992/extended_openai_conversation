"""Replay evidence must outlive the conversation entity that observed an effect."""

import json
import sqlite3

import pytest

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context
from tests_real_ha.entity_service_probe import register_entity_service_probe
from tests_real_ha.test_cross_feature_acceptance import _agent as configured_agent, _say
from tests_real_ha.test_provider_wire_e2e import (
    _agent,
    _chat_sse_text,
    _chat_sse_tool_call,
    _install_wire,
    _responses_sse_text,
    _responses_sse_tool_call,
)


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_failed_effect_cannot_replay_after_genuine_reload(
    hass, monkeypatch, mode, failure_kind="lost_ack"
):
    agent = await _agent(hass, mode)
    entry = agent.entry
    entity_id = "light.reload_replay"
    effects = []

    async def turn_off(call):
        effects.append(call.data["entity_id"])
        hass.states.async_set(entity_id, "off")

    register_entity_service_probe(hass, "light", "turn_off", turn_off)
    hass.states.async_set(entity_id, "on")
    async_expose_entity(hass, conversation.DOMAIN, entity_id, True)
    arguments = {
        "list": [
            {
                "domain": "light",
                "service": "turn_off",
                "service_data": {"entity_id": [entity_id]},
            }
        ]
    }

    def tool(call_id):
        return (
            _responses_sse_tool_call(call_id=call_id, tool_arguments=arguments)
            if mode == "responses"
            else _chat_sse_tool_call(call_id=call_id, arguments=arguments)
        )

    final = _responses_sse_text if mode == "responses" else _chat_sse_text
    if failure_kind == "output_budget":
        from custom_components.extended_openai_conversation_responses import (
            conversation as owner,
            provider_output_budget as limits,
        )
        from custom_components.extended_openai_conversation_responses.provider_errors import (
            ProviderStreamError,
        )

        failures = []
        render_error = owner._conversation_error_result

        def observe_error(*args, **kwargs):
            failures.append(args[3])
            return render_error(*args, **kwargs)

        monkeypatch.setattr(owner, "_conversation_error_result", observe_error)
        monkeypatch.setattr(limits, "MAX_ARGUMENT_CHARACTERS", 256)
        oversized = {"padding": "x" * 1024}
        continuation = (
            _responses_sse_tool_call(call_id="oversized", tool_arguments=oversized)
            if mode == "responses"
            else _chat_sse_tool_call(call_id="oversized", arguments=oversized)
        )
    else:
        continuation = (
            400,
            {
                "error": {
                    "message": "continuation failed",
                    "type": "invalid_request_error",
                }
            },
        )
    _install_wire(monkeypatch, agent, [tool("first-effect"), continuation])

    async def say(conversation_id=None):
        return await conversation.async_converse(
            hass=hass,
            text="Turn off the reload test light",
            conversation_id=conversation_id,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )

    failed = await say()
    assert failed.response.error_code is not None
    if failure_kind == "output_budget":
        assert len(failures) == 1
        assert isinstance(failures[0], ProviderStreamError)
        assert failures[0].type == "output_limit_exceeded"
    assert failed.conversation_id
    assert len(effects) == 1
    assert hass.states.get(entity_id).state == "off"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    replacement = conversation.async_get_agent(hass, entry.entry_id)
    assert replacement is not agent
    wire = _install_wire(
        monkeypatch, replacement, [tool("new-provider-call-id"), final("Checked")]
    )
    resumed = await say(failed.conversation_id)
    assert resumed.conversation_id == failed.conversation_id
    assert wire.requests
    assert len(effects) == 1, "reload must not permit a second external effect"
    assert hass.states.get(entity_id).state == "off"

    # A deliberate successful turn retires ambiguity, including after another reload.
    _install_wire(monkeypatch, replacement, [final("Reviewed")])
    acknowledged = await say(failed.conversation_id)
    assert acknowledged.response.error_code is None
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    replacement = conversation.async_get_agent(hass, entry.entry_id)
    _install_wire(monkeypatch, replacement, [tool("legitimate-repeat"), final("Done")])
    assert (await say(failed.conversation_id)).response.error_code is None
    assert len(effects) == 2
    _install_wire(monkeypatch, replacement, [tool("new-conversation"), final("Done")])
    assert (await say()).response.error_code is None
    assert len(effects) == 3


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_reload_oracle_detects_entity_owned_state(hass, monkeypatch, mode):
    from custom_components.extended_openai_conversation_responses import (
        conversation as owner,
    )
    from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
        ReplaySafetyState,
    )

    monkeypatch.setattr(
        owner, "get_agent_replay_safety", lambda *_: ReplaySafetyState()
    )
    with pytest.raises(
        AssertionError, match="reload must not permit a second external effect"
    ):
        await test_failed_effect_cannot_replay_after_genuine_reload(
            hass, monkeypatch, mode
        )


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_budget_failure_preserves_effect_through_retry_and_reload(
    hass, monkeypatch, mode
):
    await test_failed_effect_cannot_replay_after_genuine_reload(
        hass, monkeypatch, mode, failure_kind="output_budget"
    )


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("backend", ["sqlite", "template"])
async def test_read_retry_after_failed_provider_continuation(
    hass, monkeypatch, tmp_path, mode, backend
):
    path = tmp_path / "read.db"

    def prepare_database():
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE states (state TEXT)")
            db.execute("INSERT INTO states VALUES ('22.5')")

    await hass.async_add_executor_job(prepare_database)
    config = (
        {"type": "sqlite", "db_url": str(path), "query": "SELECT state FROM states"}
        if backend == "sqlite"
        else {"type": "template", "value_template": "{{ 2 + 2 }}"}
    )
    tool = {
        "spec": {
            "name": "read_value",
            "description": "Read",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": config,
    }
    agent = await configured_agent(hass, api_mode=mode, functions=[tool])

    def call(call_id):
        return (
            _responses_sse_tool_call(call_id, name="read_value", tool_arguments={})
            if mode == "responses"
            else _chat_sse_tool_call(call_id, name="read_value", arguments={})
        )

    final = _responses_sse_text if mode == "responses" else _chat_sse_text

    def outputs(wire):
        body = wire.requests[-1]["body"]
        return [
            json.loads(
                item["content"] if mode == "chat_completions" else item["output"]
            )["result"]
            for item in body["messages" if mode == "chat_completions" else "input"]
            if item.get("role") == "tool" or item.get("type") == "function_call_output"
        ]

    first_wire = _install_wire(
        monkeypatch,
        agent,
        [
            call("first"),
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
    failed = await _say(hass, agent, "Read the value")
    assert failed.response.error_code is not None
    assert outputs(first_wire)[-1] == (
        [{"state": "22.5"}] if backend == "sqlite" else "4"
    )

    # Verify a fresh provider call reaches actual I/O, not a retained result.
    def change_database():
        with sqlite3.connect(path) as db:
            db.execute("UPDATE states SET state='23.0'")

    await hass.async_add_executor_job(change_database)
    wire = _install_wire(monkeypatch, agent, [call("fresh-id"), final("Read again")])
    resumed = await _say(hass, agent, "Read again", failed.conversation_id)
    assert resumed.response.error_code is None
    assert resumed.conversation_id == failed.conversation_id
    assert len(wire.requests) == 2
    assert outputs(wire)[-1] == ([{"state": "23.0"}] if backend == "sqlite" else "4")
    _install_wire(monkeypatch, agent, [call("acknowledged"), final("Done")])
    assert (
        await _say(hass, agent, "Read again", failed.conversation_id)
    ).response.error_code is None
