from copy import deepcopy
import pytest
from custom_components.extended_openai_conversation_responses import usage
from tests.test_usage import FakeStorage, _coverage_request, _coverage_run
from homeassistant.util import dt as dt_util


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
