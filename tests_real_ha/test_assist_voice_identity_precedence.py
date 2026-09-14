"""Real HA Assist coverage for Voice Identity source precedence."""

from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_ARCHIVE_ENABLED,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
    CONF_GUEST_MODE_ENABLED,
    CONF_VOICE_DEVICE_MAPPINGS,
    CONF_VOICE_SCOPE_POLICY,
    VOICE_POLICY_DEVICE_MAPPING,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    async_get_archive,
)
from homeassistant.components import conversation
from homeassistant.components.assist_pipeline.pipeline import (
    PipelineEvent,
    PipelineEventType,
    PipelineInput,
    PipelineRun,
    PipelineStage,
    async_get_pipeline,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import chat_session
from homeassistant.setup import async_setup_component

from tests_real_ha.test_acceptance_lifecycle import (
    _conversation_subentry,
    _make_entry,
    _setup_entry,
)
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire

_DEVICE_ID = "voice-identity-device"
_SATELLITE_ID = "assist_satellite.voice_identity_kitchen"
_DEVICE_USER_ID = "voice-device-user"
_SATELLITE_USER_ID = "voice-satellite-user"
_CONVERSATION_ID = "assist-voice-identity-both-origins"
_RESPONSE_TEXT = "Voice identity precedence accepted."


def _add_user(hass: HomeAssistant, user_id: str, name: str) -> MockUser:
    user = MockUser(id=user_id, name=name)
    user.add_to_hass(hass)
    return user


async def _agent(hass: HomeAssistant, device_user: MockUser, satellite_user: MockUser):
    entry = _make_entry(
        "Assist Voice Identity",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: [],
            CONF_ARCHIVE_ENABLED: True,
            CONF_GUEST_MODE_ENABLED: False,
            CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
            CONF_VOICE_DEVICE_MAPPINGS: {
                _DEVICE_ID: device_user.id,
                _SATELLITE_ID: satellite_user.id,
            },
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    return entry, agent


async def _run_genuine_assist(hass: HomeAssistant, *, pipeline_id: str) -> list[PipelineEvent]:
    assert await async_setup_component(hass, "assist_pipeline", {})
    events: list[PipelineEvent] = []
    with chat_session.async_get_chat_session(hass, _CONVERSATION_ID) as session:
        pipeline_input = PipelineInput(
            intent_input="Which Voice Identity owns this Assist turn?",
            session=session,
            device_id=_DEVICE_ID,
            satellite_id=_SATELLITE_ID,
            run=PipelineRun(
                hass,
                context=Context(),
                pipeline=async_get_pipeline(hass, pipeline_id=pipeline_id),
                start_stage=PipelineStage.INTENT,
                end_stage=PipelineStage.INTENT,
                event_callback=events.append,
            ),
        )
        await pipeline_input.execute(validate=True)
    return events


def _event(events: list[PipelineEvent], event_type: PipelineEventType) -> PipelineEvent:
    matches = [event for event in events if event.type == event_type]
    assert len(matches) == 1
    return matches[0]


async def test_genuine_assist_prefers_satellite_voice_identity_when_both_ids_exist(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """Use satellite identity whenever the HA ConversationInput actually carries it."""
    device_user = _add_user(hass, _DEVICE_USER_ID, "Device User")
    satellite_user = _add_user(hass, _SATELLITE_USER_ID, "Satellite User")
    entry, agent = await _agent(hass, device_user, satellite_user)

    # Assert the test itself really configured both possible Voice Identity owners.
    mappings = agent.subentry.data[CONF_VOICE_DEVICE_MAPPINGS]
    assert mappings[_DEVICE_ID] == device_user.id
    assert mappings[_SATELLITE_ID] == satellite_user.id

    delivered_identities: list[tuple[str | None, str | None]] = []
    original_process = agent.async_process

    async def capture_input(user_input: Any):
        # Snapshot at the exact agent ingress boundary. Do not retain the mutable
        # ConversationInput object and inspect it after processing, because HA or an
        # agent may legitimately mutate/reuse request objects during the turn.
        delivered_identities.append((user_input.device_id, user_input.satellite_id))
        return await original_process(user_input)

    monkeypatch.setattr(agent, "async_process", capture_input)
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text(_RESPONSE_TEXT)])
    events = await _run_genuine_assist(hass, pipeline_id=agent.entity_id)

    assert events[0].type == PipelineEventType.RUN_START
    assert events[-1].type == PipelineEventType.RUN_END
    intent_start = _event(events, PipelineEventType.INTENT_START)
    assert intent_start.data is not None
    assert intent_start.data["device_id"] == _DEVICE_ID
    assert intent_start.data["satellite_id"] == _SATELLITE_ID

    intent_end = _event(events, PipelineEventType.INTENT_END)
    assert intent_end.data is not None
    assert (
        intent_end.data["intent_output"]["response"]["speech"]["plain"]["speech"]
        == _RESPONSE_TEXT
    )

    assert len(delivered_identities) == 1
    delivered_device_id, delivered_satellite_id = delivered_identities[0]
    assert delivered_device_id == _DEVICE_ID
    # HA releases differ at this boundary: some expose satellite_id in the pipeline
    # event but do not forward it into ConversationInput. ExtendedOpenAI can only
    # apply satellite precedence when HA actually supplies that field to the agent.
    assert delivered_satellite_id in (None, _SATELLITE_ID)

    subentry = _conversation_subentry(entry)
    archive = await async_get_archive(hass, entry.entry_id, subentry.subentry_id)
    satellite_sessions = await archive.async_list_sessions(f"user:{satellite_user.id}")
    device_sessions = await archive.async_list_sessions(f"user:{device_user.id}")

    if delivered_satellite_id == _SATELLITE_ID:
        assert device_sessions["sessions"] == []
        assert len(satellite_sessions["sessions"]) == 1
        session = satellite_sessions["sessions"][0]
        assert session["scope_id"] == f"user:{satellite_user.id}"
        assert session["source_device_id"] == _SATELLITE_ID
    else:
        assert satellite_sessions["sessions"] == []
        assert len(device_sessions["sessions"]) == 1
        session = device_sessions["sessions"][0]
        assert session["scope_id"] == f"user:{device_user.id}"
        assert session["source_device_id"] == _DEVICE_ID

    assert session["scope_type"] == "user"
    assert session["scope_source"] == "device_mapping"
    assert session["home_assistant_conversation_id"] == _CONVERSATION_ID
    assert session["turn_count"] == 1
    assert session["retention_state"] == "retained"

    assert len(wire.requests) == 1
    assert wire.requests[0]["path"] == "/v1/chat/completions"
    assert wire.requests[0]["body"]["stream"] is True
