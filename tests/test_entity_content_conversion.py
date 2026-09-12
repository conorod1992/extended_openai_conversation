"""Focused coverage for entity chat-content conversion helpers."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import entity
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    MalformedToolArguments,
)


def test_convert_content_to_param_covers_roles_tools_and_shortened_ids() -> None:
    """Classic Chat Completions conversion preserves every supported content role."""
    normal_call = SimpleNamespace(
        id="normal-call-id",
        tool_name="normal_tool",
        tool_args={"value": 1},
    )
    malformed_call = SimpleNamespace(
        id="malformed-call-id",
        tool_name="malformed_tool",
        tool_args=MalformedToolArguments('{"value":'),
    )
    chat_content = [
        SimpleNamespace(role="system", content="system prompt"),
        SimpleNamespace(role="user", content="hello"),
        SimpleNamespace(
            role="assistant",
            content="working",
            tool_calls=[normal_call, malformed_call],
        ),
        SimpleNamespace(
            role="tool_result",
            tool_call_id="normal-call-id",
            tool_result={"b": 2, "a": 1},
        ),
        SimpleNamespace(role="unsupported", content="ignored"),
    ]

    messages = entity._convert_content_to_param(chat_content, shorten_tool_call_id=True)

    assert messages[0] == {"role": "system", "content": "system prompt"}
    assert messages[1] == {"role": "user", "content": "hello"}
    assistant = messages[2]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "working"
    assert assistant["tool_calls"][0]["id"] == entity._shorten_tool_call_id(
        "normal-call-id"
    )
    assert assistant["tool_calls"][0]["function"] == {
        "name": "normal_tool",
        "arguments": '{"value":1}',
    }
    assert assistant["tool_calls"][1]["function"] == {
        "name": "malformed_tool",
        "arguments": '{"value":',
    }
    assert messages[3] == {
        "role": "tool",
        "tool_call_id": entity._shorten_tool_call_id("normal-call-id"),
        "content": '{"a":1,"b":2}',
    }
    assert len(messages) == 4


def test_convert_content_to_param_keeps_unshortened_ids_and_empty_assistant() -> None:
    """Default conversion keeps provider IDs and permits content-free assistants."""
    messages = entity._convert_content_to_param(
        [
            SimpleNamespace(
                role="assistant",
                content="",
                tool_calls=None,
            ),
            SimpleNamespace(
                role="tool_result",
                tool_call_id="provider-call-id",
                tool_result="done",
            ),
        ]
    )

    assert messages == [
        {"role": "assistant"},
        {
            "role": "tool",
            "tool_call_id": "provider-call-id",
            "content": '"done"',
        },
    ]


def test_convert_content_to_responses_param_covers_supported_item_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Responses conversion handles tool results, attachments, native items, and calls."""

    class FakeToolResultContent:
        def __init__(self, call_id: str, result: object) -> None:
            self.tool_call_id = call_id
            self.tool_result = result
            self.role = "tool_result"
            self.content = ""

    class FakeUserContent:
        def __init__(self, content: str, attachments: list[object]) -> None:
            self.role = "user"
            self.content = content
            self.attachments = attachments

    class FakeAssistantContent:
        def __init__(
            self,
            *,
            content: str,
            native: object | None,
            tool_calls: list[object] | None,
        ) -> None:
            self.role = "assistant"
            self.content = content
            self.native = native
            self.tool_calls = tool_calls

    class NativeItem:
        def __init__(self, item_type: str, payload: dict[str, object]) -> None:
            self.type = item_type
            self._payload = payload

        def to_dict(self) -> dict[str, object]:
            return dict(self._payload)

    monkeypatch.setattr(entity.conversation, "ToolResultContent", FakeToolResultContent)
    monkeypatch.setattr(entity.conversation, "UserContent", FakeUserContent)
    monkeypatch.setattr(entity.conversation, "AssistantContent", FakeAssistantContent)

    normal_call = SimpleNamespace(
        id="call-1",
        tool_name="normal_tool",
        tool_args={"value": 1},
    )
    malformed_call = SimpleNamespace(
        id="call-2",
        tool_name="malformed_tool",
        tool_args=MalformedToolArguments('{"value":'),
    )
    native_reasoning = NativeItem(
        "reasoning",
        {
            "type": "reasoning",
            "id": "reasoning-1",
            "summary": ["summary"],
            "encrypted_content": "ciphertext",
            "provider_only": "drop-me",
        },
    )
    native_message = NativeItem(
        "message",
        {"type": "message", "id": "message-1", "content": ["native"]},
    )

    items = entity._convert_content_to_responses_param(
        [
            FakeToolResultContent("call-0", {"b": 2, "a": 1}),
            FakeUserContent("", [object()]),
            FakeAssistantContent(
                content="assistant text",
                native=native_reasoning,
                tool_calls=[normal_call, malformed_call],
            ),
            FakeAssistantContent(
                content="must not duplicate native message",
                native=native_message,
                tool_calls=None,
            ),
            SimpleNamespace(role="system", content="system prompt"),
        ]
    )

    assert items == [
        {
            "type": "function_call_output",
            "call_id": "call-0",
            "output": '{"a":1,"b":2}',
        },
        {"type": "message", "role": "user", "content": ""},
        {
            "type": "reasoning",
            "id": "reasoning-1",
            "summary": ["summary"],
            "encrypted_content": "ciphertext",
        },
        {"type": "message", "role": "assistant", "content": "assistant text"},
        {
            "type": "function_call",
            "call_id": "call-1",
            "name": "normal_tool",
            "arguments": '{"value":1}',
        },
        {
            "type": "function_call",
            "call_id": "call-2",
            "name": "malformed_tool",
            "arguments": '{"value":',
        },
        {"type": "message", "id": "message-1", "content": ["native"]},
        {"type": "message", "role": "system", "content": "system prompt"},
    ]


def test_convert_content_to_responses_param_ignores_empty_non_native_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty content without attachments, native items, or tool calls adds nothing."""

    class FakeUserContent:
        role = "user"
        content = ""
        attachments: list[object] = []

    class FakeAssistantContent:
        role = "assistant"
        content = ""
        native = None
        tool_calls: list[object] = []

    class FakeToolResultContent:
        pass

    monkeypatch.setattr(entity.conversation, "ToolResultContent", FakeToolResultContent)
    monkeypatch.setattr(entity.conversation, "UserContent", FakeUserContent)
    monkeypatch.setattr(entity.conversation, "AssistantContent", FakeAssistantContent)

    assert entity._convert_content_to_responses_param(
        [FakeUserContent(), FakeAssistantContent()]
    ) == []
