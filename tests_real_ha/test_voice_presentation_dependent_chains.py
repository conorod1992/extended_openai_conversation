"""Real HA dependent voice and presentation workflows."""

from __future__ import annotations

import json

from custom_components.extended_openai_conversation_responses.const import (
    CONF_CONTINUE_CONVERSATION,
    CONTINUE_CONVERSATION_CONDITIONAL,
)
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.setup import async_setup_component
from tests_real_ha.test_assist_streaming_speech_processing import (
    _chat_sse_deltas,
    _final_speech,
    _progressive_text,
    _run_assist,
    _speech_agent,
)
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _install_wire,
)


async def test_followup_answer_completes_and_next_turn_is_ordinary(
    hass, monkeypatch, hass_ws_client
):
    """Question → authenticated Assist reply/effect → ordinary request, without replay."""
    agent = await _speech_agent(
        hass,
        **{
            CONF_CONTINUE_CONVERSATION: CONTINUE_CONVERSATION_CONDITIONAL,
        },
    )
    assert await async_setup_component(hass, "assist_pipeline", {})
    assert await async_setup_component(
        hass,
        "counter",
        {
            "counter": {"voice_followup_effect": {"initial": 0}},
        },
    )
    await hass.async_block_till_done()
    entity_id = "counter.voice_followup_effect"
    async_expose_entity(hass, "conversation", entity_id, True)
    question = "Would you like me to increment the test counter?"
    answer = "The counter has been incremented."
    ordinary = "Ready for another request."
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "ask",
                "set_continue_conversation",
                {
                    "continue_conversation": True,
                    "response": question,
                },
            ),
            _chat_sse_tool_call(
                "effect",
                "execute_services",
                {
                    "list": [
                        {
                            "domain": "counter",
                            "service": "increment",
                            "service_data": {"entity_id": [entity_id]},
                        }
                    ],
                },
            ),
            _chat_sse_tool_call(
                "answered",
                "set_continue_conversation",
                {
                    "continue_conversation": False,
                    "response": answer,
                },
            ),
            _chat_sse_tool_call(
                "ordinary",
                "set_continue_conversation",
                {
                    "continue_conversation": False,
                    "response": ordinary,
                },
            ),
        ],
    )
    client = await hass_ws_client(hass)
    conversation_id = "dependent-followup"

    async def speak(text):
        return await _run_assist(
            client,
            pipeline_id=agent.entity_id,
            conversation_id=conversation_id,
            text=text,
        )

    events = await speak("Offer to increment the test counter")
    assert _final_speech(events) == question
    assert _progressive_text(events).count(question) == 1
    first_output = next(event for event in events if event["type"] == "intent-end")[
        "data"
    ]["intent_output"]
    assert first_output["continue_conversation"] is True
    assert hass.states.get(entity_id).state == "0"
    followup = await speak("Yes, please.")
    assert _final_speech(followup) == answer
    assert _progressive_text(followup).count(answer) == 1
    followup_output = next(
        event for event in followup if event["type"] == "intent-end"
    )["data"]["intent_output"]
    assert followup_output["conversation_id"] == first_output["conversation_id"]
    assert followup_output["continue_conversation"] is False
    assert hass.states.get(entity_id).state == "1"
    normal = await speak("Another request")
    assert _final_speech(normal) == ordinary
    assert _progressive_text(normal).count(ordinary) == 1
    assert hass.states.get(entity_id).state == "1"
    assert len(wire.requests) == 4
    followup_payload = json.dumps(wire.requests[1]["body"])
    assert question in followup_payload
    assert "Yes, please." in followup_payload
    assert first_output["conversation_id"] == conversation_id


async def test_speech_cleanup_does_not_change_followup_provider_data(
    hass, monkeypatch, hass_ws_client
):
    """Presentation sanitizer must not rewrite the transcript sent to the provider."""
    agent = await _speech_agent(hass)
    assert await async_setup_component(hass, "assist_pipeline", {})
    original = "For details use https://example.invalid/secret and **cobalt**."
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_deltas(
                ["For details use https://example.", "invalid/secret and **cobalt**."]
            ),
            _chat_sse_text("Second response."),
        ],
    )
    client = await hass_ws_client(hass)
    events = await _run_assist(
        client,
        pipeline_id=agent.entity_id,
        conversation_id="speech-data-boundary",
    )
    assert "https://example.invalid" not in _final_speech(events)
    assert "**" not in _final_speech(events)
    after = await _run_assist(
        client,
        pipeline_id=agent.entity_id,
        conversation_id="speech-data-boundary",
        text="Recall the previous reply",
    )
    assert _final_speech(after) == "Second response."
    assert _progressive_text(after) == "Second response."
    body = wire.requests[1]["body"]
    serialized = json.dumps(body, ensure_ascii=False)
    # Speech cleanup must preserve the original model reply in later context.
    assistant_history = [
        item["content"] for item in body["messages"] if item["role"] == "assistant"
    ]
    assert original in assistant_history
    assert _progressive_text(events) == _final_speech(events)
    assert "Recall the previous reply" in serialized
    assert "Second response." not in serialized
    assert len(wire.requests) == 2
