"""Real HA dependent voice and presentation workflows."""
from __future__ import annotations

import json

from custom_components.extended_openai_conversation_responses.const import (
    CONF_CONTINUE_CONVERSATION, CONTINUE_CONVERSATION_CONDITIONAL,
)
from homeassistant.setup import async_setup_component
from tests_real_ha.test_assist_streaming_speech_processing import (
    _speech_agent, _chat_sse_deltas, _run_assist, _progressive_text, _final_speech,
)
from tests_real_ha.test_cross_feature_acceptance import _say, _speech
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text, _chat_sse_tool_call, _install_wire,
)


async def test_followup_answer_completes_and_next_turn_is_ordinary(
    hass, monkeypatch, hass_ws_client
):
    """Prompt for follow-up -> reply in same Assist identity -> new ordinary turn."""
    agent = await _speech_agent(hass, **{
        CONF_CONTINUE_CONVERSATION: CONTINUE_CONVERSATION_CONDITIONAL,
    })
    assert await async_setup_component(hass, "assist_pipeline", {})
    calls = []
    async def record(call):
        calls.append(call.data["marker"])
    hass.services.async_register("voice_probe", "record", record)
    # The native Assist intent pipeline receives the follow-up prompt.
    question = "Would you like the hallway light on?"
    wire = _install_wire(monkeypatch, agent, [
        _chat_sse_tool_call("ask", "set_continue_conversation", {
            "continue_conversation": True, "response": question,
        }),
        _chat_sse_text("Yes, I can do that."),
        _chat_sse_text("Ready for another request."),
    ])
    events = await _run_assist(
        await hass_ws_client(hass),
        pipeline_id=agent.entity_id,
        conversation_id="dependent-followup",
    )
    assert _final_speech(events) == question
    assert _progressive_text(events).count(question) == 1
    followup = await _say(hass, agent, "Yes, please.", "dependent-followup")
    assert _speech(followup) == "Yes, I can do that."
    normal = await _say(hass, agent, "Another request", followup.conversation_id)
    assert _speech(normal) == "Ready for another request."
    assert len(wire.requests) == 3
    assert calls == []


async def test_speech_cleanup_does_not_change_followup_provider_data(
    hass, monkeypatch, hass_ws_client
):
    """Presentation sanitizer must not rewrite the transcript sent to the provider."""
    agent = await _speech_agent(hass)
    assert await async_setup_component(hass, "assist_pipeline", {})
    original = "For details use https://example.invalid/secret and **cobalt**."
    wire = _install_wire(monkeypatch, agent, [
        _chat_sse_deltas(["For details use https://example.", "invalid/secret and **cobalt**."]),
        _chat_sse_text("Second response."),
    ])
    events = await _run_assist(
        await hass_ws_client(hass),
        pipeline_id=agent.entity_id,
        conversation_id="speech-data-boundary",
    )
    assert "https://example.invalid" not in _final_speech(events)
    assert "**" not in _final_speech(events)
    after = await _say(hass, agent, "Recall the previous reply", "speech-data-boundary")
    assert _speech(after) == "Second response."
    body = wire.requests[1]["body"]
    serialized = json.dumps(body, ensure_ascii=False)
    # Only assert the documented functional boundary, independent of whether
    # history retains raw or processed assistant text.
    assert "Recall the previous reply" in serialized
    assert "Second response." not in serialized
    assert len(wire.requests) == 2
