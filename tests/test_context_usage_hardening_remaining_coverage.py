"""Remaining resilience coverage for context usage hardening."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    context_usage_hardening as hardening,
)
from custom_components.extended_openai_conversation_responses.usage import RequestUsage


@pytest.mark.asyncio
async def test_chat_stream_traces_usage_attached_to_normal_choice(
    monkeypatch,
) -> None:
    usage = RequestUsage()
    traces: list[dict[str, Any]] = []
    chat_log = SimpleNamespace(
        content=[],
        async_trace=lambda payload: traces.append(payload),
    )
    chunk = SimpleNamespace(
        usage={
            "prompt_tokens": 12,
            "completion_tokens": 3,
            "total_tokens": 15,
        },
        choices=[SimpleNamespace()],
    )

    async def stream():
        yield chunk

    transformed = hardening.normalized_chat_stream(chat_log, stream(), usage)
    items = [item async for item in transformed]

    assert items == [chunk]
    assert usage.input_tokens == 12
    assert usage.output_tokens == 3
    assert usage.total_tokens == 15
    assert traces == [{"stats": {"input_tokens": 12, "output_tokens": 3}}]
