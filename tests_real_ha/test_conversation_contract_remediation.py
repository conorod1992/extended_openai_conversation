"""Native Rules, satellite task context and progressive Responses delivery."""

import json
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CONTINUE_CONVERSATION,
    CONF_FUNCTION_TOOLS,
    CONTINUE_CONVERSATION_CONDITIONAL,
)
from homeassistant.components.assist_pipeline.pipeline import (
    PipelineInput,
    PipelineRun,
    PipelineStage,
    async_get_pipeline,
)
from homeassistant.components.assist_satellite import entity as satellite_module
from homeassistant.components.assist_satellite.entity import (
    AssistSatelliteAnnouncement,
    AssistSatelliteEntity,
)
from homeassistant.core import Context
from homeassistant.helpers import (
    chat_session,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.setup import async_setup_component
from tests_real_ha.test_assist_streaming_speech_processing import (
    _final_speech,
    _progressive_text,
    _run_assist,
    _speech_agent,
)
from tests_real_ha.test_cross_feature_acceptance import _agent, _say, _speech
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _responses_sse_text,
    _responses_sse_tool_call,
)
from tests_real_ha.test_request_rules_script_semantics import _local


def combined_response(text, name, arguments):
    text_events = [
        json.loads(frame.removeprefix(b"data: "))
        for frame in _responses_sse_text(text).strip().split(b"\n\n")
    ]
    call_events = [
        json.loads(frame.removeprefix(b"data: "))
        for frame in _responses_sse_tool_call(
            call_id="audit-finalize", name=name, tool_arguments=arguments
        )
        .strip()
        .split(b"\n\n")
    ]
    events = text_events[:-1]
    for event in call_events[:-1]:
        event["output_index"] = 1
        events.append(event)
    completed = call_events[-1]
    completed["response"]["output"] = [
        text_events[-1]["response"]["output"][0],
        *completed["response"]["output"],
    ]
    events.append(completed)
    for index, event in enumerate(events):
        event["sequence_number"] = index
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


async def test_already_streamed_responses_answer_is_spoken_once(
    hass, hass_ws_client, monkeypatch
):
    question = "The door is unlocked. Would you like me to lock it?"
    tool = {
        "spec": {
            "name": "check_door",
            "description": "Check door",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "unlocked"},
    }
    agent = await _speech_agent(
        hass,
        **{
            CONF_API_MODE: API_MODE_RESPONSES,
            CONF_CONTINUE_CONVERSATION: CONTINUE_CONVERSATION_CONDITIONAL,
            CONF_FUNCTION_TOOLS: [tool],
        },
    )
    assert await async_setup_component(hass, "assist_pipeline", {})
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _responses_sse_tool_call(
                call_id="audit-check", name="check_door", tool_arguments={}
            ),
            combined_response(
                question,
                "set_continue_conversation",
                {"continue_conversation": True, "response": question},
            ),
        ],
    )
    events = await _run_assist(
        await hass_ws_client(hass),
        pipeline_id=agent.entity_id,
        conversation_id="audit-separated-output",
    )
    spoken = _progressive_text(events)
    assert len(wire.requests) == 2
    assert spoken.count(question) == 1
    assert _final_speech(events) == question


