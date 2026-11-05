"""Guest interval producer -> real panel normalizer -> server wall-time resolver."""

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

import pytest

from custom_components.extended_openai_conversation_responses.management_datetime import (
    resolve_management_dates,
)


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
