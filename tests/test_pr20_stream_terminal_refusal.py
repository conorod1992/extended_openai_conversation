"""Regression tests for streaming refusal and terminal-state handling."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    TokenLengthExceededError,
)
from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderStreamError,
)
from homeassistant.exceptions import HomeAssistantError


class FakeStream:
    """Async iterator over fake provider stream events."""

    def __init__(self, events: list[Any]) -> None:
        self.events = events
        self._index = 0

    def __aiter__(self) -> FakeStream:
        return self

    async def __anext__(self) -> Any:
        if self._index >= len(self.events):
            raise StopAsyncIteration
        event = self.events[self._index]
        self._index += 1
        return event


def _event(event_type: str, **kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(type=event_type, **kwargs)


def _entity() -> ExtendedOpenAIBaseLLMEntity:
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.subentry = SimpleNamespace(data={})
    return entity


def _chat_chunk(
    *,
    content: str | None = None,
    refusal: str | None = None,
    finish_reason: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=content,
                    refusal=refusal,
                    tool_calls=None,
                ),
                finish_reason=finish_reason,
            )
        ],
        usage=None,
    )


async def test_chat_completed_terminal_is_accepted() -> None:
    """A normal Chat Completions stop finish still succeeds."""
    stream = FakeStream(
        [
            _chat_chunk(content="Done"),
            _chat_chunk(finish_reason="stop"),
        ]
    )

    deltas = [
        delta
        async for delta in _entity()._transform_chat_stream(SimpleNamespace(), stream)
    ]

    assert deltas == [{"role": "assistant"}, {"content": "Done"}]


async def test_chat_premature_eof_is_error() -> None:
    """Chat EOF without a finish reason is not accepted as a completed response."""
    stream = FakeStream([_chat_chunk(content="Partial answer")])

    with pytest.raises(HomeAssistantError, match="before a terminal finish reason"):
        _ = [
            delta
            async for delta in _entity()._transform_chat_stream(
                SimpleNamespace(), stream
            )
        ]


async def test_chat_deprecated_function_call_finish_is_terminal() -> None:
    """The API's deprecated function_call finish value is still a valid terminal."""
    stream = FakeStream([_chat_chunk(finish_reason="function_call")])

    deltas = [
        delta
        async for delta in _entity()._transform_chat_stream(SimpleNamespace(), stream)
    ]

    assert deltas == [{"role": "assistant"}]


async def test_chat_stream_surfaces_refusal_text() -> None:
    """Chat Completions refusal deltas become assistant-visible content."""
    stream = FakeStream(
        [
            _chat_chunk(refusal="I can't help with that."),
            _chat_chunk(finish_reason="stop"),
        ]
    )

    deltas = [
        delta
        async for delta in _entity()._transform_chat_stream(SimpleNamespace(), stream)
    ]

    assert deltas == [
        {"role": "assistant"},
        {"content": "I can't help with that."},
    ]


async def test_chat_content_filter_without_refusal_is_error() -> None:
    """A filtered empty Chat completion is not treated as normal success."""
    stream = FakeStream([_chat_chunk(finish_reason="content_filter")])
    transformed = _entity()._transform_chat_stream(SimpleNamespace(), stream)

    try:
        assert await anext(transformed) == {"role": "assistant"}
        with pytest.raises(HomeAssistantError, match="content filter"):
            await anext(transformed)
    finally:
        await transformed.aclose()


async def test_chat_content_filter_with_refusal_preserves_refusal() -> None:
    """Provider refusal text remains usable even when finish_reason is filtered."""
    stream = FakeStream(
        [
            _chat_chunk(refusal="I can't provide that."),
            _chat_chunk(finish_reason="content_filter"),
        ]
    )

    deltas = [
        delta
        async for delta in _entity()._transform_chat_stream(SimpleNamespace(), stream)
    ]

    assert deltas[-1] == {"content": "I can't provide that."}


