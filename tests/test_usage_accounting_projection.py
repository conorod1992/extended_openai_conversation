"""Exact provider usage normalization, aggregation, persistence, and sensor tests."""

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from custom_components.extended_openai_conversation_responses.sensor import (
    LastResponseUsageSensor,
    UsageSensor,
    UsageTodaySensor,
)
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    UsageManager,
    extract_usage,
)


class FakeStorage:
    """Detached in-memory usage persistence boundary."""

    def __init__(self, data: Any = None) -> None:
        self.data = deepcopy(data)

    async def async_load(self) -> Any:
        return deepcopy(self.data)

    async def async_save(self, data: Any) -> None:
        self.data = deepcopy(data)


async def _manager(
    totals: FakeStorage | None = None,
    daily: FakeStorage | None = None,
    details: FakeStorage | None = None,
) -> UsageManager:
    manager = UsageManager(
        totals or FakeStorage(),
        daily,
        details,
        agent_subentry_id="agent-1",
    )
    await manager.async_initialize()
    return manager


def test_responses_usage_maps_exact_token_categories() -> None:
    """Responses usage preserves every supported token category exactly."""
    usage = extract_usage(
        {
            "input_tokens": 11,
            "output_tokens": 17,
            "input_tokens_details": {"cached_tokens": 3, "audio_tokens": 2},
            "output_tokens_details": {"reasoning_tokens": 5},
        }
    )

    assert usage == RequestUsage(
        input_tokens=11,
        output_tokens=17,
        total_tokens=28,
        cached_input_tokens=3,
        reasoning_tokens=5,
        details={
            "input_cached_tokens": 3,
            "input_audio_tokens": 2,
            "output_reasoning_tokens": 5,
        },
    )


def test_chat_completions_usage_normalizes_to_same_semantics() -> None:
    """Chat Completions fields normalize to the same accounting buckets."""
    usage = extract_usage(
        SimpleNamespace(
            prompt_tokens=11,
            completion_tokens=17,
            total_tokens=28,
            prompt_tokens_details=SimpleNamespace(cached_tokens=3),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=5),
        )
    )

    assert (
        usage.input_tokens,
        usage.output_tokens,
        usage.total_tokens,
        usage.cached_input_tokens,
        usage.reasoning_tokens,
    ) == (11, 17, 28, 3, 5)


def test_partial_usage_preserves_known_fields_and_derives_total() -> None:
    """Missing output metadata does not discard a known input count."""
    usage = extract_usage({"input_tokens": 11})

    assert usage.input_tokens == 11
    assert usage.output_tokens == 0
    assert usage.total_tokens == 11
    assert usage.cached_input_tokens == 0
    assert usage.reasoning_tokens == 0


async def test_malformed_persisted_counters_salvage_valid_values() -> None:
    """Startup neutralizes malformed counters without losing valid totals."""
    storage = FakeStorage(
        {
            "conversation_count": 2,
            "api_request_count": -4,
            "successful_request_count": True,
            "failed_request_count": 3,
            "input_tokens": 11,
            "output_tokens": "17",
            "total_tokens": 19,
            "cached_input_tokens": 5,
            "reasoning_tokens": 7,
            "details": {
                "input_cached_tokens": 5,
                "bad_negative": -1,
                "bad_bool": True,
            },
        }
    )

    manager = await _manager(storage)

    assert manager.as_dict() == {
        "conversation_count": 2,
        "api_request_count": 0,
        "successful_request_count": 0,
        "failed_request_count": 3,
        "input_tokens": 11,
        "output_tokens": 0,
        "total_tokens": 19,
        "cached_input_tokens": 5,
        "reasoning_tokens": 7,
        "details": {"input_cached_tokens": 5},
    }


