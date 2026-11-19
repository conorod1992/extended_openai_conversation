from copy import deepcopy
from datetime import timedelta

import pytest

from custom_components.extended_openai_conversation_responses import usage
from custom_components.extended_openai_conversation_responses.management_history_queries import (
    usage_requests_page,
)
from homeassistant.util import dt as dt_util
from tests.test_usage import FakeStorage, _coverage_request, _coverage_run


@pytest.mark.parametrize(
    "field,value",
    [
        ("duration_ms", "unexpected text"),
        ("duration_ms", -1),
        ("timestamp", "not a date"),
    ],
)
async def test_damaged_usage_stays_readable_but_export_requires_repaired_storage(field, value):
    totals, daily, details = FakeStorage(), FakeStorage(), FakeStorage()
    good = _coverage_request()
    good["request_id"] = "good"
    bad = {**good, "request_id": "bad", field: value}
    run = _coverage_run()
    broken = {**run, "run_id": "broken", "duration_ms": "bad"}
    day = dt_util.utcnow().date().isoformat()
    details.data = {
        "requests": [good, bad, {**good, "duration_ms": 999}],
        "runs": [run, broken],
    }
    daily.data = {
        "days": {
            day: {"date": day, "api_request_count": 1},
            "2026-01-01": {"date": "2026-01-01", "run_count": "bad"},
        }
    }
    original = deepcopy(details.data)
    manager = usage.UsageManager(
        totals, daily, details, request_retention_days=10000, run_retention_days=10000
    )
    await manager.async_initialize()
    with pytest.raises(ValueError, match="needs repair"):
        await manager.async_backup_data()
    assert [row.request_id for row in manager.requests] == ["good"]
    assert [row.run_id for row in manager.runs] == [run["run_id"]]
    assert details.data == original
    # Explicit offline repair followed by a fresh manager restores valid exports.
    details.data = {"requests": [good], "runs": [run]}
    daily.data = {"days": {day: {"date": day, "api_request_count": 1}}}
    repaired = deepcopy(details.data)
    manager = usage.UsageManager(totals, daily, details, request_retention_days=10000, run_retention_days=10000)
    await manager.async_initialize()
    exported = await manager.async_backup_data()
    assert [row["request_id"] for row in exported["requests"]] == ["good"]
    assert [row["run_id"] for row in exported["runs"]] == [run["run_id"]]
    assert set(exported["daily"]) == {day}
    assert details.data == repaired, "Loading must not start a cleanup write"
    restored = usage.UsageManager(
        FakeStorage(),
        FakeStorage(),
        FakeStorage(),
        request_retention_days=10000,
        run_retention_days=10000,
    )
    await restored.async_initialize()
    replacement = usage.UsageManager.validate_backup_data(exported, "imported-agent")
    await restored.async_replace_backup(*replacement)
    fresh = usage.UsageManager(
        restored._storage,
        restored._daily_storage,
        restored._detail_storage,
        request_retention_days=10000,
        run_retention_days=10000,
    )
    await fresh.async_initialize()
    assert fresh.requests[0].agent_subentry_id == "imported-agent"
    assert fresh.requests[0].duration_ms == good["duration_ms"]
    assert fresh.runs[0].duration_ms == run["duration_ms"]
    usage.UsageManager.validate_backup_data(
        await fresh.async_backup_data(), "next-agent"
    )


