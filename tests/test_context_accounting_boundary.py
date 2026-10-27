"""Context-budget estimates never enter provider-only accounting snapshots."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.context_usage_hardening import (
    RequestTokenMeasurements,
    _capture_provider_usage,
)
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    UsageManager,
)
from tests.test_usage_accounting_recovery import FakeStorage


@pytest.mark.parametrize("successful", [False, True])
@pytest.mark.parametrize(
    ("reports", "expected", "context_tokens"),
    [
        ([None], RequestUsage(), 8000),
        ([{"input_tokens": 0, "output_tokens": 0}], RequestUsage(), 8000),
        (
            [{"input_tokens": 6500}],
            RequestUsage(input_tokens=6500, total_tokens=6500),
            6500,
        ),
        (
            [{"output_tokens": 400}],
            RequestUsage(output_tokens=400, total_tokens=400),
            8000,
        ),
        (
            [{"input_tokens": 6500}, {"output_tokens": 400}, {"output_tokens": 400}],
            RequestUsage(input_tokens=6500, output_tokens=400, total_tokens=6900),
            6500,
        ),
        (
            [{"input_tokens": 6500}, {"input_tokens": 0, "output_tokens": 400}],
            RequestUsage(output_tokens=400, total_tokens=400),
            400,
        ),
    ],
)
async def test_context_estimate_is_independent_of_persisted_provider_counts(
    successful, reports, expected, context_tokens
):
    measurements = RequestTokenMeasurements(context_estimate=8000)
    for report in reports:
        _capture_provider_usage(measurements, deepcopy(report))
    assert measurements.context_tokens == context_tokens
    assert measurements.reported == expected
    assert not hasattr(measurements, "input_tokens")
    assert not hasattr(measurements, "total_tokens")

    daily, details = FakeStorage(), FakeStorage()
    manager = UsageManager(FakeStorage(), daily, details)
    await manager.async_initialize()
    async with manager.async_run() as run:
        await manager.async_record_request(
            successful=successful, usage=measurements.reported
        )
    await manager.async_shutdown()
    assert manager.totals.api_request_count == run.request_count == 1
    assert run.successful is successful
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        count = getattr(expected, field)
        assert getattr(manager.totals, field) == getattr(run, field) == count
        assert daily.data["totals"][field] == count
        assert details.data["requests"][0][field] == count
        assert details.data["runs"][0][field] == count
    assert measurements.context_tokens == context_tokens
    assert manager.totals.details == {}
