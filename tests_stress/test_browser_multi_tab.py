"""Run two Chromium tabs against the same authenticated genuine HA backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from homeassistant.core import HomeAssistant
from tests_real_ha.test_browser_backend_acceptance import (
    _run_playwright,
    _start_ws_bridge,
)
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _entry,
    _setup_entry,
)
from tests_stress.conftest import record


@pytest.mark.asyncio
async def test_two_tabs_reject_a_stale_rule_writer(
    hass: HomeAssistant,
    hass_ws_client: Any,
    stress_trace: list[dict],
) -> None:
    entry = _entry("Enhanced two-tab conflict")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    runner, backend_url = await _start_ws_bridge(client)
    record(stress_trace, "open_shared_backend", tabs=2)
    try:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parent.parent,
            spec="tests_browser/real-ha-multi-tab.stress.mjs",
            config="playwright.stress.config.mjs",
            env={"REAL_HA_BACKEND_URL": backend_url},
            failure_label="Enhanced real-HA two-tab conflict failed",
        )
    finally:
        await runner.cleanup()
    record(
        stress_trace,
        "summary",
        layer="browser + real-ha",
        tabs=2,
        multi_tab_conflicts=6,
        conflict_surfaces=[
            "Request Rules",
            "Assistant backup restore",
            "Function Tool",
            "Function Group",
            "Knowledge",
            "Guest Mode",
        ],
    )
