"""Final meaningful coverage for entity provider and HA-tool failure paths."""

from __future__ import annotations

from contextlib import nullcontext
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import entity as entity_module
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses import (
    function_tool_resolution,
)


class _AssistantContent:
    """Small assistant-content stand-in for provider-stream tests."""

    def __init__(self, tool_calls) -> None:
        self.content = None
        self.tool_calls = tool_calls
        self.native = None


class _ChatLog:
    """Minimal chat log needed by the provider loop."""

    def __init__(self, emitted_content) -> None:
        self.content = [SimpleNamespace(content="system")]
        self._emitted_content = list(emitted_content)
        self.added_without_tools = []
        self.unresponded_tool_results = False

    async def async_add_delta_content_stream(self, _agent_id, _stream):
        for content in self._emitted_content:
            self.content.append(content)
            yield content

    def async_add_assistant_content_without_tools(self, content) -> None:
        self.added_without_tools.append(content)
        self.content.append(content)


class _UsageFailure:
    """Usage manager that fails only while recording the successful request."""

    @staticmethod
    def current_run():
        return object()

    async def async_record_request(self, *, successful, **_kwargs) -> None:
        assert successful is True
        raise RuntimeError("usage write failed")


@pytest.fixture
def provider_loop_entity(monkeypatch):
    """Build a minimal entity whose provider loop can be exercised in isolation."""
    instance = object.__new__(ExtendedOpenAIBaseLLMEntity)
    instance._attr_entity_id = "conversation.entity-final-coverage"
    instance.subentry = SimpleNamespace(data={})
    instance.entry = SimpleNamespace(
        data={},
        runtime_data=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(return_value=object()))
            )
        ),
    )
    instance._usage = None
    instance._async_add_attachments = AsyncMock()
    instance._transform_chat_stream = lambda _chat_log, _stream, _usage: object()
    instance._truncate_message_history = AsyncMock()

    monkeypatch.setattr(
        entity_module,
        "build_provider_request_snapshot",
        lambda _options, _entry_data: SimpleNamespace(
            api_kwargs={"model": "test-model"},
            api_mode="chat_completions",
            provider_tools=[],
        ),
    )
    monkeypatch.setattr(entity_module, "_convert_content_to_param", lambda *_args: [])
    monkeypatch.setattr(entity_module, "_format_tools", lambda _tools, _mode: [])
    monkeypatch.setattr(
        entity_module,
        "current_snapshot",
        lambda: SimpleNamespace(prompt_for=lambda _tools: ""),
    )
    monkeypatch.setattr(entity_module, "bind_tool_recovery_state", lambda *_args: nullcontext())
    monkeypatch.setattr(
        entity_module, "async_streaming_speech_cleanup", lambda *_args: nullcontext()
    )
    monkeypatch.setattr(entity_module, "assert_provider_loop_completed", lambda *_args: None)
    monkeypatch.setattr(entity_module.conversation, "AssistantContent", _AssistantContent)
    monkeypatch.setattr(entity_module, "async_execute_tool_exchange", AsyncMock())
    return instance


def _loader_definition():
    return {
        "spec": {"name": entity_module.FUNCTION_GROUP_LOADER_TOOL_NAME},
        "function": {"type": "function_group_loader"},
    }


def _loader_call():
    return SimpleNamespace(
        id="loader-call",
        tool_name=entity_module.FUNCTION_GROUP_LOADER_TOOL_NAME,
        tool_args={"groups": ["lighting"]},
    )


@pytest.mark.asyncio
async def test_successful_request_usage_failure_appends_unresolved_results(
    provider_loop_entity, monkeypatch
) -> None:
    """A post-request usage failure must not strand the provider's tool call."""
    call = SimpleNamespace(id="tool-call", tool_name="demo", tool_args={})
    chat_log = _ChatLog([_AssistantContent([call])])
    provider_loop_entity._usage = _UsageFailure()
    append_unresolved = AsyncMock()
    monkeypatch.setattr(entity_module, "append_unresolved_tool_results", append_unresolved)

    with pytest.raises(RuntimeError, match="usage write failed"):
        await provider_loop_entity._async_handle_chat_log(
            chat_log,
            function_tools=[],
            exposed_entities=[],
        )

    append_unresolved.assert_called_once()
    args, kwargs = append_unresolved.call_args
    assert args[:3] == (chat_log, provider_loop_entity.entity_id, [call])
    assert isinstance(kwargs["error"], RuntimeError)


