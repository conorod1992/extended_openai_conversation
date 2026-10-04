"""Independent protocol checks below the real SDK and production dispatch."""

import base64
from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.agent_test import (
    async_test_agent,
)
from custom_components.extended_openai_conversation_responses.const import (
    DEFAULT_CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.live_subentry_updates import (
    update_live_subentry,
)
from homeassistant.components import ai_task, conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.chat_session import async_get_chat_session
from tests_real_ha.strict_provider import install_strict_provider
from tests_real_ha.test_ai_task_provider_wire import _task_entity
from tests_real_ha.test_ai_task_runtime import CallerAPI, ContextProbeTool
from tests_real_ha.test_cross_feature_acceptance import _say, _speech
from tests_real_ha.test_entry_point_contract_matrix import _contract_agent
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _response_object,
    _responses_sse_text,
    _responses_sse_tool_call,
)

MODES = ("chat_completions", "responses")
ENTITY = "light.strict_boundary"
ARGUMENTS = {
    "list": [
        {
            "domain": "light",
            "service": "turn_off",
            "service_data": {"entity_id": [ENTITY]},
        }
    ]
}


@pytest.mark.parametrize("route", ["conversation", "caller_ai_task"])
@pytest.mark.parametrize("mode", ["auto", "responses", "chat_completions"])
async def test_effective_tools_respect_model_api_capability_at_the_wire(
    hass, monkeypatch, route, mode
):
    # Independent fixture contract: this model supports text on both APIs,
    # Functions on Responses only. Conversation adds its lifecycle tool;
    # AI Task obtains tools exclusively from the caller, not stored options.
    if route == "conversation":
        agent = await _contract_agent(
            hass, api_mode=mode, chat_model="gpt-6.1-sol", reasoning_effort="low"
        )
        wire = install_strict_provider(
            monkeypatch, agent, [_text("responses", "Compatible route")]
        )
        result = await _say(hass, agent, "Use the configured request route")
        if mode == "chat_completions":
            assert result.response.error_code is not None
        else:
            assert _speech(result) == "Compatible route"
    else:
        entry, entity_id = await _task_entity(hass, mode)
        subentry = next(iter(entry.subentries.values()))
        update_live_subentry(
            hass,
            entry,
            subentry,
            data={
                **subentry.data,
                "chat_model": "gpt-6.1-sol",
                "reasoning_effort": "low",
            },
        )
        caller = CallerAPI(hass=hass, id="strict-route", name="Strict route")
        caller.tools = [ContextProbeTool()]
        wire = install_strict_provider(
            monkeypatch,
            SimpleNamespace(_client=entry.runtime_data),
            [_text("responses", "Compatible route")],
        )

        async def task():
            return await ai_task.async_generate_data(
                hass,
                task_name="Route contract",
                entity_id=entity_id,
                instructions="Use caller tools",
                llm_api=caller,
            )

        if mode == "chat_completions":
            with pytest.raises(HomeAssistantError):
                await task()
        else:
            assert (await task()).data == "Compatible route"
    wire.assert_complete(0 if mode == "chat_completions" else 1)
    if mode != "chat_completions":
        assert wire.requests[0]["path"] == "/v1/responses"
        assert wire.requests[0]["body"]["tools"]


def _text(mode, value):
    return _responses_sse_text(value) if mode == "responses" else _chat_sse_text(value)


def _call(mode, call_id, arguments):
    return (
        _responses_sse_tool_call(call_id, tool_arguments=arguments)
        if mode == "responses"
        else _chat_sse_tool_call(call_id, arguments=arguments)
    )


async def _action_agent(hass, mode, **options):
    agent = await _contract_agent(
        hass,
        api_mode=mode,
        functions=[deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])],
        **options,
    )
    hass.states.async_set(ENTITY, "on")
    async_expose_entity(hass, conversation.DOMAIN, ENTITY, True)
    effects = []

    async def turn_off(call):
        effects.append(call)
        hass.states.async_set(ENTITY, "off")

    hass.services.async_register("light", "turn_off", turn_off)
    return agent, effects