async def test_multiple_requests_accumulate_exact_categories_and_reload() -> None:
    """Dedicated token buckets sum exactly and survive aggregate reload."""
    totals = FakeStorage()
    daily = FakeStorage()
    first = await _manager(totals, daily)

    await first.async_record_request(
        successful=True,
        usage=RequestUsage(
            input_tokens=11,
            output_tokens=17,
            total_tokens=28,
            cached_input_tokens=3,
            reasoning_tokens=5,
            details={"input_cached_tokens": 3, "output_reasoning_tokens": 5},
        ),
        provider="openai",
        model="gpt-alpha",
        api_mode="responses",
    )
    await first.async_record_request(
        successful=True,
        usage=RequestUsage(
            input_tokens=13,
            output_tokens=19,
            total_tokens=32,
            cached_input_tokens=7,
            reasoning_tokens=11,
            details={"input_cached_tokens": 7, "output_reasoning_tokens": 11},
        ),
        provider="openai",
        model="gpt-beta",
        api_mode="responses",
    )

    assert first.totals.input_tokens == 24
    assert first.totals.output_tokens == 36
    assert first.totals.total_tokens == 60
    assert first.totals.cached_input_tokens == 10
    assert first.totals.reasoning_tokens == 16
    assert first.totals.details == {
        "input_cached_tokens": 10,
        "output_reasoning_tokens": 16,
    }

    restarted = await _manager(totals, daily)
    assert restarted.as_dict() == first.as_dict()


async def test_model_change_uses_per_agent_totals_and_exact_breakdowns() -> None:
    """Model changes share agent totals while retaining per-model daily buckets."""
    manager = await _manager(FakeStorage(), FakeStorage())

    await manager.async_record_request(
        successful=True,
        usage=RequestUsage(input_tokens=11, output_tokens=17, total_tokens=28),
        provider="openai",
        model="gpt-alpha",
        api_mode="responses",
    )
    await manager.async_record_request(
        successful=True,
        usage=RequestUsage(input_tokens=13, output_tokens=19, total_tokens=32),
        provider="openai",
        model="gpt-beta",
        api_mode="chat_completions",
    )

    assert manager.totals.total_tokens == 60
    assert manager.breakdowns() == {
        "providers": {"openai": 60},
        "models": {"gpt-alpha": 28, "gpt-beta": 32},
        "api_modes": {"responses": 28, "chat_completions": 32},
    }


async def test_usage_sensor_projection_matches_manager_exactly() -> None:
    """Cumulative, period, and latest-response sensors project manager state exactly."""
    manager = await _manager(FakeStorage(), FakeStorage(), FakeStorage())
    async with manager.async_run(
        home_assistant_conversation_id="conversation-1",
        source_device_id="kitchen",
    ):
        await manager.async_record_request(
            successful=True,
            usage=RequestUsage(
                input_tokens=11,
                output_tokens=17,
                total_tokens=28,
                cached_input_tokens=3,
                reasoning_tokens=5,
            ),
            provider="openai",
            model="gpt-alpha",
            api_mode="responses",
            tool_calls_requested=2,
        )

    subentry = SimpleNamespace(
        subentry_id="agent-1",
        title="Assistant",
        data={},
    )
    cumulative = UsageSensor(subentry, manager)
    today = UsageTodaySensor(subentry, manager)
    latest = LastResponseUsageSensor(subentry, manager)

    assert cumulative.native_value == manager.totals.total_tokens == 28
    assert cumulative.extra_state_attributes == manager.as_dict()

    summary = manager.today_summary()
    assert today.native_value == summary["total_tokens"] == 28
    assert today.extra_state_attributes == {
        "input": 11,
        "output": 17,
        "cached_input": 3,
        "reasoning": 5,
        "runs": 1,
        "requests": 1,
        "failures": 0,
        "average_tokens_per_run": 28,
    }

    assert latest.native_value == manager.latest_run.total_tokens == 28
    assert latest.extra_state_attributes["input"] == 11
    assert latest.extra_state_attributes["output"] == 17
    assert latest.extra_state_attributes["cached_input"] == 3
    assert latest.extra_state_attributes["reasoning"] == 5
    assert latest.extra_state_attributes["api_request_count"] == 1
    assert latest.extra_state_attributes["tool_call_count"] == 2
    assert latest.extra_state_attributes["models"] == ["gpt-alpha"]
    assert latest.extra_state_attributes["providers"] == ["openai"]
    assert latest.extra_state_attributes["success"] is True