@pytest.mark.asyncio
async def test_missing_function_group_loader_returns_clean_tool_error(
    provider_loop_entity,
) -> None:
    """A loader request without a runtime loader is returned as a tool error."""
    call = _loader_call()
    chat_log = _ChatLog([_AssistantContent([call])])

    result = await provider_loop_entity._async_handle_chat_log(
        chat_log,
        function_tools=[_loader_definition()],
        exposed_entities=[],
        function_group_loader=None,
    )

    assert result is None
    assert len(chat_log.added_without_tools) == 1
    tool_result = chat_log.added_without_tools[0]
    assert tool_result.tool_call_id == call.id
    assert tool_result.tool_name == call.tool_name
    payload = json.loads(tool_result.tool_result["result"])
    assert payload == {
        "status": "error",
        "error": "Function-group loading is unavailable",
    }


@pytest.mark.asyncio
async def test_function_group_loader_failure_appends_unresolved_results(
    provider_loop_entity, monkeypatch
) -> None:
    """A loader exception marks that call unresolved before propagating."""
    call = _loader_call()
    chat_log = _ChatLog([_AssistantContent([call])])
    append_unresolved = AsyncMock()
    monkeypatch.setattr(entity_module, "append_unresolved_tool_results", append_unresolved)

    def failing_loader(_groups):
        raise RuntimeError("loader failed")

    with pytest.raises(RuntimeError, match="loader failed"):
        await provider_loop_entity._async_handle_chat_log(
            chat_log,
            function_tools=[_loader_definition()],
            exposed_entities=[],
            function_group_loader=failing_loader,
        )

    append_unresolved.assert_called_once()
    args, kwargs = append_unresolved.call_args
    assert args[:3] == (chat_log, provider_loop_entity.entity_id, [call])
    assert kwargs["failed_call_id"] == call.id
    assert isinstance(kwargs["error"], RuntimeError)


@pytest.mark.asyncio
async def test_ha_tool_without_user_id_skips_auth_lookup_and_executes(monkeypatch) -> None:
    """An HA tool can execute when the LLM context has no authenticated user id."""
    instance = object.__new__(ExtendedOpenAIBaseLLMEntity)
    instance._attr_entity_id = "conversation.entity-final-coverage"
    auth_lookup = AsyncMock(side_effect=AssertionError("user lookup should be skipped"))
    instance.hass = SimpleNamespace(auth=SimpleNamespace(async_get_user=auth_lookup))

    live_tool = SimpleNamespace(async_call=AsyncMock(return_value="executed"))
    monkeypatch.setattr(entity_module, "is_ha_tool", lambda _tool: True)
    monkeypatch.setattr(entity_module, "reference_key", lambda _reference: "ha-ref")
    monkeypatch.setattr(
        entity_module,
        "current_snapshot",
        lambda: SimpleNamespace(
            caller_provided=True,
            tools={"ha-ref": live_tool},
        ),
    )
    monkeypatch.setattr(
        function_tool_resolution,
        "latest_function_tool_for_execution",
        lambda _entity, _tool: None,
    )

    tool_input = SimpleNamespace(id="ha-call", tool_name="ha_demo", tool_args={})
    result = await instance._execute_function_tool(
        {"function": {"type": "ha_demo"}},
        tool_input,
        SimpleNamespace(context=None),
        [],
    )

    auth_lookup.assert_not_awaited()
    live_tool.async_call.assert_awaited_once_with(tool_input)
    assert result.tool_call_id == tool_input.id
    assert result.tool_name == tool_input.tool_name
    assert result.tool_result == {"result": "executed"}