@pytest.mark.parametrize("mode", MODES)
async def test_corrected_pre_dispatch_call_crosses_wire_with_one_execution(
    hass, monkeypatch, mode
):
    agent, effects = await _action_agent(
        hass,
        mode,
        function_tool_error_recovery=True,
        max_function_calls_per_conversation=1,
    )
    wire = install_strict_provider(
        monkeypatch,
        agent,
        [
            _call(mode, "invalid", {"list": "not-an-array"}),
            _call(mode, "corrected", ARGUMENTS),
            _text(mode, "Corrected action completed"),
        ],
    )
    result = await _say(hass, agent, "Turn the light off")
    assert _speech(result) == "Corrected action completed"
    wire.assert_complete(3)
    assert len(effects) == 1
    assert hass.states.get(ENTITY).state == "off"


@pytest.mark.parametrize("mode", MODES)
async def test_replay_rejection_leaves_next_turn_provider_history_complete(
    hass, monkeypatch, mode
):
    agent, effects = await _action_agent(hass, mode)
    wire = install_strict_provider(
        monkeypatch,
        agent,
        [
            _call(mode, "executed", ARGUMENTS),
            (
                400,
                {
                    "error": {
                        "message": "Continuation lost",
                        "type": "invalid_request_error",
                    }
                },
            ),
            _call(mode, "equivalent-new-id", ARGUMENTS),
            _text(mode, "Unrelated answer"),
        ],
    )
    first = await _say(hass, agent, "Turn the light off")
    assert first.response.error_code is not None
    replay = await _say(hass, agent, "Retry that action", first.conversation_id)
    assert replay.response.error_code is not None
    final = await _say(hass, agent, "An unrelated question", first.conversation_id)
    assert _speech(final) == "Unrelated answer"
    wire.assert_complete(4)
    assert len(effects) == 1


@pytest.mark.parametrize("mode", MODES)
async def test_text_only_production_and_diagnostic_do_not_require_tools(
    hass, monkeypatch, mode
):
    agent = await _contract_agent(
        hass,
        api_mode=mode,
        chat_model="custom-text-only",
        max_function_calls_per_conversation=0,
    )
    completion = (
        _response_object("resp-text-only", [])
        if mode == "responses"
        else {
            "id": "chat-text-only",
            "object": "chat.completion",
            "created": 0,
            "model": "custom-text-only",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "OK"},
                }
            ],
        }
    )
    # This provider supports text on both APIs, and no Function schema on either.
    wire = install_strict_provider(
        monkeypatch,
        agent,
        [_text(mode, "Text works"), (200, completion)],
        supports_tools=False,
    )
    assert _speech(await _say(hass, agent, "Text only")) == "Text works"
    diagnostic = await async_test_agent(hass, agent.entry, agent.subentry)
    assert not any(check.status == "Failed" for check in diagnostic.checks), (
        diagnostic.as_dict()
    )
    wire.assert_complete(2)


@pytest.mark.parametrize(
    "mode,mime",
    [
        ("chat_completions", "image/png"),
        ("responses", "image/png"),
        ("responses", "application/pdf"),
    ],
)
async def test_retained_ai_task_chat_session_reconstructs_historical_attachment(
    hass, monkeypatch, tmp_path, mode, mime
):
    entry, entity_id = await _task_entity(hass, mode)
    entity = hass.data[ai_task.DOMAIN].get_entity(entity_id)
    content = b"Historical attachment bytes\x00\xff"
    path = tmp_path / ("photo.png" if mime == "image/png" else "report.pdf")
    path.write_bytes(content)
    attachment = conversation.Attachment(
        media_content_id="media-source://media_source/local/" + path.name,
        mime_type=mime,
        path=path,
    )
    wire = install_strict_provider(
        monkeypatch,
        SimpleNamespace(_client=entry.runtime_data),
        [_text(mode, "Initial answer"), _text(mode, "Follow-up answer")],
        attachments=(base64.b64encode(content).decode(),),
    )
    # Core's public AI Task action creates a new session. Its entity interface
    # supports retaining a ChatSession; use that real interface for this journey.
    with async_get_chat_session(hass) as session:
        first = await entity.internal_async_generate_data(
            session,
            ai_task.GenDataTask(
                name="First", instructions="Inspect this file", attachments=[attachment]
            ),
        )
    with async_get_chat_session(hass, first.conversation_id) as session:
        second = await entity.internal_async_generate_data(
            session,
            ai_task.GenDataTask(
                name="Follow-up", instructions="Another detail, with no new attachment"
            ),
        )
    assert first.data == "Initial answer"
    assert second.data == "Follow-up answer"
    assert first.conversation_id == second.conversation_id
    wire.assert_complete(2)
