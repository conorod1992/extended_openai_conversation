"""Regression tests for runtime failure containment."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    runtime_failure_hardening as hardening,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from homeassistant.exceptions import HomeAssistantError


class FakeHass:
    def __init__(self) -> None:
        self.data: dict = {}


class FakeUsage:
    def __init__(self) -> None:
        self.failed: list[str] = []

    def mark_current_run_failed(self, error_type: str) -> None:
        self.failed.append(error_type)


class FakeConversationEntity:
    def __init__(self) -> None:
        self._usage = FakeUsage()
        self.events: list[tuple[str, str | None]] = []

    def _get_exposed_entities(self):
        raise HomeAssistantError("broken request preparation")

    def _fire_conversation_finished(
        self, _user_input, _chat_log, *, status: str, error_type: str | None = None
    ) -> None:
        self.events.append((status, error_type))


class FakeArchiveEntity:
    def _effective_guest_policy(self):
        return SimpleNamespace(guest_active=False)

    async def _async_execute_archive_tool(self, _operation: str, _arguments: dict):
        raise OSError("archive store unavailable")

    def _tool_result(self, _tool_input, result):
        return result


@pytest.mark.asyncio
async def test_usage_initialization_failure_falls_back_to_shared_volatile_manager(
    monkeypatch,
) -> None:
    hass = FakeHass()
    calls = 0

    async def failing_getter(_hass, _entry_id: str, _subentry_id: str):
        nonlocal calls
        calls += 1
        raise OSError("usage store unavailable")

    monkeypatch.setattr(hardening, "_ORIGINAL_ASYNC_GET_USAGE", failing_getter)

    manager = await hardening.async_get_usage_safely(hass, "entry", "agent")
    same_manager = await hardening.async_get_usage_safely(hass, "entry", "agent")

    assert manager is same_manager
    assert calls == 1
    await manager.async_record_conversation()
    assert manager.totals.conversation_count == 1


@pytest.mark.asyncio
async def test_request_preparation_home_assistant_error_returns_assist_error() -> None:
    hardening.install_runtime_failure_hardening()
    entity = FakeConversationEntity()
    user_input = SimpleNamespace(
        language="en",
        conversation_id="conversation-id",
        as_llm_context=lambda _domain: SimpleNamespace(),
    )

    result = await ExtendedOpenAIAgentEntity._async_handle_message(
        entity, user_input, SimpleNamespace()
    )

    assert result.conversation_id == "conversation-id"
    assert entity._usage.failed == ["HomeAssistantError"]
    assert entity.events == [("error", "HomeAssistantError")]


@pytest.mark.asyncio
async def test_unexpected_archive_failure_is_labeled_as_archive() -> None:
    hardening.install_runtime_failure_hardening()
    entity = FakeArchiveEntity()
    tool = {"function": {"type": "archive", "operation": "search"}}
    tool_input = SimpleNamespace(id="call", tool_name="archive_search", tool_args={})

    result = await ExtendedOpenAIAgentEntity._execute_function_tool(
        entity, tool, tool_input, None, []
    )

    assert result == {
        "status": "unavailable",
        "error": "Conversation Archive is temporarily unavailable",
    }


@pytest.mark.asyncio
async def test_chat_stream_repairs_tool_call_id_received_in_later_delta() -> None:
    hardening.install_runtime_failure_hardening()
    entity = SimpleNamespace(subentry=SimpleNamespace(data={}))
    chat_log = SimpleNamespace(async_trace=lambda _trace: None)

    async def stream():
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id=None,
                                function=SimpleNamespace(
                                    name="late_id_tool", arguments='{"value":'
                                ),
                            )
                        ],
                    ),
                    finish_reason=None,
                )
            ]
        )
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call_late_123",
                                function=SimpleNamespace(name=None, arguments="1}"),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ]
        )

    output = [
        item
        async for item in ExtendedOpenAIBaseLLMEntity._transform_chat_stream(
            entity, chat_log, stream()
        )
    ]
    tool_payload = next(item for item in output if item.get("tool_calls"))
    tool_call = tool_payload["tool_calls"][0]

    assert tool_call.id == "call_late_123"
    assert tool_call.tool_name == "late_id_tool"
    assert tool_call.tool_args == json.loads('{"value":1}')
