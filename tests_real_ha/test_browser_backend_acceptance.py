"""Run the shipped management panel against Home Assistant's real WS backend."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
from typing import Any

from aiohttp import web
import pytest
from homeassistant.core import HomeAssistant

from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _entry,
    _setup_entry,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_HA_BROWSER") != "1",
    reason="enabled only by the browser-to-genuine-HA acceptance job",
)


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
    npx = shutil.which("npx")
    assert npx is not None, "npx is required for genuine HA browser acceptance"
    env = {
        **os.environ,
        "CI": "1",
        "REAL_HA_BACKEND_URL": backend_url,
    }

    try:
        process = await asyncio.create_subprocess_exec(
            npx,
            "playwright",
            "test",
            "tests_browser/real-ha-backend.spec.mjs",
            "--config=playwright.config.mjs",
            cwd=repo_root,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async with asyncio.timeout(120):
            stdout, _ = await process.communicate()
        output = stdout.decode("utf-8", errors="replace")
        assert process.returncode == 0, f"Playwright genuine-HA acceptance failed:\n{output}"
    finally:
        await runner.cleanup()