async def test_responses_stream_surfaces_refusal_delta() -> None:
    """Responses refusal deltas become assistant-visible content."""
    stream = FakeStream(
        [
            _event(
                "response.output_item.added",
                item=SimpleNamespace(type="message"),
            ),
            _event(
                "response.refusal.delta",
                output_index=0,
                content_index=0,
                delta="I can't help with that.",
            ),
            _event(
                "response.refusal.done",
                output_index=0,
                content_index=0,
                refusal="I can't help with that.",
            ),
            _event(
                "response.completed",
                response=SimpleNamespace(usage=None),
            ),
        ]
    )

    deltas = [
        delta
        async for delta in _entity()._transform_responses_stream(
            SimpleNamespace(), stream
        )
    ]

    assert deltas == [
        {"role": "assistant"},
        {"content": "I can't help with that."},
    ]


async def test_responses_refusal_done_fills_missing_delta_tail() -> None:
    """The finalized refusal fills any text not delivered as deltas."""
    stream = FakeStream(
        [
            _event(
                "response.refusal.delta",
                output_index=0,
                content_index=0,
                delta="I can't",
            ),
            _event(
                "response.refusal.done",
                output_index=0,
                content_index=0,
                refusal="I can't help with that.",
            ),
            _event(
                "response.completed",
                response=SimpleNamespace(usage=None),
            ),
        ]
    )

    deltas = [
        delta
        async for delta in _entity()._transform_responses_stream(
            SimpleNamespace(), stream
        )
    ]

    assert [delta["content"] for delta in deltas] == [
        "I can't",
        " help with that.",
    ]


async def test_responses_premature_eof_is_error() -> None:
    """Responses EOF without an explicit terminal event fails closed."""
    stream = FakeStream(
        [
            _event(
                "response.output_item.added",
                item=SimpleNamespace(type="message"),
            ),
            _event(
                "response.output_text.delta",
                output_index=0,
                content_index=0,
                delta="Partial answer",
            ),
            _event(
                "response.output_item.done",
                item=SimpleNamespace(type="message", content=[]),
            ),
        ]
    )

    with pytest.raises(HomeAssistantError, match="before a terminal event"):
        _ = [
            delta
            async for delta in _entity()._transform_responses_stream(
                SimpleNamespace(), stream
            )
        ]


async def test_responses_completed_terminal_is_accepted() -> None:
    """A normal completed Responses stream still succeeds."""
    stream = FakeStream(
        [
            _event(
                "response.output_text.delta",
                output_index=0,
                content_index=0,
                delta="Done",
            ),
            _event(
                "response.completed",
                response=SimpleNamespace(usage=None),
            ),
        ]
    )

    deltas = [
        delta
        async for delta in _entity()._transform_responses_stream(
            SimpleNamespace(), stream
        )
    ]

    assert deltas == [{"content": "Done"}]


async def test_responses_incomplete_terminal_is_error() -> None:
    """A non-token-limit incomplete Responses terminal is surfaced as failure."""
    stream = FakeStream(
        [
            _event(
                "response.incomplete",
                response=SimpleNamespace(
                    usage=None,
                    incomplete_details=SimpleNamespace(reason="content_filter"),
                ),
            )
        ]
    )

    with pytest.raises(HomeAssistantError, match="response incomplete: content_filter"):
        _ = [
            delta
            async for delta in _entity()._transform_responses_stream(
                SimpleNamespace(), stream
            )
        ]


async def test_responses_token_limit_keeps_specific_error() -> None:
    """The Responses token-limit terminal keeps the existing typed error."""
    stream = FakeStream(
        [
            _event(
                "response.incomplete",
                response=SimpleNamespace(
                    usage=None,
                    incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                ),
            )
        ]
    )

    with pytest.raises(TokenLengthExceededError):
        _ = [
            delta
            async for delta in _entity()._transform_responses_stream(
                SimpleNamespace(), stream
            )
        ]


async def test_responses_failed_terminal_is_provider_error() -> None:
    """A response.failed event remains an OpenAI-compatible provider failure."""
    stream = FakeStream(
        [
            _event(
                "response.failed",
                response=SimpleNamespace(
                    id="resp_failed",
                    error=SimpleNamespace(
                        message="The model failed to generate a response.",
                        code="server_error",
                    ),
                ),
            )
        ]
    )

    with pytest.raises(ProviderStreamError, match="OpenAI response failed") as exc_info:
        _ = [
            delta
            async for delta in _entity()._transform_responses_stream(
                SimpleNamespace(), stream
            )
        ]

    assert exc_info.value.code == "server_error"
    assert exc_info.value.response_id == "resp_failed"
