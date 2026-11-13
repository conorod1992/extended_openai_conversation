"""Only one audited fixed expression may avoid regex-worker startup."""

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    regex_execution as regex,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    async_validate_function_arguments,
)
from custom_components.extended_openai_conversation_responses.memory import memory_tools
from homeassistant.exceptions import HomeAssistantError


class ExecutorHass:
    async def async_add_executor_job(self, target, *args):
        return await asyncio.to_thread(target, *args)


@pytest.mark.parametrize(
    "value",
    [
        "a" * 64,
        "0" * 64,
        "A" * 64,
        "a" * 63,
        "a" * 65,
        "a" * 64 + "\n",
        "\n" + "a" * 64,
        "a" * 64 + "\n\n",
        "z" * 1_000_000,
    ],
)
async def test_fixed_revision_matches_exact_stdlib_semantics_without_worker(
    value, monkeypatch
):
    worker = AsyncMock(
        side_effect=AssertionError("fixed pattern must not start a worker")
    )
    hass = SimpleNamespace(async_add_executor_job=worker)
    expected = re.search(r"^[0-9a-f]{64}$", value) is not None
    assert await regex.async_search_configured_patterns(
        hass, [(r"^[0-9a-f]{64}$", value, 0)]
    ) == [expected]
    worker.assert_not_awaited()


async def test_real_memory_revision_validation_survives_worker_startup_failure(
    monkeypatch,
):
    def unavailable(_payload):
        raise HomeAssistantError("startup unavailable")

    monkeypatch.setattr(regex, "_run_regex_worker", unavailable)
    spec = next(
        tool["spec"]
        for tool in memory_tools()
        if tool["spec"]["name"] == "memory_update"
    )
    arguments = {"memory_id": "memory-id", "expected_revision": "a" * 64}
    assert (
        await async_validate_function_arguments(ExecutorHass(), spec, arguments)
        == arguments
    )
    with pytest.raises(HomeAssistantError, match="required pattern"):
        await async_validate_function_arguments(
            ExecutorHass(), spec, {**arguments, "expected_revision": "invalid"}
        )


async def test_mixed_results_keep_order_and_isolate_every_other_pattern(monkeypatch):
    seen = []
    original = regex._run_regex_worker

    def observed(payload):
        seen.append(payload)
        return original(payload)

    monkeypatch.setattr(regex, "_run_regex_worker", observed)
    checks = [
        (r"^[0-9a-f]{64}$", "a" * 64, 0),
        ("^ok$", "no", 0),
        (r"^[0-9a-f]{64}$", "invalid", 0),
        (r"^[0-9a-f]{64}$", "A" * 64, re.IGNORECASE),
        (r"(?:^[0-9a-f]{64}$)", "a" * 64, 0),
    ]
    assert await regex.async_search_configured_patterns(ExecutorHass(), checks) == [
        True,
        False,
        False,
        True,
        True,
    ]
    assert [item["pattern"] for item in seen[0]["items"]] == [
        "^ok$",
        r"^[0-9a-f]{64}$",
        r"(?:^[0-9a-f]{64}$)",
    ]


@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_startup_delay_is_distinct_from_regex_execution(monkeypatch, mode):
    monkeypatch.setattr(regex, "_CONFIGURED_REGEX_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(
        regex,
        "_REGEX_WORKER",
        "import time; time.sleep(0.2)\nraise SystemExit('regex not reached')\n"
        + regex._REGEX_WORKER,
    )
    payload = {
        "op": "search_many",
        "items": [{"pattern": "^ok$", "text": "ok", "flags": 0}],
    }
    with pytest.raises(HomeAssistantError, match="execution limit"):
        if mode == "sync":
            await asyncio.to_thread(regex._run_regex_worker, payload)
        else:
            await regex._async_run_regex_worker(payload)
