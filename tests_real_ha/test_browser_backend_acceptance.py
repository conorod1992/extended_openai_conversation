"""Run the shipped management panel against genuine Home Assistant browser seams."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any

from aiohttp import web
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import CLIENT_ID, MockUser

from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _entry,
    _setup_entry,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_HA_BROWSER") != "1",
    reason="enabled only by the browser-to-genuine-HA acceptance job",
)


async def _run_playwright(
    *,
    repo_root: Path,
    spec: str,
    config: str,
    env: dict[str, str],
    failure_label: str,
) -> None:
    """Run one Playwright acceptance target and surface its output on failure."""
    npx = shutil.which("npx")
    assert npx is not None, "npx is required for genuine HA browser acceptance"
    process = await asyncio.create_subprocess_exec(
        npx,
        "playwright",
        "test",
        spec,
        f"--config={config}",
        cwd=repo_root,
        env={**os.environ, "CI": "1", **env},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    async with asyncio.timeout(180):
        stdout, _ = await process.communicate()
    output = stdout.decode("utf-8", errors="replace")
    assert process.returncode == 0, f"{failure_label}:\n{output}"


async def _start_ws_bridge(client: Any) -> tuple[web.AppRunner, str]:
    """Expose a transparent HTTP bridge into HA's authenticated WS test client."""
    lock = asyncio.Lock()

    async def call_ws(request: web.Request) -> web.Response:
        try:
            message = json.loads(await request.text())
        except (json.JSONDecodeError, TypeError) as err:
            return web.json_response(
                {"message": f"Invalid management message: {err}"},
                status=400,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        if not isinstance(message, dict):
            return web.json_response(
                {"message": "Management message must be an object"},
                status=400,
                headers={"Access-Control-Allow-Origin": "*"},
            )

        async with lock:
            await client.send_json_auto_id(message)
            response = await client.receive_json()

        if not response.get("success"):
            error = response.get("error") or {}
            return web.json_response(
                {
                    "message": error.get("message", "Home Assistant WebSocket call failed"),
                    "error": error,
                },
                status=400,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        return web.json_response(
            response.get("result"),
            headers={"Access-Control-Allow-Origin": "*"},
        )

    app = web.Application()
    app.router.add_post("/callws", call_ws)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = getattr(site._server, "sockets", None)  # noqa: SLF001 - test server port
    assert sockets, "genuine HA browser bridge did not bind a socket"
    port = sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/callws"


@pytest.mark.asyncio
async def test_shipped_browser_frontend_talks_to_real_management_websocket(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """Chromium CRUD journeys must satisfy the genuine HA management contract."""
    entry = _entry("Browser Backend Acceptance")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    runner, backend_url = await _start_ws_bridge(client)

    repo_root = Path(__file__).resolve().parent.parent
    try:
        await _run_playwright(
            repo_root=repo_root,
            spec="tests_browser/real-ha-backend.spec.mjs",
            config="playwright.config.mjs",
            env={"REAL_HA_BACKEND_URL": backend_url},
            failure_label="Playwright genuine-HA backend acceptance failed",
        )
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_shipped_browser_frontend_loads_inside_real_home_assistant_shell(
    hass: HomeAssistant,
    aiohttp_client: Any,
) -> None:
    """HA itself must register, serve, instantiate, and connect the shipped panel."""
    assert await async_setup_component(hass, "websocket_api", {})
    assert await async_setup_component(hass, "frontend", {})

    entry = _entry("Browser Frontend Shell Acceptance")
    await _setup_entry(hass, entry)

    admin = MockUser(
        id="browser-frontend-shell-admin",
        name="Browser Frontend Shell Admin",
        is_owner=True,
    )
    admin.add_to_hass(hass)
    refresh_token = await hass.auth.async_create_refresh_token(admin, CLIENT_ID)
    access_token = hass.auth.async_create_access_token(refresh_token)

    # aiohttp_client exposes HA's actual HTTP application on a real loopback TCP
    # port, allowing Chromium to load the genuine Home Assistant frontend and use
    # its own websocket/authentication stack rather than our standalone fixture.
    client = await aiohttp_client(hass.http.app)
    base_url = str(client.make_url("/")).rstrip("/")
    expires_in = int(refresh_token.access_token_expiration.total_seconds())
    auth_data = {
        "hassUrl": base_url,
        "clientId": CLIENT_ID,
        "expires": int(time.time() * 1000) + expires_in * 1000,
        "refresh_token": refresh_token.token,
        "access_token": access_token,
        "expires_in": expires_in,
    }

    repo_root = Path(__file__).resolve().parent.parent
    await _run_playwright(
        repo_root=repo_root,
        spec="tests_browser/real-ha-shell.spec.mjs",
        config="playwright.real-ha-shell.config.mjs",
        env={
            "REAL_HA_FRONTEND_URL": base_url,
            "REAL_HA_FRONTEND_AUTH": json.dumps(auth_data),
        },
        failure_label="Playwright genuine Home Assistant frontend-shell acceptance failed",
    )