@pytest.mark.parametrize("retention", [0, 30])
async def test_detached_provider_requests_follow_retention_across_restart_and_backup(retention):
    stores = (FakeStorage(), FakeStorage(), FakeStorage())
    manager = usage.UsageManager(*stores, request_retention_days=retention)
    await manager.async_initialize()
    async with manager.async_run() as run:
        await manager.async_record_request(successful=True, request_stage="initial")
    await manager.async_record_request(
        successful=False,
        request_stage="context_summary",
        error_type="TimeoutError",
        usage=usage.RequestUsage(input_tokens=10, total_tokens=10),
    )
    fresh = usage.UsageManager(*stores, request_retention_days=retention)
    await fresh.async_initialize()
    assert fresh.totals.api_request_count == 2
    assert fresh.totals.conversation_count == 1
    assert fresh.totals.total_tokens == 10
    requests = usage_requests_page(fresh, None)["requests"]
    if retention:
        detached, attached = requests
        assert detached["run_id"] is None
        assert detached["request_stage"] == "context_summary"
        assert detached["error_type"] == "TimeoutError"
        assert detached["total_tokens"] == 10
        assert attached["run_id"] == run.run_id
        assert usage_requests_page(fresh, run.run_id)["requests"] == [attached]
        snapshot = await fresh.async_backup_data()
        _, _, restored, runs = usage.UsageManager.validate_backup_data(snapshot, "restored")
        assert [row.run_id for row in restored] == [run.run_id, None]
        assert [row.agent_subentry_id for row in restored] == ["restored", "restored"]
        assert [row.run_id for row in runs] == [run.run_id]
    else:
        assert requests == []


@pytest.mark.parametrize("include_expired", [False, True])
async def test_inline_run_dates_normalize_on_prune_and_survive_backup_restore(
    include_expired,
):
    completed = dt_util.utcnow() - timedelta(days=1)
    day = (completed - timedelta(days=1)).date().isoformat()
    current = _coverage_run(
        run_id="dated",
        started_at=completed.isoformat(),
        completed_at=completed.isoformat(),
    )
    undated = {**current, "run_id": "undated"}
    original = {
        "requests": [],
        "runs": [
            {**current, "accounting_date": day},
            {**undated, "accounting_date": None},
        ],
    }
    if include_expired:
        expired_at = completed - timedelta(days=366)
        original["runs"].append(
            {
                **current,
                "run_id": "expired",
                "started_at": expired_at.isoformat(),
                "completed_at": expired_at.isoformat(),
                "accounting_date": expired_at.date().isoformat(),
            }
        )
    stores = (FakeStorage(), FakeStorage(), FakeStorage())
    stores[2].data = deepcopy(original)
    source = usage.UsageManager(*stores)
    await source.async_initialize()
    assert await stores[2].async_load() == original
    assert [run.accounting_date for run in source.runs] == [day, None]

    await source.async_prune_details()
    canonical = {
        "requests": [],
        "runs": [current, undated],
        "run_accounting_dates": {"dated": day},
    }
    assert await stores[2].async_load() == canonical
    reloaded = usage.UsageManager(*stores)
    await reloaded.async_initialize()
    backup = await reloaded.async_backup_data()
    assert [run["accounting_date"] for run in backup["runs"]] == [day, None]

    destination_details = FakeStorage()
    destination = usage.UsageManager(FakeStorage(), detail_storage=destination_details)
    await destination.async_initialize()
    await destination.async_replace_backup(
        *usage.UsageManager.validate_backup_data(backup, current["agent_subentry_id"])
    )
    assert await destination_details.async_load() == canonical
    await destination.async_clear_details(confirm=True)
    assert await destination_details.async_load() == {"requests": [], "runs": []}


@pytest.mark.parametrize("metadata", [[], {"dated": None}, {"missing": "2026-09-13"}])
async def test_bad_stored_run_date_metadata_preserves_readable_run(metadata):
    completed = dt_util.utcnow().isoformat()
    current = _coverage_run(
        run_id="dated", started_at=completed, completed_at=completed
    )
    payload = {"requests": [], "runs": [current], "run_accounting_dates": metadata}
    details = FakeStorage()
    details.data = deepcopy(payload)
    manager = usage.UsageManager(FakeStorage(), detail_storage=details)
    await manager.async_initialize()
    assert [run.run_id for run in manager.runs] == ["dated"]
    assert manager.runs[0].accounting_date is None
    with pytest.raises(ValueError, match="needs repair"):
        await manager.async_backup_data()
    assert await details.async_load() == payload
