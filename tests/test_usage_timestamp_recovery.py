"""Regression tests for malformed persisted Usage timestamps."""

from copy import deepcopy
from dataclasses import asdict

import pytest
from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses.usage import (
    UsageManager,
    UsageRequest,
    UsageRun,
    _usage_request_from_backup,
    _usage_run_from_backup,
)


class FakeStorage:
    """In-memory persistence boundary for Usage initialization tests."""

    def __init__(self, data: dict | None = None) -> None:
        self.data = data

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


def _request(request_id: str, timestamp: object) -> dict:
    return asdict(
        UsageRequest(
            request_id=request_id,
            run_id="run-1",
            timestamp=timestamp,  # type: ignore[arg-type]
            agent_subentry_id="agent",
            provider="openai",
            model="gpt-test",
            api_mode="responses",
            successful=True,
            duration_ms=10,
        )
    )


def _run(run_id: str, started_at: object) -> dict:
    return asdict(
        UsageRun(
            run_id=run_id,
            started_at=started_at,  # type: ignore[arg-type]
            completed_at=None,
            duration_ms=10,
            agent_subentry_id="agent",
            home_assistant_conversation_id=None,
            source_device_id=None,
        )
    )


@pytest.mark.asyncio
async def test_initialize_drops_malformed_detail_timestamps_without_losing_valid_rows() -> None:
    """Corrupt request/run timestamps must not abort Usage initialization."""
    now = dt_util.utcnow().isoformat()
    details = FakeStorage(
        {
            "requests": [
                _request("valid-request", now),
                _request("bad-string-request", "not-a-timestamp"),
                _request("bad-type-request", None),
            ],
            "runs": [
                _run("valid-run", now),
                _run("bad-string-run", "not-a-timestamp"),
                _run("bad-type-run", None),
            ],
        }
    )
    manager = UsageManager(
        FakeStorage(),
        FakeStorage(),
        details,
        agent_subentry_id="agent",
    )

    await manager.async_initialize()

    assert [request.request_id for request in manager.requests] == ["valid-request"]
    assert [run.run_id for run in manager.runs] == ["valid-run"]


@pytest.mark.parametrize("invalid", ["not-a-timestamp", "2026-99-99T99:99:99"])
def test_backup_validation_still_rejects_malformed_timestamps(invalid: str) -> None:
    """Recovery semantics must not turn malformed backup timestamps into valid data."""
    with pytest.raises(ValueError, match="usage request metadata is invalid"):
        _usage_request_from_backup(_request("bad-request", invalid), "agent")

    with pytest.raises(ValueError, match="usage run metadata is invalid"):
        _usage_run_from_backup(_run("bad-run", invalid), "agent")
