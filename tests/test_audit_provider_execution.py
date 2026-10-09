"""Behavioral regressions for the EOAI execution audit."""

from types import SimpleNamespace

import aiohttp
import pytest
import zstandard

from custom_components.extended_openai_conversation_responses.functions import web
from custom_components.extended_openai_conversation_responses.speech import _SpeechDeltaListener
from custom_components.extended_openai_conversation_responses.usage import RequestUsage
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.template import Template


def test_response_json_cannot_be_supplied_by_caller(hass):
    template = Template("{{ value_json.success }}", hass)
    with pytest.raises(TemplateError):
        web._render_value(template, "<html>login</html>", {"value_json": {"success": True}})
    assert web._render_value(template, '{"success":false}', {"value_json": {"success": True}}) == "False"


def test_zstandard_fallback_requires_complete_concatenated_frames(monkeypatch):
    monkeypatch.setattr(web, "_ZSTD_COMPAT", zstandard)
    frame = zstandard.ZstdCompressor().compress(b"hello")
    assert web._decode_zstd(frame + frame, 10) == b"hellohello"
    for body in (frame[:-1], frame + frame[:-1]):
        with pytest.raises(aiohttp.ClientPayloadError, match="Incomplete"):
            web._decode_zstd(body, 10)


def test_role_and_content_share_speech_cleanup():
    heard = []
    listener = _SpeechDeltaListener(lambda _log, delta: heard.append(delta), {"speech_strip_markdown": True, "speech_strip_urls": True})
    listener(None, {"role": "assistant", "content": "**Ready** https://example.com"})
    listener.flush(None)
    assert "".join(delta.get("content", "") for delta in heard).strip() == "Ready"
    assert heard[0]["role"] == "assistant"


async def test_optional_embedding_failure_does_not_fail_successful_chat():
    from tests.test_usage import _manager

    manager = await _manager()
    async with manager.async_run() as run:
        await manager.async_record_request(successful=False, request_stage="embeddings", api_mode="embeddings")
        await manager.async_record_request(successful=True, request_stage="initial")
    assert run.successful
    assert run.failed_request_count == 1


async def test_token_limit_consumes_trailing_usage():
    from tests.test_provider_stream_terminals import FakeStream, _chat_chunk, _entity
    from custom_components.extended_openai_conversation_responses.exceptions import TokenLengthExceededError

    usage = RequestUsage()
    stream = FakeStream([_chat_chunk(content="partial", finish_reason="length"),
        SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=500, total_tokens=1700))])
    with pytest.raises(TokenLengthExceededError):
        async for _ in _entity()._transform_chat_stream(SimpleNamespace(async_trace=lambda _value: None), stream, usage):
            pass
    assert usage.input_tokens == 1200
    assert usage.output_tokens == 500


async def test_structured_result_is_bounded_after_serialization():
    from custom_components.extended_openai_conversation_responses.conversation import ExtendedOpenAIAgentEntity
    from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import tool_result_data
    from custom_components.extended_openai_conversation_responses.runtime_hardening import MAX_MODEL_TOOL_RESULT_CHARACTERS

    class StructuredEntity(ExtendedOpenAIAgentEntity):
        async def _async_dispatch_function_tool(self, *_args):
            return SimpleNamespace(tool_result={"result": [{"value": "x" * 1000}] * 400})

    content = await object.__new__(StructuredEntity)._execute_function_tool({}, {}, None, [])
    assert len(tool_result_data(content)["result"]) <= MAX_MODEL_TOOL_RESULT_CHARACTERS


async def test_history_is_trimmed_before_provider_request(hass):
    from unittest.mock import AsyncMock
    from tests.test_responses_api import FakeStream, _event, _completed_event
    from custom_components.extended_openai_conversation_responses.entity import ExtendedOpenAIBaseLLMEntity
    from homeassistant.components import conversation

    create = AsyncMock(return_value=FakeStream([
        _event("response.output_item.added", item=SimpleNamespace(type="message")),
        _event("response.output_text.delta", delta="Done"), _completed_event()]))
    entity = object.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(runtime_data=SimpleNamespace(responses=SimpleNamespace(create=create)))
    entity.subentry = SimpleNamespace(data={"chat_model":"gpt-5.6-luna", "api_mode":"responses", "context_threshold":150, "context_truncate_strategy":"keep_recent"})
    entity.hass = hass
    entity.entity_id = "conversation.audit"
    log = conversation.ChatLog(hass, "audit-context")
    log.async_add_user_content(conversation.UserContent(content="old-history-" * 100))
    log.async_add_assistant_content_without_tools(conversation.AssistantContent(agent_id=entity.entity_id, content="Old answer"))
    log.async_add_user_content(conversation.UserContent(content="current-question-" * 40))
    await entity._async_handle_chat_log(log, [], [])
    serialized = str(create.await_args.kwargs["input"])
    assert "old-history-" not in serialized
    assert "current-question-" in serialized
