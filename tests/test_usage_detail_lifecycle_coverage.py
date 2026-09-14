"""Focused coverage for Usage detail retention and destructive lifecycle paths."""

from copy import deepcopy
from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses.durable_state_hardening import (
    _install_usage_transactions,
)
from custom_components.extended_openai_conversation_responses.usage import (
    UsageManager,
    UsageRequest,
    UsageRun,
)


# Exercise the effective Usage lifecycle contract installed at integration startup.
_install_usage_transactions()


class DetailStorage:
    """Small detail-store stand-in with observable writes and optional failure."""

    def __init__(self) -> None:
        self.data = None
        self.saves: list[dict] = []
        self.fail_save = False

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data) -> None:
        if self.fail_save:
            raise OSError("detail store unavailable")
        self.data = deepcopy(data)
        self.saves.append(deepcopy(data))


class TotalsStorage:
    async def async_load(self):
        return None

    async def async_save(self, _data) -> None:
        return None


def _request(request_id: str, timestamp: str) -> UsageRequest:
    return UsageRequest(
        request_id=request_id,
        run_id=f"run-{request_id}",
        timestamp=timestamp,
        agent_subentry_id="agent",
        provider="openai",
        model="gpt-test",
        api_mode="responses",
        successful=True,
        duration_ms=1,
    )


def _run(run_id: str, started_at: str) -> UsageRun:
    return UsageRun(
        run_id=run_id,
        started_at=started_at,
        completed_at=started_at,
        duration_ms=1,
        agent_subentry_id="agent",
        home_assistant_conversation_id=None,
        source_device_id=None,
    )


def _manager(
    detail_storage: DetailStorage | None,
    *,
    request_retention_days: int = 30,
    run_retention_days: int = 30,
) -> UsageManager:
    return UsageManager(
        TotalsStorage(),
        detail_storage=detail_storage,
        agent_subentry_id="agent",
        request_retention_days=request_retention_days,
        run_retention_days=run_retention_days,
    )


async def test_prune_applies_independent_retention_windows_and_persists_survivors() -> None:
    """Request and run detail retention are evaluated independently."""
    details = DetailStorage()
    manager = _manager(
        details,
        request_retention_days=10,
        run_retention_days=30,
    )
    now = dt_util.utcnow()
    manager.requests = [
        _request("recent", (now - timedelta(days=2)).isoformat()),
        _request("expired", (now - timedelta(days=20)).isoformat()),
    ]
    manager.runs = [
        _run("recent-run", (now - timedelta(days=20)).isoformat()),
        _run("expired-run", (now - timedelta(days=40)).isoformat()),
    ]

    result = await manager.async_prune_details()

    assert result == {"deleted_requests": 1, "deleted_runs": 1}
    assert [request.request_id for request in manager.requests] == ["recent"]
    assert [run.run_id for run in manager.runs] == ["recent-run"]
    assert details.data is not None
    assert [item["request_id"] for item in details.data["requests"]] == ["recent"]
    assert [item["run_id"] for item in details.data["runs"]] == ["recent-run"]


async def test_zero_retention_prunes_every_detail_without_saving_when_disabled() -> None:
    """Zero retention means keep no details, while save=False remains purely in-memory."""
    details = DetailStorage()
    manager = _manager(details, request_retention_days=0, run_retention_days=0)
    now = dt_util.utcnow().isoformat()
    manager.requests = [_request("request", now)]
    manager.runs = [_run("run", now)]

    result = await manager.async_prune_details(save=False)

    assert result == {"deleted_requests": 1, "deleted_runs": 1}
    assert manager.requests == []
    assert manager.runs == []
    assert details.saves == []
    assert details.data is None


async def test_clear_without_confirmation_is_non_destructive() -> None:
    """The destructive detail clear cannot proceed without explicit confirmation."""
    details = DetailStorage()
    manager = _manager(details)
    now = dt_util.utcnow().isoformat()
    request = _request("request", now)
    run = _run("run", now)
    manager.requests = [request]
    manager.runs = [run]

    with pytest.raises(ValueError, match="Explicit confirmation is required"):
        await manager.async_clear_details(confirm=False)

    assert manager.requests == [request]
    assert manager.runs == [run]
    assert details.saves == []


async def test_clear_without_detail_store_still_reports_and_clears_live_state() -> None:
    """Managers without detail persistence still support an explicit live clear."""
    manager = _manager(None)
    now = dt_util.utcnow().isoformat()
    manager.requests = [_request("first", now), _request("second", now)]
    manager.runs = [_run("run", now)]

    result = await manager.async_clear_details(confirm=True)

    assert result == {"deleted_requests": 2, "deleted_runs": 1}
    assert manager.requests == []
    assert manager.runs == []


async def test_clear_save_failure_preserves_live_state_and_can_converge() -> None:
    """A failed durable clear stays unpublished, and a later retry can converge."""
    details = DetailStorage()
    manager = _manager(details)
    now = dt_util.utcnow().isoformat()
    request = _request("request", now)
    run = _run("run", now)
    manager.requests = [request]
    manager.runs = [run]
    details.fail_save = True

    with pytest.raises(OSError, match="detail store unavailable"):
        await manager.async_clear_details(confirm=True)

    # Durable-state hardening persists the candidate clear before publishing it.
    assert manager.requests == [request]
    assert manager.runs == [run]
    assert details.data is None

    details.fail_save = False
    result = await manager.async_clear_details(confirm=True)

    assert result == {"deleted_requests": 1, "deleted_runs": 1}
    assert manager.requests == []
    assert manager.runs == []
    assert details.data == {"requests": [], "runs": []}
