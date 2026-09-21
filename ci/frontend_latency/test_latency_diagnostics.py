"""Manual genuine-HA frontend/backend latency diagnostics."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from statistics import median
import time
from typing import Any

import pytest
from homeassistant.components import onboarding
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import CLIENT_ID, MockUser

from tests_real_ha.test_browser_backend_acceptance import _run_playwright
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _entry,
    _management_response,
    _setup_entry,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_EOAI_LATENCY_DIAGNOSTICS") != "1",
    reason="manual latency diagnostics only",
)


async def _timed_management_call(
    client: Any,
    *,
    entry: Any,
    section: str,
    action: str,
    **payload: Any,
) -> tuple[float, dict[str, Any]]:
    started = time.perf_counter()
    response = await _management_response(
        client,
        entry=entry,
        section=section,
        action=action,
        **payload,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert response["success"], response
    return elapsed_ms, response["result"]


@pytest.mark.asyncio
async def test_manual_frontend_latency_diagnostics(
    hass: HomeAssistant,
    hass_ws_client: Any,
    aiohttp_client: Any,
    hass_storage: dict[str, Any],
    socket_enabled: Any,
) -> None:
    """Collect backend WS and real-HA browser timings into one JSON artifact."""
    runs = max(1, int(os.environ.get("EOAI_LATENCY_RUNS", "3")))
    label = os.environ.get("EOAI_LATENCY_LABEL", "candidate")
    output = Path(
        os.environ.get(
            "EOAI_LATENCY_OUTPUT",
            f"latency-results/{label}.json",
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    hass_storage[onboarding.STORAGE_KEY] = {
        "version": onboarding.STORAGE_VERSION,
        "data": {"done": list(onboarding.STEPS)},
    }
    assert await async_setup_component(hass, "websocket_api", {})
    assert await async_setup_component(hass, "frontend", {})

    entry = _entry(f"Latency Diagnostics {label}")
    await _setup_entry(hass, entry)
    client = await _admin_client(
        hass,
        hass_ws_client,
        user_id=f"latency-diagnostics-{label}",
        name=f"Latency Diagnostics {label}",
    )

    backend: dict[str, dict[str, Any]] = {}

    async def collect(name: str, section: str, action: str, **payload: Any) -> dict[str, Any]:
        samples: list[float] = []
        last_result: dict[str, Any] = {}
        for _ in range(runs):
            elapsed_ms, last_result = await _timed_management_call(
                client,
                entry=entry,
                section=section,
                action=action,
                **payload,
            )
            samples.append(round(elapsed_ms, 3))
        backend[name] = {
            "samples_ms": samples,
            "first_ms": samples[0],
            "median_ms": round(median(samples), 3),
        }
        return last_result

    await collect("agents", "overview", "agents")
    await collect("overview_summary", "overview", "summary")
    await collect("configuration_get", "configuration", "get")
    await collect(
        "configuration_live_local_handling",
        "configuration",
        "live_metadata",
        metadata_keys=["local_handling"],
    )
    await collect(
        "configuration_live_exposed_attributes",
        "configuration",
        "live_metadata",
        metadata_keys=["exposed_attribute_catalog"],
    )
    await collect("guest_mode_get", "guest_mode", "get")
    await collect("request_rules_list", "request_rules", "list")
    await collect("knowledge_list", "knowledge", "list")
    await collect("quiet_hours_get", "quiet_hours", "get")
    await collect("usage_summary", "usage", "summary")
    await collect("usage_daily", "usage", "daily")
    await collect("usage_runs", "usage", "runs", limit=30)
    await collect("usage_retention", "usage", "retention")
    scopes = await collect("scopes_catalog", "scopes", "catalog")
    current_scope = next(
        (
            item["scope_id"]
            for item in scopes.get("scopes", [])
            if item.get("is_current_user")
        ),
        None,
    )
    if current_scope:
        await collect(
            "memories_list",
            "memories",
            "list",
            scope_id=current_scope,
            limit=100,
        )
        await collect(
            "temporary_memories_list",
            "memories",
            "temporary_list",
            scope_id=current_scope,
            limit=100,
        )
        await collect(
            "conversations_list",
            "conversations",
            "list",
            scope_id=current_scope,
            limit=50,
        )
    await collect("conversations_active", "conversations", "active")

    admin = MockUser(
        id=f"latency-browser-{label}",
        name=f"Latency Browser {label}",
        is_owner=True,
    )
    admin.add_to_hass(hass)
    refresh_token = await hass.auth.async_create_refresh_token(admin, CLIENT_ID)
    access_token = hass.auth.async_create_access_token(refresh_token)
    http_client = await aiohttp_client(hass.http.app)
    base_url = str(http_client.make_url("/")).rstrip("/")
    expires_in = int(refresh_token.access_token_expiration.total_seconds())
    auth_data = {
        "hassUrl": base_url,
        "clientId": CLIENT_ID,
        "expires": int(time.time() * 1000) + expires_in * 1000,
        "refresh_token": refresh_token.token,
        "access_token": access_token,
        "expires_in": expires_in,
    }

    repo_root = Path(__file__).resolve().parents[2]
    browser_output = output.with_name(f"{output.stem}-browser.json")
    await _run_playwright(
        repo_root=repo_root,
        spec="ci/frontend_latency/latency.spec.mjs",
        config="ci/frontend_latency/playwright.config.mjs",
        env={
            "REAL_HA_FRONTEND_URL": base_url,
            "REAL_HA_FRONTEND_AUTH": json.dumps(auth_data),
            "EOAI_BROWSER_LATENCY_OUTPUT": str(browser_output.resolve()),
            "EOAI_LATENCY_RUNS": str(runs),
        },
        failure_label="Manual genuine-HA latency browser diagnostics failed",
    )

    browser = json.loads(browser_output.read_text(encoding="utf-8"))
    browser_output.unlink(missing_ok=True)
    output.write_text(
        json.dumps(
            {
                "label": label,
                "runs": runs,
                "backend": backend,
                "browser": browser,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
