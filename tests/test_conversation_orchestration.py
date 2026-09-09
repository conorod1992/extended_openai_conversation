"""Conversation pipeline orchestration regressions."""

from __future__ import annotations

from contextlib import asynccontextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import conversation as conversation_module
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
    _ACTIVE_ARCHIVE,
    _ACTIVE_FUNCTION_GROUP_SESSION,
    _ACTIVE_MEMORY_SESSION,
    _ACTIVE_SCOPE,
    _ACTIVE_TEMPORARY_SCOPE,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.local_intents import (
    LocalIntentResult,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RuleEvaluation,
    RuleMatch,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.helpers import intent


class _UsageRecorder:
    """Minimal deterministic usage context for orchestration assertions."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @asynccontextmanager
    async def async_run(self, **kwargs):
        self.calls.append(dict(kwargs))
        yield SimpleNamespace(run_id="run-1", successful=True)


def _pipeline_fixture(monkeypatch, *, text: str = "hello"):
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=MagicMock()))
    entity.entry = SimpleNamespace(entry_id="entry")
    entity.subentry = SimpleNamespace(subentry_id="agent", data={})
    entity._attr_entity_id = "conversation.agent"
    entity._continuity = SimpleNamespace(async_record_success=AsyncMock())
    entity._function_groups_runtime = None
    entity._request_rules = None
    entity._request_rule_runtime = None
    entity._usage = None
    entity._archive = None
    entity._async_begin_archive_session = AsyncMock(return_value=None)
    entity._async_handle_message_with_ha_tools = AsyncMock()

    user_input = SimpleNamespace(
        text=text,
        language="en",
        conversation_id=None,
        context=Context(user_id="alice"),
    )
    chat_log = SimpleNamespace(
        content=[
            conversation.SystemContent(content="system"),
            conversation.UserContent(content=text),
        ],
        conversation_id="conversation-1",
    )
    resolution = SimpleNamespace(
        conversation_id="conversation-1",
        key="device:kitchen",
        claim_token="claim-token",
        history=(),
    )
    policy = GuestCapabilityPolicy.unrestricted()
    scope = user_scope("alice", source="test", device_id="kitchen")
    llm_context = SimpleNamespace(context=user_input.context)

    monkeypatch.setattr(
        conversation_module,
        "async_get_chat_session",
        lambda *_args, **_kwargs: nullcontext(SimpleNamespace()),
    )
    monkeypatch.setattr(
        conversation_module,
        "async_get_chat_log",
        lambda *_args, **_kwargs: nullcontext(chat_log),
    )

    async def process():
        return await entity._async_process_claimed(
            user_input,
            llm_context,
            policy,
            scope,
            "kitchen",
            15,
            resolution,
        )

    return entity, user_input, chat_log, policy, process


async def test_consumed_request_rule_bypasses_local_intent_and_provider(monkeypatch):
    entity, _user_input, _chat_log, _policy, process = _pipeline_fixture(
        monkeypatch, text="good night"
    )
    entity._request_rules = object()
    entity._request_rule_runtime = SimpleNamespace(effective_options=MagicMock())

    evaluation = RuleEvaluation(
        match=RuleMatch(
            {"id": "good-night", "name": "Good night"},
            "good night",
            False,
            100.0,
        ),
        consume=True,
        response="Handled locally",
    )
    evaluate_rule = AsyncMock(return_value=evaluation)
    try_local_intent = AsyncMock()
    monkeypatch.setattr(conversation_module, "async_evaluate_rule", evaluate_rule)
    monkeypatch.setattr(
        conversation_module, "async_try_handle_local_intent", try_local_intent
    )

    result = await process()

    assert result.response.speech["plain"]["speech"] == "Handled locally"
    evaluate_rule.assert_awaited_once()
    try_local_intent.assert_not_awaited()
    entity._async_handle_message_with_ha_tools.assert_not_awaited()
    entity._continuity.async_record_success.assert_awaited_once()
    payload = entity.hass.bus.async_fire.call_args.args[1]
    assert payload["status"] == "local"
    assert payload["handled_locally"] is True


async def test_local_intent_bypasses_provider_and_records_continuity(monkeypatch):
    entity, _user_input, _chat_log, _policy, process = _pipeline_fixture(
        monkeypatch, text="is the kitchen light on"
    )
    local_response = intent.IntentResponse(language="en")
    local_response.async_set_speech("The kitchen light is on.")
    try_local_intent = AsyncMock(
        return_value=LocalIntentResult(
            response=local_response,
            intent_name="HassGetState",
        )
    )
    monkeypatch.setattr(
        conversation_module, "async_try_handle_local_intent", try_local_intent
    )

    result = await process()

    assert result.response is local_response
    assert result.continue_conversation is False
    try_local_intent.assert_awaited_once()
    assert try_local_intent.call_args.kwargs["guest_active"] is False
    entity._async_handle_message_with_ha_tools.assert_not_awaited()
    entity._continuity.async_record_success.assert_awaited_once()
    payload = entity.hass.bus.async_fire.call_args.args[1]
    assert payload["status"] == "local"
    assert payload["handled_locally"] is True


async def test_provider_success_records_usage_archive_and_continuity(monkeypatch):
    entity, user_input, chat_log, policy, process = _pipeline_fixture(monkeypatch)
    usage = _UsageRecorder()
    archive = SimpleNamespace(async_record_turn=AsyncMock())
    archive_session = SimpleNamespace(session_id="archive-1")
    entity._usage = usage
    entity._archive = archive
    entity._async_begin_archive_session = AsyncMock(return_value=archive_session)
    entity._effective_guest_policy = MagicMock(return_value=policy)

    monkeypatch.setattr(
        conversation_module,
        "async_try_handle_local_intent",
        AsyncMock(return_value=None),
    )

    provider_response = intent.IntentResponse(language="en")
    provider_response.async_set_speech("Provider response")
    expected = conversation.ConversationResult(
        response=provider_response,
        conversation_id="conversation-1",
    )

    async def provider_success(_user_input, log, _request_options):
        log.content.append(
            conversation.AssistantContent(
                agent_id=entity.entity_id,
                content="Provider response",
            )
        )
        return expected

    entity._async_handle_message_with_ha_tools = AsyncMock(
        side_effect=provider_success
    )

    result = await process()

    assert result is expected
    assert usage.calls == [
        {
            "home_assistant_conversation_id": "conversation-1",
            "source_device_id": "kitchen",
        }
    ]
    archive.async_record_turn.assert_awaited_once_with(
        "archive-1",
        run_id="run-1",
        user_text=user_input.text,
        assistant_text="Provider response",
        successful=True,
    )
    entity._continuity.async_record_success.assert_awaited_once_with(
        "device:kitchen",
        "claim-token",
        chat_log.content,
    )


async def test_unexpected_failure_restores_request_scoped_context(monkeypatch):
    entity, _user_input, _chat_log, _policy, process = _pipeline_fixture(monkeypatch)
    monkeypatch.setattr(
        conversation_module,
        "async_try_handle_local_intent",
        AsyncMock(return_value=None),
    )
    entity._async_handle_message_with_ha_tools = AsyncMock(
        side_effect=RuntimeError("provider wrapper failed")
    )

    outer_scope = user_scope("outer", source="test", device_id="hall")
    outer_function_group = object()
    tokens = [
        (_ACTIVE_SCOPE, _ACTIVE_SCOPE.set(outer_scope)),
        (_ACTIVE_ARCHIVE, _ACTIVE_ARCHIVE.set(("outer", "archive"))),
        (_ACTIVE_TEMPORARY_SCOPE, _ACTIVE_TEMPORARY_SCOPE.set("outer-temp")),
        (
            _ACTIVE_FUNCTION_GROUP_SESSION,
            _ACTIVE_FUNCTION_GROUP_SESSION.set(outer_function_group),
        ),
        (_ACTIVE_MEMORY_SESSION, _ACTIVE_MEMORY_SESSION.set(("outer-memory", 5))),
    ]
    try:
        with pytest.raises(RuntimeError, match="provider wrapper failed"):
            await process()

        assert _ACTIVE_SCOPE.get() is outer_scope
        assert _ACTIVE_ARCHIVE.get() == ("outer", "archive")
        assert _ACTIVE_TEMPORARY_SCOPE.get() == "outer-temp"
        assert _ACTIVE_FUNCTION_GROUP_SESSION.get() is outer_function_group
        assert _ACTIVE_MEMORY_SESSION.get() == ("outer-memory", 5)
    finally:
        for context_var, token in reversed(tokens):
            context_var.reset(token)
