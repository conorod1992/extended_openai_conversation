"""Concurrent provider-request accounting regression coverage."""

import asyncio

from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    UsageManager,
)


class FakeStorage:
    """In-memory persistence boundary."""

    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


async def test_concurrent_provider_requests_are_counted_once_in_one_run() -> None:
    """Concurrent provider completions remain one run with exact request totals."""
    manager = UsageManager(FakeStorage(), FakeStorage(), FakeStorage())
    await manager.async_initialize()

    async with manager.async_run() as run:
        await asyncio.gather(
            *(
                manager.async_record_request(
                    successful=True,
                    usage=RequestUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                    provider="openai",
                    model="gpt-test",
                    api_mode="responses",
                )
                for _ in range(12)
            )
        )

    day = next(iter(manager.daily.values()))
    assert manager.totals.conversation_count == 1
    assert manager.totals.api_request_count == 12
    assert manager.totals.total_tokens == 24
    assert run.request_count == 12
    assert run.total_tokens == 24
    assert day["run_count"] == 1
    assert day["api_request_count"] == 12
    assert day["total_tokens"] == 24
    assert len(manager.requests) == 12
    assert len(manager.runs) == 1
