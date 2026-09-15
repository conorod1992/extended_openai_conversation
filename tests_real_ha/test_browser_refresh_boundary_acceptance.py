"""Real-HA browser acceptance for hard refresh during an in-flight mutation."""

from __future__ import annotations

import os
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

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_HA_BROWSER") != "1",
    reason="enabled only by the browser-to-genuine-HA acceptance job",
)


@pytest.mark.asyncio
async def test_hard_refresh_after_committed_mutation_does_not_replay(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """A page reload must not duplicate an already committed management mutation."""
    entry = _entry("Browser Refresh Boundary Acceptance")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    runner, backend_url = await _start_ws_bridge(client)

    repo_root = Path(__file__).resolve().parent.parent
    try:
        await _run_playwright(
            repo_root=repo_root,
            spec="tests_browser/real-ha-refresh-boundary.spec.mjs",
            config="playwright.config.mjs",
            env={"REAL_HA_BACKEND_URL": backend_url},
            failure_label="Playwright hard-refresh mutation boundary acceptance failed",
        )
    finally:
        await runner.cleanup()
