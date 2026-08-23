"""Direct processing action tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.services import (
    async_setup_services,
)
from homeassistant.components import conversation
from homeassistant.core import Context, ServiceCall
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
