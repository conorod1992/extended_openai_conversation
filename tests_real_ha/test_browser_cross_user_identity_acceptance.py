"""Cross-user browser identity and authorization boundaries against genuine HA."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from aiohttp import web
import pytest
from pytest_homeassistant_custom_component.common import CLIENT_ID, MockUser

from custom_components.extended_openai_conversation_responses.memory import async_get_memory
from homeassistant.core import HomeAssistant
from tests_real_ha.test_browser_backend_acceptance import (
    _run_playwright,
    _start_ws_bridge,
)
from tests_real_ha.test_management_backend_acceptance import (
    _conversation_subentry,
    _entry,
    _setup_entry,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_HA_BROWSER") != "1",
    reason="enabled only by the browser-to-genuine-HA acceptance job",
)

_SPEC = "tests_browser/real-ha-cross-user-identity.stress.mjs"
_USER_A = "cross-user-browser-admin-a"
_USER_B = "cross-user-browser-admin-b"
_PERMISSION_USER = "cross-user-browser-permission-admin"
_MARKER_A = "Private same-browser marker A"
_MARKER_B = "Private same-browser marker B"


async def _client_for_user(
    hass: HomeAssistant,
    hass_ws_client: Any,
    user: MockUser,
) -> Any:
    user.add_to_hass(hass)
    refresh = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    token = hass.auth.async_create_access_token(refresh)
    client = await hass_ws_client(hass, token)
    client._eoai_test_hass = hass
    return client


async def _start_switchable_bridge(
    clients: dict[str, Any],
    *,
    initial: str,
) -> tuple[web.AppRunner, str, str]:
    """Expose one management endpoint whose genuine HA identity can be switched."""
    state = {"identity": initial}
    lock = asyncio.Lock()

    async def control(request: web.Request) -> web.Response:
        payload = json.loads(await request.text() or "{}")
        identity = payload.get("identity")
        if identity not in clients:
            return web.json_response(
                {"message": "Unknown identity"},
                status=400,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        state["identity"] = identity
        return web.json_response(
            {"identity": identity},
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def call_ws(request: web.Request) -> web.Response:
        message = json.loads(await request.text())
        selected = clients[state["identity"]]
        async with lock:
            await selected.send_json_auto_id(message)
            response = await selected.receive_json()
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
        return web.json_response(
            response.get("result"),
            headers={"Access-Control-Allow-Origin": "*"},
        )

    app = web.Application()
    app.router.add_post("/callws", call_ws)
    app.router.add_post("/control", control)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = getattr(site._server, "sockets", None)
    assert sockets
    root = f"http://127.0.0.1:{sockets[0].getsockname()[1]}"
    return runner, f"{root}/callws", f"{root}/control"


async def _start_permission_bridge(
    client: Any,
    user: MockUser,
) -> tuple[web.AppRunner, str, str]:
    """Keep one authenticated HA client while changing that user's owner role."""
    lock = asyncio.Lock()

    async def control(request: web.Request) -> web.Response:
        payload = json.loads(await request.text() or "{}")
        user.is_owner = bool(payload.get("admin"))
        return web.json_response(
            {"is_owner": user.is_owner, "is_admin": user.is_admin},
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def call_ws(request: web.Request) -> web.Response:
        message = json.loads(await request.text())
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
        return web.json_response(
            response.get("result"),
            headers={"Access-Control-Allow-Origin": "*"},
        )

    app = web.Application()
    app.router.add_post("/callws", call_ws)
    app.router.add_post("/control", control)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = getattr(site._server, "sockets", None)
    assert sockets
    root = f"http://127.0.0.1:{sockets[0].getsockname()[1]}"
    return runner, f"{root}/callws", f"{root}/control"


async def _memory_contents(
    hass: HomeAssistant,
    entry: Any,
    user_id: str,
) -> list[str]:
    manager = await async_get_memory(
        hass,
        entry.entry_id,
        _conversation_subentry(entry).subentry_id,
    )
    return [record.content for record in await manager.async_list(user_id, limit=100)]


@pytest.mark.asyncio
async def test_cross_user_browser_identity_and_authorization_boundaries(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """Exercise identity handoff, live role mutation and cross-admin stale writes."""
    entry = _entry("Cross-user browser boundaries")
    await _setup_entry(hass, entry)

    user_a = MockUser(id=_USER_A, name="Cross-user Browser Admin A", is_owner=True)
    user_b = MockUser(id=_USER_B, name="Cross-user Browser Admin B", is_owner=True)
    client_a = await _client_for_user(hass, hass_ws_client, user_a)
    client_b = await _client_for_user(hass, hass_ws_client, user_b)

    switch_runner, switch_backend, switch_control = await _start_switchable_bridge(
        {"a": client_a, "b": client_b},
        initial="a",
    )

    permission_user = MockUser(
        id=_PERMISSION_USER,
        name="Cross-user Browser Permission Admin",
        is_owner=True,
    )
    permission_client = await _client_for_user(
        hass, hass_ws_client, permission_user
    )
    permission_runner, permission_backend, permission_control = (
        await _start_permission_bridge(permission_client, permission_user)
    )

    race_runner_a, race_backend_a = await _start_ws_bridge(client_a)
    race_runner_b, race_backend_b = await _start_ws_bridge(client_b)

    try:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parent.parent,
            spec=_SPEC,
            config="playwright.config.mjs",
            env={
                "REAL_HA_IDENTITY_SWITCH_BACKEND": switch_backend,
                "REAL_HA_IDENTITY_SWITCH_CONTROL": switch_control,
                "REAL_HA_PERMISSION_BACKEND": permission_backend,
                "REAL_HA_PERMISSION_CONTROL": permission_control,
                "REAL_HA_ADMIN_RACE_BACKEND_A": race_backend_a,
                "REAL_HA_ADMIN_RACE_BACKEND_B": race_backend_b,
            },
            failure_label="Cross-user browser identity/authorization acceptance failed",
        )
    finally:
        await switch_runner.cleanup()
        await permission_runner.cleanup()
        await race_runner_a.cleanup()
        await race_runner_b.cleanup()

    a = await _memory_contents(hass, entry, _USER_A)
    b = await _memory_contents(hass, entry, _USER_B)
    assert _MARKER_A in a
    assert _MARKER_B not in a
    assert _MARKER_B in b
    assert _MARKER_A not in b
