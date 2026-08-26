"""Tests for provider-reported persistent usage statistics."""

from collections.abc import AsyncIterator
from copy import deepcopy
from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    UsageManager,
    extract_usage,
)


class FakeStream:
    def __init__(self, items):
        self.items = items

    async def __aiter__(self) -> AsyncIterator:
        for item in self.items:
            yield item


class FakeStorage:
    """In-memory persistence boundary."""

    data: dict | None = None

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


class FailingStorage(FakeStorage):
    async def async_save(self, data):
        raise OSError("disk full")


async def _manager(storage: FakeStorage | None = None) -> UsageManager:
    manager = UsageManager(storage or FakeStorage())
    await manager.async_initialize()
    return manager


async def test_single_request_usage_and_conversation_count() -> None:
    """A conversation and its one API request remain distinct counters."""
    manager = await _manager()
    await manager.async_record_conversation()
    await manager.async_record_request(
        successful=True,
        usage=RequestUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )

    assert manager.as_dict() == {
        "conversation_count": 1,
        "api_request_count": 1,
        "successful_request_count": 1,
        "failed_request_count": 0,
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "details": {},
    }


async def test_multi_round_tool_usage_is_aggregated() -> None:
    """Every provider request in one tool-call conversation is accumulated."""
    manager = await _manager()
    await manager.async_record_conversation()
    await manager.async_record_request(
        successful=True,
        usage=RequestUsage(
            input_tokens=20,
            output_tokens=3,
            total_tokens=23,
            cached_input_tokens=4,
        ),
    )
    await manager.async_record_request(
        successful=True,
        usage=RequestUsage(
            input_tokens=30,
            output_tokens=7,
            total_tokens=37,
            reasoning_tokens=2,
        ),
    )

    assert manager.totals.conversation_count == 1
    assert manager.totals.api_request_count == 2
    assert manager.totals.total_tokens == 60
    assert manager.totals.cached_input_tokens == 4
    assert manager.totals.reasoning_tokens == 2


async def test_missing_usage_metadata_degrades_gracefully() -> None:
    """Compatible providers may omit usage while the successful request is counted."""
    manager = await _manager()
    await manager.async_record_request(successful=True, usage=extract_usage(None))

    assert manager.totals.successful_request_count == 1
    assert manager.totals.total_tokens == 0


async def test_failed_api_request_is_counted_without_tokens() -> None:
    """A rejected request increments request and failure counters only."""
    manager = await _manager()
    await manager.async_record_request(successful=False)

    assert manager.totals.api_request_count == 1
    assert manager.totals.failed_request_count == 1
    assert manager.totals.successful_request_count == 0
    assert manager.totals.total_tokens == 0


async def test_usage_persists_across_reload() -> None:
    """Cumulative statistics survive manager recreation."""
    storage = FakeStorage()
    first = await _manager(storage)
    await first.async_record_conversation()
    await first.async_record_request(
        successful=True,
        usage=RequestUsage(input_tokens=8, output_tokens=2, total_tokens=10),
    )

    second = await _manager(storage)
    assert second.totals.conversation_count == 1
    assert second.totals.total_tokens == 10


def test_chat_and_responses_detailed_usage_fields() -> None:
    """Known detailed token fields are normalized without provider assumptions."""
    chat = extract_usage(
        SimpleNamespace(
            prompt_tokens=12,
            completion_tokens=6,
            total_tokens=18,
            prompt_tokens_details=SimpleNamespace(cached_tokens=5),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=3),
        )
    )
    responses = extract_usage(
        {
            "input_tokens": 7,
            "output_tokens": 4,
            "input_tokens_details": {"cached_tokens": 2},
            "output_tokens_details": {"reasoning_tokens": 1},
        }
    )

    assert (chat.cached_input_tokens, chat.reasoning_tokens) == (5, 3)
    assert (responses.total_tokens, responses.cached_input_tokens) == (11, 2)


async def test_chat_stream_consumes_usage_chunk_after_stop() -> None:
    """Chat Completions does not stop before the final usage-only chunk."""
    stop = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content="OK", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    final_usage = SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=9, completion_tokens=2, total_tokens=11),
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    request_usage = RequestUsage()
    chat_log = SimpleNamespace(async_trace=lambda _: None)

    deltas = [
        delta
        async for delta in entity._transform_chat_stream(
            chat_log, FakeStream([stop, final_usage]), request_usage
        )
    ]

    assert any(delta.get("content") == "OK" for delta in deltas)
    assert request_usage.total_tokens == 11


async def test_run_lifecycle_groups_multiple_requests_and_finalizes_once() -> None:
    manager = await _manager()
    async with manager.async_run(
        home_assistant_conversation_id="conversation-1",
        source_device_id="satellite-1",
    ) as run:
        await manager.async_record_request(
            successful=True,
            usage=RequestUsage(input_tokens=10, output_tokens=2, total_tokens=12),
            provider="openai",
            model="gpt-5-mini",
            api_mode="responses",
            request_stage="initial",
            tool_calls_requested=1,
        )
        await manager.async_record_request(
            successful=False,
            provider="openai",
            model="gpt-5-mini",
            api_mode="responses",
            request_stage="after_tool",
            error_type="APIError",
        )
    assert manager.totals.conversation_count == 1
    assert run.request_count == 2
    assert run.tool_call_count == 1
    assert run.successful is False
    assert manager.today_summary()["run_count"] == 1
    assert manager.today_summary()["total_tokens"] == 12
    assert manager.breakdowns()["providers"] == {"openai": 12}
    await manager.async_clear_details(confirm=True)
    assert manager.breakdowns()["providers"] == {"openai": 12}
    assert manager.today_summary()["total_tokens"] == 12


async def test_exception_run_is_finalized_without_request_metadata() -> None:
    manager = await _manager()
    try:
        async with manager.async_run() as run:
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert run.completed_at is not None
    assert run.error_type == "RuntimeError"
    assert run.request_count == 0
    assert manager.totals.conversation_count == 1


async def test_persistence_failure_keeps_successful_request_and_run_in_memory(
    caplog,
) -> None:
    manager = UsageManager(
        FailingStorage(), FailingStorage(), FailingStorage(), agent_subentry_id="agent"
    )
    await manager.async_initialize()

    async with manager.async_run() as run:
        await manager.async_record_request(
            successful=True,
            usage=RequestUsage(input_tokens=3, output_tokens=2, total_tokens=5),
        )

    assert run.successful is True
    assert manager.totals.api_request_count == 1
    assert manager.totals.conversation_count == 1
    assert manager.totals.total_tokens == 5
    assert "Unable to persist usage" in caplog.text
