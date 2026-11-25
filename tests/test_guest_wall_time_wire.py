"""Guest interval producer -> real panel normalizer -> server wall-time resolver."""

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

import pytest

from custom_components.extended_openai_conversation_responses.management_datetime import (
    project_management_dates,
    resolve_management_dates,
)
from tests.test_guest_mode import _manager


@pytest.mark.parametrize("browser_zone", ["UTC", "Pacific/Auckland"])
@pytest.mark.parametrize("ha_zone", ["UTC", "America/New_York"])
@pytest.mark.parametrize("implementation", ["source", "bundle"])
def test_guest_wall_times_survive_panel_wire_normalization(
    hass, browser_zone, ha_zone, implementation
):
    output = subprocess.check_output(
        [
            "node",
            str(Path(__file__).with_name("guest-wall-time-wire.mjs")),
            ha_zone,
            implementation,
        ],
        env={**os.environ, "TZ": browser_zone},
        text=True,
    )
    rows = json.loads(output)
    assert len(rows) == 8
    hass.config.time_zone = ha_zone
    by_case = {row["scenario"]: row["message"] for row in rows}
    resolved_cases = {}
    for row in rows:
        msg = row["message"]
        for key, value in msg.get("local_times", {}).items():
            assert msg[key] == value
        resolved = resolve_management_dates(hass, msg)
        resolved_cases[row["scenario"]] = resolved
        if row["scenario"] == "schedule":
            assert resolved["active_from"] == (
                "2027-06-04T12:00:00+00:00"
                if ha_zone == "UTC"
                else "2027-06-04T16:00:00+00:00"
            )
    assert by_case["now-finite"]["local_times"] == {"active_until": "2027-06-04T13:00"}
    assert by_case["unchanged"]["local_times"] == {}
    assert by_case["now-indefinite"]["local_times"] == {}
    for scenario in ("now-finite", "now-indefinite"):
        assert "active_from" not in by_case[scenario]
        assert "active_from" not in by_case[scenario]["local_times"]
    assert by_case["legacy"]["active_from"] == "2027-06-04T12:00:00+02:00"
    assert resolved_cases["edit-start"]["active_from"] == "2027-06-04T12:15:00+00:00"
    assert resolved_cases["edit-start"]["active_until"] == "2027-06-04T13:00:00+00:00"
    assert resolved_cases["edit-end"]["active_from"] == "2027-06-04T12:00:00+00:00"
    assert resolved_cases["edit-end"]["active_until"] == "2027-06-04T13:15:00+00:00"
    assert resolved_cases["unchanged"]["active_from"] == "2027-06-04T12:00:00+00:00"
    expected_legacy = datetime(
        2027, 6, 4, 12, tzinfo=ZoneInfo(browser_zone)
    ).astimezone(UTC)
    assert by_case["legacy-naive"]["active_from"] == expected_legacy.isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


@pytest.mark.parametrize("implementation", ["source", "bundle"])
async def test_restored_offset_expiry_uses_utc_for_display_and_edits(
    hass, implementation
):
    hass.config.time_zone = "Asia/Tokyo"
    manager = _manager(hass)
    original = "9999-12-31T23:59:59.123456+01:00"
    schedule = manager.validate_backup_data(
        {
            "schedule": {
                "active_from": "2027-06-04T12:00:00+00:00",
                "active_until": original,
                "source": "home_assistant",
                "updated_at": None,
            }
        }
    )
    await manager.async_replace_backup(schedule)
    status = project_management_dates(hass, "guest_mode", {"status": manager.status()})[
        "status"
    ]
    assert status["active_until"] == original
    assert status["active_until_local"] is None
    assert status["active_until_utc"] == "9999-12-31T22:59:59"
    result = subprocess.check_output(
        [
            "node",
            str(Path(__file__).with_name("guest-wall-time-wire.mjs")),
            hass.config.time_zone,
            implementation,
            "2027-06-01T12:00:00Z",
            json.dumps(status),
        ],
        env={**os.environ, "TZ": "Pacific/Auckland"},
        text=True,
    )
    unchanged, edited = json.loads(result)
    for case in (unchanged, edited):
        assert case["displayed"]["end"] == "9999-12-31T22:59"
        assert "Ends 9999-12-31 22:59:59 UTC" in case["displayed"]["summary"]
        assert case["message"]["local_times"] == {}
    assert unchanged["message"]["active_until"] == original
    edited_message = resolve_management_dates(hass, edited["message"])
    assert edited_message["active_until"] == "9999-12-31T22:58Z"
    saved = await manager.async_update_trusted(
        active_from=edited_message["active_from"],
        active_until=edited_message["active_until"],
        indefinite=False,
        now=datetime(2027, 6, 1, tzinfo=UTC),
    )
    assert datetime.fromisoformat(saved["active_until"]) < datetime.fromisoformat(
        original
    )


@pytest.mark.parametrize("implementation", ["source", "bundle"])
@pytest.mark.parametrize(
    "browser_clock", ["2027-06-01T12:00:00Z", "2027-06-07T12:00:00Z"]
)
async def test_activation_uses_server_clock_and_preserves_rejected_schedule(
    hass, implementation, browser_clock
):
    output = subprocess.check_output(
        [
            "node",
            str(Path(__file__).with_name("guest-wall-time-wire.mjs")),
            "UTC",
            implementation,
            browser_clock,
        ],
        text=True,
    )
    cases = {
        row["scenario"]: resolve_management_dates(hass, row["message"])
        for row in json.loads(output)
    }
    manager = _manager(hass)
    server_time = datetime(2027, 6, 4, 12, tzinfo=UTC)
    for scenario in ("now-finite", "now-indefinite"):
        msg = cases[scenario]
        assert "active_from" not in msg
        status = await manager.async_update_trusted(
            active_until=msg.get("active_until"),
            indefinite=msg["indefinite"],
            now=server_time,
        )
        assert status["active_from"] == server_time.isoformat()
        assert status["currently_active"]
    await manager.async_update_trusted(
        active_from="2027-06-05T12:00:00Z", indefinite=True, now=server_time
    )
    saved = await manager.async_backup_data()
    writes = manager._store.async_save.await_count
    with pytest.raises(ValueError, match="active_until must be later"):
        await manager.async_update_trusted(
            active_until=cases["now-finite"]["active_until"],
            now=datetime(2027, 6, 4, 14, tzinfo=UTC),
        )
    assert await manager.async_backup_data() == saved
    assert manager._store.async_save.await_count == writes