@pytest.mark.parametrize(
    "actions,expected",
    [
        ([{"set_conversation_response": "{{ '' }}"}], ""),
        ([{"set_conversation_response": "Ready"}], "Ready"),
        ([{"stop": "Successful stop"}], "Done"),
    ],
)
async def test_native_rule_preserves_explicit_empty_speech(
    hass, monkeypatch, actions, expected
):
    agent = await _agent(hass)
    wire = _install_wire(monkeypatch, agent, [])
    await agent._request_rules.async_create(_local(actions, phrase="run rule"))
    result = await _say(hass, agent, "run rule")
    assert _speech(result) == expected
    assert not wire.requests


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("continuity", ["ha_default", "device", "user"])
@pytest.mark.parametrize("custom", [False, True])
async def test_satellite_background_survives_followup_and_clears_for_new_owner(
    hass, monkeypatch, freezer, mode, continuity, custom
):
    options = {"api_mode": mode, "conversation_continuity": continuity}
    if custom:
        options["prompt"] = (
            "Custom background: {{ user_input.extra_system_prompt | default('', true) }}"
        )
    agent = await _agent(hass, **options)
    assert await async_setup_component(hass, "assist_pipeline", {})
    owner = await hass.auth.async_create_user("Task owner", group_ids=["system-admin"])
    other = await hass.auth.async_create_user("Other owner", group_ids=["system-admin"])
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=agent.entry.entry_id,
        identifiers={("test_satellite_context", "device")},
    )
    registry = er.async_get(hass).async_get_or_create(
        "assist_satellite", "test_satellite_context", "satellite", device_id=device.id
    )

    class Satellite(AssistSatelliteEntity):
        async def async_get_configuration(self):
            return None

        async def async_set_configuration(self, configuration):
            pass

        def _resolve_pipeline(self):
            return agent.entity_id

        def _resolve_vad_sensitivity(self):
            return 0.1

        def _set_state(self, state):
            self.observed_state = (
                state  # hardware state publishing is outside this journey
            )

        def on_pipeline_event(self, event):
            self.events.append(event)

        async def async_announce(self, announcement):
            pass

        async def async_start_conversation(self, announcement):
            self.announcement = announcement

        async def _resolve_announcement_media_id(self, message, media_id, **kwargs):
            return AssistSatelliteAnnouncement(
                message or "",
                "fixture://announcement",
                "fixture://announcement",
                None,
                "url",
            )

    satellite = Satellite()
    satellite.hass = hass
    satellite.entity_id = registry.entity_id
    satellite.registry_entry = registry
    satellite.platform = SimpleNamespace(config_entry=agent.entry)
    satellite.events = []

    # Replace only the hardware audio/STT adapter, retaining real HA pipeline,
    # satellite one-shot context consumption, ChatLog and integration dispatch.
    async def from_audio(hass, **kwargs):
        with chat_session.async_get_chat_session(
            hass, kwargs["conversation_id"]
        ) as session:
            run = PipelineRun(
                hass,
                context=kwargs["context"],
                pipeline=async_get_pipeline(hass, pipeline_id=kwargs["pipeline_id"]),
                start_stage=PipelineStage.INTENT,
                end_stage=PipelineStage.INTENT,
                event_callback=kwargs["event_callback"],
            )
            pipeline = PipelineInput(
                intent_input="Tell me what to do next",
                session=session,
                device_id=kwargs["device_id"],
                satellite_id=kwargs["satellite_id"],
                conversation_extra_system_prompt=kwargs[
                    "conversation_extra_system_prompt"
                ],
                run=run,
            )
            await pipeline.validate()
            await pipeline.execute()

    monkeypatch.setattr(
        satellite_module, "async_pipeline_from_audio_stream", from_audio
    )

    async def audio():
        yield b"fixture audio"

    reply = _responses_sse_text if mode == "responses" else _chat_sse_text
    wire = _install_wire(monkeypatch, agent, [reply("Task reply") for _ in range(4)])
    background = "Background absent from announcement: task canary cobalt 741."
    await satellite.async_internal_start_conversation(
        start_message="Please help with this task.",
        extra_system_prompt=background,
        preannounce=False,
    )
    assert background not in satellite.announcement.message
    await satellite.async_accept_pipeline_from_satellite(
        audio(), context=Context(user_id=owner.id)
    )
    assert satellite._extra_system_prompt is None
    assert background in json.dumps(wire.requests[0]["body"])
    if continuity != "ha_default":
        from datetime import timedelta

        from pytest_homeassistant_custom_component.common import async_fire_time_changed

        from homeassistant.util import dt as dt_util

        # HA's five-minute ChatLog expiry must not discard the integration's
        # longer, explicitly configured logical conversation context.
        freezer.move_to(dt_util.utcnow() + timedelta(minutes=6))
        async_fire_time_changed(hass, fire_all=True)
        await hass.async_block_till_done(wait_background_tasks=True)
    await satellite.async_accept_pipeline_from_satellite(
        audio(), context=Context(user_id=owner.id)
    )
    assert background in json.dumps(wire.requests[1]["body"])
    # An authenticated owner boundary must clear task context even on the same device/ID.
    await satellite.async_accept_pipeline_from_satellite(
        audio(), context=Context(user_id=other.id)
    )
    assert background not in json.dumps(wire.requests[2]["body"])
    # Starting another satellite conversation also resets the old background.
    await satellite.async_internal_start_conversation(
        start_message="A fresh task.",
        extra_system_prompt="New task context",
        preannounce=False,
    )
    await satellite.async_accept_pipeline_from_satellite(
        audio(), context=Context(user_id=owner.id)
    )
    assert background not in json.dumps(wire.requests[3]["body"])


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("continuity", ["ha_default", "device", "user"])
@pytest.mark.parametrize("override", ["Current-turn background override", ""])
async def test_background_override_and_explicit_reset_reach_provider(
    hass, monkeypatch, mode, continuity, override
):
    from custom_components.extended_openai_conversation_responses.conversation_lifecycle import (
        async_reset_conversation_context,
    )
    from homeassistant.components import conversation

    agent = await _agent(hass, api_mode=mode, conversation_continuity=continuity)
    owner = await hass.auth.async_create_user(
        "Background owner", group_ids=["system-admin"]
    )
    reply = _responses_sse_text if mode == "responses" else _chat_sse_text
    wire = _install_wire(monkeypatch, agent, [reply("Reply") for _ in range(4)])

    async def say(conversation_id, extra):
        result = await conversation.async_converse(
            hass=hass,
            text="Continue task",
            conversation_id=conversation_id,
            context=Context(user_id=owner.id),
            language="en",
            agent_id=agent.entity_id,
            device_id="task-device",
            extra_system_prompt=extra,
        )
        assert _speech(result) == "Reply"
        return result.conversation_id

    background = "Original background canary saffron 821"
    conversation_id = await say(None, background)
    assert background in json.dumps(wire.requests[0]["body"])
    conversation_id = await say(conversation_id, override)
    assert background not in json.dumps(wire.requests[1]["body"])
    conversation_id = await say(conversation_id, None)
    assert background not in json.dumps(wire.requests[2]["body"])
    if override:
        assert override in json.dumps(wire.requests[1]["body"])
        assert override in json.dumps(wire.requests[2]["body"])
    key = next(iter(agent._continuity._sessions), None)
    state_session = f"continuity:{key}" if key else f"conversation:{conversation_id}"
    await async_reset_conversation_context(
        hass,
        agent._continuity,
        agent.entry.entry_id,
        agent.subentry.subentry_id,
        continuity_key=key,
        state_session_id=state_session,
        memory_session_id=state_session,
    )
    await say(conversation_id, None)
    body = json.dumps(wire.requests[3]["body"])
    assert background not in body
    if override:
        assert override not in body
