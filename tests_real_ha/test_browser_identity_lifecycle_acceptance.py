"""Browser identity and lifecycle boundaries against genuine Home Assistant."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from aiohttp import web
import pytest

from custom_components.extended_openai_conversation_responses.memory import async_get_memory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from tests_real_ha.test_browser_backend_acceptance import (
    _run_playwright,
    _start_ws_bridge,
)
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _conversation_subentry,
    _entry,
    _setup_entry,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_HA_BROWSER") != "1",
    reason="enabled only by the browser-to-genuine-HA acceptance job",
)

_SPEC = "tests_browser/real-ha-frontend-identity-lifecycle.spec.mjs"
_LIFECYCLE_USER = "browser-lifecycle-boundary-admin"
_USER_A = "browser-identity-admin-a"
_USER_B = "browser-identity-admin-b"
_LIFECYCLE_MARKER = "Browser lifecycle boundary memory"
_MARKER_A = "Private browser memory for user A"
_MARKER_B = "Private browser memory for user B"


async def _start_ack_gate_bridge(
    client: Any,
) -> tuple[web.AppRunner, str, asyncio.Event, asyncio.Event]:
    """Hold one committed Memory acknowledgement across an EOAI reload."""
    lock = asyncio.Lock()
    committed = asyncio.Event()
    release = asyncio.Event()
    held = False

    async def call_ws(request: web.Request) -> web.Response:
        nonlocal held
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
                    "message": error.get(
                        "message", "Home Assistant WebSocket call failed"
                    ),
                    "error": error,
                },
                status=400,
                headers={"Access-Control-Allow-Origin": "*"},
            )

        if (
            not held
            and message.get("section") == "memories"
            and message.get("action") == "add"
        ):
            # At this point Home Assistant has already returned success: the
            # mutation is committed. Only the browser acknowledgement is held.
            held = True
            committed.set()
            await release.wait()

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
    sockets = getattr(site._server, "sockets", None)
    assert sockets
    port = sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/callws", committed, release


async def _personal_memory_contents(
    hass: HomeAssistant,
    entry: Any,
    user_id: str,
) -> list[str]:
    manager = await async_get_memory(
        hass,
        entry.entry_id,
        _conversation_subentry(entry).subentry_id,
    )
    return [
        record.content
        for record in await manager.async_list(user_id, limit=100)
    ]


@pytest.mark.asyncio
async def test_browser_memory_acknowledgement_spans_real_integration_unload_reload(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """A committed browser write must settle once when EOAI reloads before its ack."""
    entry = _entry("Browser lifecycle acknowledgement")
    await _setup_entry(hass, entry)
    client = await _admin_client(
        hass,
        hass_ws_client,
        user_id=_LIFECYCLE_USER,
        name="Browser Lifecycle Boundary Admin",
    )
    runner, backend_url, committed, release = await _start_ack_gate_bridge(client)
    repo_root = Path(__file__).resolve().parent.parent

    browser = asyncio.create_task(
        _run_playwright(
            repo_root=repo_root,
            spec=_SPEC,
            config="playwright.config.mjs",
            env={"REAL_HA_BACKEND_URL": backend_url},
            failure_label="Playwright EOAI unload/reload acknowledgement acceptance failed",
        )
    )
    try:
        await asyncio.wait_for(committed.wait(), 60)
        assert await _personal_memory_contents(
            hass, entry, _LIFECYCLE_USER
        ) == [_LIFECYCLE_MARKER]

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.NOT_LOADED

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        # Only now may the old successful response reach the still-open browser.
        release.set()
        await asyncio.wait_for(browser, 120)

        contents = await _personal_memory_contents(
            hass, entry, _LIFECYCLE_USER
        )
        assert contents.count(_LIFECYCLE_MARKER) == 1
    finally:
        release.set()
        if not browser.done():
            browser.cancel()
            await asyncio.gather(browser, return_exceptions=True)
        await runner.cleanup()


@pytest.mark.asyncio
async def test_two_browser_sessions_use_distinct_authenticated_ha_users(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """Frontend state and private Memory stay isolated across simultaneous users."""
    entry = _entry("Browser identity isolation")
    await _setup_entry(hass, entry)

    client_a = await _admin_client(
        hass,
        hass_ws_client,
        user_id=_USER_A,
        name="Browser Identity Admin A",
    )
    client_b = await _admin_client(
        hass,
        hass_ws_client,
        user_id=_USER_B,
        name="Browser Identity Admin B",
    )
    runner_a, backend_a = await _start_ws_bridge(client_a)
    runner_b, backend_b = await _start_ws_bridge(client_b)

    try:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parent.parent,
            spec=_SPEC,
            config="playwright.config.mjs",
            env={
                "REAL_HA_BACKEND_URL_A": backend_a,
                "REAL_HA_BACKEND_URL_B": backend_b,
            },
            failure_label="Playwright separate-authenticated-user acceptance failed",
        )
    finally:
        await runner_a.cleanup()
        await runner_b.cleanup()

    a = await _personal_memory_contents(hass, entry, _USER_A)
    b = await _personal_memory_contents(hass, entry, _USER_B)
    assert a.count(_MARKER_A) == 1
    assert _MARKER_B not in a
    assert b.count(_MARKER_B) == 1
    assert _MARKER_A not in b
