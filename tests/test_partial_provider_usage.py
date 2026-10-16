"""Successive cumulative provider snapshots retain absent accounting fields."""

from types import SimpleNamespace
from typing import Any

from pydantic import create_model
import pytest

from custom_components.extended_openai_conversation_responses import (
    context_usage_hardening as context_usage,
)
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    UsageManager,
)
from tests.test_usage_accounting_recovery import FakeStorage

COMPLETE = {
    "input_tokens": 120,
    "output_tokens": 30,
    "total_tokens": 150,
    "input_tokens_details": {"cached_tokens": 20},
}
LATER_COMPLETE = {
    "input_tokens": 200,
    "output_tokens": 50,
    "total_tokens": 250,
    "input_tokens_details": {"cached_tokens": 25},
    "output_tokens_details": {"reasoning_tokens": 3},
}

SparseDetails = create_model(
    "SparseDetails", cached_tokens=(int, 0), reasoning_tokens=(int, 0)
)
SparseUsage = create_model(
    "SparseUsage",
    **{
        name: (Any, 0)
        for name in (
            "input_tokens",
            "output_tokens",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        )
    },
    **{
        name: (SparseDetails | None, None)
        for name in (
            "input_tokens_details",
            "output_tokens_details",
            "prompt_tokens_details",
            "completion_tokens_details",
        )
    },
)


def wire_usage(report, api, objects):
    aliases = (
        {
            "input_tokens": "prompt_tokens",
            "output_tokens": "completion_tokens",
            "input_tokens_details": "prompt_tokens_details",
            "output_tokens_details": "completion_tokens_details",
        }
        if api == "chat"
        else {}
    )
    raw = {aliases.get(key, key): value for key, value in report.items()}
    if objects == "model-defaults":
        return SparseUsage(**raw)
    if objects == "attributes":
        # SDK objects expose absent optional fields as None rather than zero.
        keys = {aliases.get(key, key): None for key in COMPLETE}
        keys[aliases.get("output_tokens_details", "output_tokens_details")] = None
        raw = SimpleNamespace(
            **{
                key: SimpleNamespace(**value) if isinstance(value, dict) else value
                for key, value in {**keys, **raw}.items()
            }
        )
    return raw


@pytest.mark.parametrize("api", ["chat", "responses"])
@pytest.mark.parametrize("objects", ["mapping", "attributes", "model-defaults"])
@pytest.mark.parametrize(
    ("reports", "expected"),
    [
        ([COMPLETE, {"output_tokens": 40, "total_tokens": 160}], (120, 40, 160, 20, 0)),
        (
            [COMPLETE, {"output_tokens_details": {"reasoning_tokens": 9}}],
            (120, 30, 150, 20, 9),
        ),
        ([{"output_tokens": 40}, COMPLETE], (120, 30, 150, 20, 0)),
        ([COMPLETE, LATER_COMPLETE, LATER_COMPLETE], (200, 50, 250, 25, 3)),
        (
            [COMPLETE, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}],
            (120, 30, 150, 20, 0),
        ),
        ([COMPLETE, {"output_tokens": 40}], (120, 40, 160, 20, 0)),
        ([COMPLETE, {"input_tokens": 130}], (130, 30, 160, 20, 0)),
        (
            [
                LATER_COMPLETE,
                {
                    "output_tokens": 40,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            ],
            (200, 40, 240, 0, 0),
        ),
        ([COMPLETE, {"input_tokens": 0, "output_tokens": 40}], (0, 40, 40, 20, 0)),
        (
            [{"output_tokens": 30}, {"output_tokens_details": {"reasoning_tokens": 9}}],
            (0, 30, 30, 0, 9),
        ),
        ([{"total_tokens": 150}, {"output_tokens": 40}], (0, 40, 150, 0, 0)),
        ([{"total_tokens": 150}, {"input_tokens": 120}], (120, 0, 150, 0, 0)),
        (
            [{"total_tokens": 150}, {"input_tokens": 120}, {"output_tokens": 40}],
            (120, 40, 160, 0, 0),
        ),
    ],
    ids=[
        "output-only",
        "reasoning-only",
        "partial-to-complete",
        "repeated-complete",
        "zero-report",
        "derive-output-total",
        "derive-input-total",
        "explicit-zero-details",
        "explicit-zero-input",
        "estimate-survives-partial-reports",
        "total-before-output",
        "total-before-input",
        "total-before-both-components",
    ],
)
async def test_partial_stream_usage_reaches_persisted_accounting(
    api, objects, reports, expected
):
    request_usage = RequestUsage(
        input_tokens=900,
        total_tokens=900,
        details={context_usage._LOCAL_ESTIMATE_DETAIL: 900},
    )
    chat_log = SimpleNamespace(async_trace=lambda _payload: None)

    async def stream():
        for report in reports:
            raw = wire_usage(report, api, objects)
            yield (
                SimpleNamespace(usage=raw, choices=[])
                if api == "chat"
                else SimpleNamespace(
                    type="response.completed", response=SimpleNamespace(usage=raw)
                )
            )

    normalize = (
        context_usage.normalized_chat_stream
        if api == "chat"
        else context_usage.normalized_responses_stream
    )
    assert len(
        [item async for item in normalize(chat_log, stream(), request_usage)]
    ) == len(reports)
    accounted = context_usage.usage_for_accounting(request_usage)
    fields = (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
    )
    assert tuple(getattr(accounted, field) for field in fields) == expected
    assert context_usage._LOCAL_ESTIMATE_DETAIL not in accounted.details
    assert accounted.details.get("input_cached_tokens", 0) == expected[3]
    assert accounted.details.get("output_reasoning_tokens", 0) == expected[4]

    details = FakeStorage()
    manager = UsageManager(FakeStorage(), FakeStorage(), details)
    await manager.async_initialize()
    async with manager.async_run() as run:
        await manager.async_record_request(
            successful=True, usage=request_usage, api_mode=api
        )
    await manager.async_prepare_for_deletion()
    assert tuple(getattr(manager.totals, field) for field in fields) == expected
    assert tuple(getattr(run, field) for field in fields) == expected
    assert tuple(details.data["requests"][0][field] for field in fields) == expected
    assert tuple(details.data["runs"][0][field] for field in fields) == expected
