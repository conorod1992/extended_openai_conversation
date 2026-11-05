"""Actual primary/detail projections survive frontend replacement and cached revisits."""

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import management_loading_performance as overview


@pytest.mark.parametrize("state", ["active", "active_indefinitely", "scheduled", "inactive"])
@pytest.mark.parametrize("excluded", [False, True])
async def test_overview_primary_detail_cache_flag(hass, monkeypatch, state, excluded):
    status = {"state": state, "currently_active": state in {"active", "active_indefinitely"}}
    options = {"guest_excluded_entities": ["lock.front_door"] if excluded else [], "functions": []}
    entry = SimpleNamespace(entry_id="entry", title="Provider", data={})
    subentry = SimpleNamespace(subentry_id="agent", title="Assistant", data=options)
    manager = SimpleNamespace(status=lambda: dict(status))
    monkeypatch.setattr(overview, "async_get_guest_mode", AsyncMock(return_value=manager))
    primary = {"agent": overview._agent_snapshot(hass, entry, subentry, guest_status=status), "loading": {"guest_mode": True}, "usage": {}, "setup_health": {}}
    detail = await overview.async_overview_detail(hass, entry, subentry, is_admin=True, kind="guest_mode")
    result = subprocess.run(["node", str(Path(__file__).with_name("overview-guest-projection-wire.mjs"))], input=json.dumps({"primary": primary, "detail": detail}), capture_output=True, text=True, check=True)
    observed = json.loads(result.stdout)
    expected = state in {"active", "active_indefinitely"} and not excluded
    assert observed == {"primary_warning": expected, "detail_warning": expected, "cached_warning": expected, "flag": excluded, "revisit_requests": 0}
