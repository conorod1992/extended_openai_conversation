"""Nightly Chromium failures against real HA management WebSocket clients."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import time
from typing import Any

from aiohttp import web
import pytest
from pytest_homeassistant_custom_component.common import CLIENT_ID, MockUser

from homeassistant.components import onboarding
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
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

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_HA_BROWSER") != "1",
    reason="scheduled/manual browser acceptance only",
)


async def test_browser_reconnect_and_permission_changes_against_real_ha_ws(
    hass: HomeAssistant,
    hass_ws_client: Any,
    stress_seed: int,
    stress_trace: list[dict],
) -> None:
    entry = _entry("Nightly browser transport acceptance")
    await _setup_entry(hass, entry)
    owner_client = await _admin_client(hass, hass_ws_client)
    restricted = MockUser(
        id="nightly-browser-restricted",
        name="Nightly browser restricted",
        is_owner=False,
    )
    restricted.add_to_hass(hass)
    refresh = await hass.auth.async_create_refresh_token(restricted, CLIENT_ID)
    token = hass.auth.async_create_access_token(refresh)
    restricted_client = await hass_ws_client(hass, token)
    runner, backend_url = await _start_ws_bridge(
        owner_client, restricted_client=restricted_client
    )
    try:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parent.parent,
            spec="tests_browser/real-ha-network-resilience.stress.mjs",
            config="playwright.stress.config.mjs",
            env={
                "REAL_HA_BACKEND_URL": backend_url,
                "REAL_HA_CONTROL_URL": backend_url.replace("/callws", "/control"),
                "STRESS_SEED": str(stress_seed),
                "EOAI_OLD_FRONTEND_DIR": os.environ.get("EOAI_OLD_FRONTEND_DIR", ""),
            },
            failure_label="Nightly browser/real-HA WebSocket resilience failed",
        )
        record(
            stress_trace,
            "browser_real_ha_transport",
            seed=stress_seed,
            identity_changes=True,
        )
    finally:
        await runner.cleanup()


async def test_real_ha_shell_auth_expiry_and_websocket_loss(
    hass: HomeAssistant,
    aiohttp_client: Any,
    hass_storage: dict[str, Any],
    socket_enabled: Any,
    stress_seed: int,
    stress_trace: list[dict],
) -> None:
    del socket_enabled
    hass_storage[onboarding.STORAGE_KEY] = {
        "version": onboarding.STORAGE_VERSION,
        "data": {"done": list(onboarding.STEPS)},
    }
    assert await async_setup_component(hass, "websocket_api", {})
    assert await async_setup_component(hass, "frontend", {})
    entry = _entry("Nightly real HA shell resilience")
    await _setup_entry(hass, entry)
    owner = MockUser(
        id="nightly-shell-owner", name="Nightly shell owner", is_owner=True
    )
    owner.add_to_hass(hass)
    client = await aiohttp_client(hass.http.app)
    base_url = str(client.make_url("/")).rstrip("/")
    current = {"refresh": await hass.auth.async_create_refresh_token(owner, CLIENT_ID)}

    def auth_data() -> dict[str, Any]:
        refresh = current["refresh"]
        duration = int(refresh.access_token_expiration.total_seconds())
        return {
            "hassUrl": base_url,
            "clientId": CLIENT_ID,
            "expires": int(time.time() * 1000) + duration * 1000,
            "refresh_token": refresh.token,
            "access_token": hass.auth.async_create_access_token(refresh),
            "expires_in": duration,
        }

    initial_auth = auth_data()

    async def expire(_request: web.Request) -> web.Response:
        hass.auth.async_remove_refresh_token(current["refresh"])
        current["refresh"] = await hass.auth.async_create_refresh_token(
            owner, CLIENT_ID
        )
        return web.json_response(
            auth_data(), headers={"Access-Control-Allow-Origin": "*"}
        )

    app = web.Application()
    app.router.add_post("/expire", expire)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = getattr(site._server, "sockets", None)
    assert sockets
    control_url = f"http://127.0.0.1:{sockets[0].getsockname()[1]}/expire"
    try:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parent.parent,
            spec="tests_browser/real-ha-shell-resilience.stress.mjs",
            config="playwright.stress.config.mjs",
            env={
                "REAL_HA_FRONTEND_URL": base_url,
                "REAL_HA_FRONTEND_AUTH": json.dumps(initial_auth),
                "REAL_HA_AUTH_CONTROL_URL": control_url,
                "STRESS_SEED": str(stress_seed),
            },
            failure_label="Genuine HA shell auth and WebSocket resilience failed",
        )
        record(stress_trace, "real_ha_shell_auth_ws", seed=stress_seed)
    finally:
        await runner.cleanup()
        await client.close()
        await hass.async_stop()
        await asyncio.sleep(0)


async def test_published_frontend_assets_against_new_real_ha_backend(
    hass: HomeAssistant,
    hass_ws_client: Any,
    stress_seed: int,
    stress_trace: list[dict],
) -> None:
    old_frontend = Path(os.environ["EOAI_OLD_FRONTEND_DIR"])
    assert (old_frontend / "management-panel.js").exists()
    entry = _entry("Nightly published frontend upgrade acceptance")
    await _setup_entry(hass, entry)
    owner_client = await _admin_client(hass, hass_ws_client)
    runner, backend_url = await _start_ws_bridge(owner_client)
    try:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parent.parent,
            spec="tests_browser/real-ha-upgrade-cache.stress.mjs",
            config="playwright.stress.config.mjs",
            env={
                "REAL_HA_BACKEND_URL": backend_url,
                "EOAI_OLD_FRONTEND_DIR": str(old_frontend),
                "STRESS_SEED": str(stress_seed),
            },
            failure_label="Published frontend/new HA backend upgrade cache failed",
        )
        record(stress_trace, "published_frontend_cache", seed=stress_seed)
    finally:
        await runner.cleanup()
