"""Direct processing action and conversation lifecycle tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from openai import OpenAIError
import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_CONVERSATION_CONTINUITY,
    CONVERSATION_CONTINUITY_DEVICE,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    ConversationContinuity,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from custom_components.extended_openai_conversation_responses.services import (
    async_setup_services,
)
from custom_components.extended_openai_conversation_responses.usage import UsageManager
from homeassistant.components import conversation
from homeassistant.core import Context, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent


class FakeAgent:
    entity_id = "conversation.extended_openai"

    def __init__(self, *, local=False):
        self.inputs = []
        self.local = local

    async def async_process_direct(self, user_input):
        self.inputs.append(user_input)
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech("Processed normally")
        return (
            conversation.ConversationResult(
                response=response,
                conversation_id=user_input.conversation_id or "new-conversation",
            ),
            {"handled_locally": self.local},
        )


class MemoryStorage:
    data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


async def _handler(hass):
    await async_setup_services(hass, {})
    return next(
        call.args[2]
        for call in hass.services.async_register.call_args_list
        if call.args[:2] == (DOMAIN, "process")
    )


async def test_process_action_calls_agent_pipeline_and_returns_response(monkeypatch):
    hass = MagicMock()
    hass.config.language = "en"
    hass.services.async_register = MagicMock()
    hass.services.async_call = AsyncMock()
    agent = FakeAgent(local=True)
    monkeypatch.setattr(conversation, "async_get_agent", lambda _hass, _id: agent)
    handler = await _handler(hass)
    call = ServiceCall(
        hass,
        DOMAIN,
        "process",
        {
            "text": "good night",
            "agent_id": agent.entity_id,
            "conversation_id": "existing-id",
            "device_id": "device-id",
            "satellite_id": "satellite-id",
            "language": "en-GB",
        },
        return_response=True,
        context=Context(user_id="owner"),
    )
    result = await handler(call)
    assert result == {
        "response": "Processed normally",
        "conversation_id": "existing-id",
        "handled_locally": True,
    }
    assert agent.inputs[0].text == "good night"
    assert agent.inputs[0].context.user_id == "owner"
    assert agent.inputs[0].device_id == "device-id"
    assert agent.inputs[0].satellite_id == "satellite-id"
    assert agent.inputs[0].language == "en-GB"
    # The direct doorway calls the entity itself and never conversation.process.
    hass.services.async_call.assert_not_awaited()


async def test_process_action_reports_missing_agent(monkeypatch):
    hass = MagicMock()
    hass.config.language = "en"
    hass.services.async_register = MagicMock()
    monkeypatch.setattr(conversation, "async_get_agent", lambda _hass, _id: None)
    handler = await _handler(hass)
    call = ServiceCall(
        hass,
        DOMAIN,
        "process",
        {"text": "hello", "agent_id": "conversation.missing"},
        return_response=True,
        context=Context(),
    )
    with pytest.raises(Exception, match="agent not found"):
        await handler(call)


async def test_direct_and_normal_entry_points_share_the_same_core_pipeline():
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    expected = object()
    entity._async_process = AsyncMock(return_value=expected)
    user_input = object()
    assert await entity.async_process(user_input) is expected
    direct_result, metadata = await entity.async_process_direct(user_input)
    assert direct_result is expected
    assert metadata == {"handled_locally": False}
    assert entity._async_process.await_count == 2


async def test_process_action_rejects_empty_text(monkeypatch):
    hass = MagicMock()
    hass.config.language = "en"
    hass.services.async_register = MagicMock()
    monkeypatch.setattr(conversation, "async_get_agent", lambda _hass, _id: FakeAgent())
    handler = await _handler(hass)
    call = ServiceCall(
        hass,
        DOMAIN,
        "process",
        {"text": "   ", "agent_id": "conversation.extended_openai"},
        return_response=True,
        context=Context(),
    )
    with pytest.raises(Exception, match="cannot be empty"):
        await handler(call)


async def test_early_failure_after_continuity_claim_releases_session(monkeypatch):
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity._continuity = ConversationContinuity("agent")
    entity.subentry = SimpleNamespace(
        data={CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_DEVICE}
    )
    entity._resolve_live_guest_policy = MagicMock(
        return_value=GuestCapabilityPolicy.unrestricted()
    )
    entity._async_process_claimed = AsyncMock(side_effect=RuntimeError("setup failed"))
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.conversation.resolve_data_scope",
        lambda *_: user_scope("alice", source="test", device_id="kitchen"),
    )
    user_input = SimpleNamespace(
        as_llm_context=lambda _: SimpleNamespace(
            context=SimpleNamespace(id="context", user_id="alice")
        ),
        satellite_id=None,
        device_id="kitchen",
        conversation_id=None,
    )

    with pytest.raises(RuntimeError, match="setup failed"):
        await entity._async_process(user_input)

    assert entity._continuity._sessions["device:kitchen"].in_flight is False


def _message_entity_and_input():
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=MagicMock()))
    entity.subentry = SimpleNamespace(subentry_id="agent", data={})
    entity._attr_entity_id = "conversation.agent"
    entity._usage = SimpleNamespace(mark_current_run_failed=MagicMock())
    entity._get_exposed_entities = MagicMock(return_value=[])
    entity._get_function_tools = MagicMock(return_value=[])
    entity._async_retrieve_memories = AsyncMock(return_value=[])
    entity._async_retrieve_temporary_memories = AsyncMock(return_value=[])
    entity._build_system_prompt = MagicMock(return_value="system")
    user_input = SimpleNamespace(
        text="hello",
        language="en",
        conversation_id="conversation",
        as_llm_context=lambda _: SimpleNamespace(context=SimpleNamespace()),
    )
    chat_log = SimpleNamespace(
        content=[conversation.UserContent(content="hello")],
        conversation_id="conversation",
        continue_conversation=None,
    )
    return entity, user_input, chat_log


@pytest.mark.parametrize(
    ("error", "expected_type"),
    [
        (OpenAIError("provider failed"), "OpenAIError"),
        (HomeAssistantError("HA failed"), "HomeAssistantError"),
    ],
)
async def test_finished_event_is_emitted_for_handled_failures(error, expected_type):
    entity, user_input, chat_log = _message_entity_and_input()
    entity._async_handle_chat_log = AsyncMock(side_effect=error)

    await entity._async_handle_message(user_input, chat_log)

    payload = entity.hass.bus.async_fire.call_args.args[1]
    assert payload["status"] == "error"
    assert payload["error_type"] == expected_type
    entity.hass.bus.async_fire.assert_called_once()


async def test_finished_event_distinguishes_provider_success_and_local_rule():
    entity, user_input, chat_log = _message_entity_and_input()

    async def succeed(log, **_):
        log.content.append(
            conversation.AssistantContent(agent_id=entity.entity_id, content="hi")
        )
        return None

    entity._async_handle_chat_log = AsyncMock(side_effect=succeed)
    await entity._async_handle_message(user_input, chat_log)
    assert entity.hass.bus.async_fire.call_args.args[1]["status"] == "success"

    entity.hass.bus.async_fire.reset_mock()
    local_log = SimpleNamespace(
        content=[conversation.UserContent(content="local")],
        conversation_id="conversation",
    )
    entity._local_rule_result(user_input, local_log, "done")
    payload = entity.hass.bus.async_fire.call_args.args[1]
    assert payload["status"] == "local"
    assert payload["handled_locally"] is True
    entity.hass.bus.async_fire.assert_called_once()


async def test_locally_consumed_rule_has_zero_usage_run_and_archive_turn():
    entity, user_input, chat_log = _message_entity_and_input()
    usage = UsageManager(
        MemoryStorage(), MemoryStorage(), MemoryStorage(), agent_subentry_id="agent"
    )
    await usage.async_initialize()
    entity._usage = usage
    entity._archive = SimpleNamespace(async_record_turn=AsyncMock())
    entity._continuity = SimpleNamespace(async_record_success=AsyncMock())
    entity._resolve_live_guest_policy = MagicMock(
        return_value=GuestCapabilityPolicy.unrestricted()
    )
    archive_session = SimpleNamespace(session_id="archive-session")

    result = await entity._async_complete_local_rule(
        user_input,
        chat_log,
        "handled here",
        archive_session,
        "continuity-key",
        "claim-token",
        "kitchen",
    )

    run = usage.latest_run
    assert result.response.speech["plain"]["speech"] == "handled here"
    assert run is not None and run.successful is True
    assert run.request_count == 0
    assert run.total_tokens == 0
    entity._archive.async_record_turn.assert_awaited_once()
    assert entity._archive.async_record_turn.call_args.kwargs["run_id"] == run.run_id
