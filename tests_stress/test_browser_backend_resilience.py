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
from homeassistant.components.websocket_api.const import DATA_CONNECTIONS
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from tests_real_ha.test_browser_backend_acceptance import (
    _run_playwright,
    _start_ws_bridge,
    real_ha_shell as real_ha_shell,
)
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _entry,
    _setup_entry,
)
from tests_stress.conftest import record
from tests_stress.test_os_storage_faults import real_store_io as real_store_io


@pytest.fixture
async def ownership_shell(hass, real_ha_shell, real_store_io):
    # The normal shell fixture provides authentication; all persisted manager and
    # config-entry data in this journey use the restored, real atomic Store I/O.
    return real_ha_shell


async def test_native_editor_ownership_and_satellite_registry_recovery(
    hass, ownership_shell, hass_ws_client, monkeypatch, stress_trace
):
    from custom_components.extended_openai_conversation_responses.const import DOMAIN
    from homeassistant.components import conversation
    from homeassistant.core import Context
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    from tests_real_ha.test_management_backend_acceptance import (
        _conversation_subentry,
        _fresh_reload,
        _management_call,
    )
    from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire

    shell = ownership_shell
    entry = shell["entry"]
    client = await _admin_client(hass, hass_ws_client)
    office_user = MockUser(id="ownership-office-user", name="Office retained owner")
    office_user.add_to_hass(hass)
    devices = dr.async_get(hass)
    old = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "ownership-old")},
        name="Old Kitchen device",
    )
    replacement = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "ownership-new")},
        name="Replacement Kitchen device",
    )
    registry = er.async_get(hass)
    kitchen = registry.async_get_or_create(
        "assist_satellite",
        DOMAIN,
        "ownership-old",
        device_id=old.id,
        suggested_object_id="ownership_kitchen",
    )
    spare = registry.async_get_or_create(
        "assist_satellite",
        DOMAIN,
        "ownership-new",
        device_id=replacement.id,
        suggested_object_id="ownership_spare",
    )
    for entity in (kitchen, spare):
        hass.states.async_set(
            entity.entity_id, "idle", {"friendly_name": entity.entity_id}
        )
    await _management_call(
        client,
        entry=entry,
        section="configuration",
        action="update",
        config={
            "api_mode": "chat_completions",
            "chat_model": "gpt-4.1",
            "reasoning_effort": "",
            "memory_auto_retrieve_limit": 3,
            "voice_scope_policy": "device_mapping",
            "voice_device_mappings": {old.id: "user:ownership-office-user"},
        },
    )
    agent = conversation.async_get_agent(hass, entry.entry_id)
    for owner, marker in (
        (office_user.id, "OFFICE_PRIVATE_REGISTRY"),
        (shell["env"]["REAL_HA_SMOKE_USER_ID"], "KITCHEN_PRIVATE_REGISTRY"),
    ):
        await agent._memory.async_add(
            owner,
            f"My registry calibration token is {marker}",
            "preferences",
            "explicit",
        )

    async def replace_registry(_request):
        registry.async_update_entity(
            kitchen.entity_id, new_entity_id="assist_satellite.ownership_office"
        )
        registry.async_update_entity(
            spare.entity_id, new_entity_id="assist_satellite.ownership_kitchen"
        )
        hass.states.async_remove(kitchen.entity_id)
        hass.states.async_remove(spare.entity_id)
        for entity_id in (
            "assist_satellite.ownership_office",
            "assist_satellite.ownership_kitchen",
        ):
            hass.states.async_set(entity_id, "idle", {"friendly_name": entity_id})
        assert (
            registry.async_get("assist_satellite.ownership_office").device_id == old.id
        )
        assert (
            registry.async_get("assist_satellite.ownership_kitchen").device_id
            == replacement.id
        )
        record(
            stress_trace,
            "registry_replaced",
            old_device=old.id,
            replacement_device=replacement.id,
        )
        return web.json_response({"office": old.id, "kitchen": replacement.id})

    async def reload_state(_request):
        before = await _management_call(
            client, entry=entry, section="configuration", action="get"
        )
        rules_before = await _management_call(
            client, entry=entry, section="request_rules", action="list"
        )
        # Flush HA's scheduled config-entry write, then independently inspect the
        # actual disk envelope before discarding process-local managers.
        await hass.config_entries._store._async_handle_delayed_save()
        raw = json.loads(
            Path(hass.config.path(".storage", "core.config_entries")).read_text()
        )
        subentry = _conversation_subentry(entry)

        def persisted_subentry(value):
            if isinstance(value, dict):
                if value.get("subentry_id") == subentry.subentry_id and "data" in value:
                    return value
                for child in value.values():
                    found = persisted_subentry(child)
                    if found:
                        return found
            if isinstance(value, list):
                for child in value:
                    found = persisted_subentry(child)
                    if found:
                        return found
            return None

        persisted = persisted_subentry(raw)
        assert persisted is not None
        assert persisted["data"] == dict(subentry.data)
        await _fresh_reload(hass, entry)
        after = await _management_call(
            client, entry=entry, section="configuration", action="get"
        )
        rules_after = await _management_call(
            client, entry=entry, section="request_rules", action="list"
        )
        assert after["config"] == before["config"]
        assert rules_after["rules"] == rules_before["rules"]
        record(stress_trace, "durable_reload", native_ownership_reload_checks=1)
        return web.json_response(after["config"])

    async def probe_voice(_request):
        agent = conversation.async_get_agent(hass, entry.entry_id)
        wire = _install_wire(
            monkeypatch,
            agent,
            [_chat_sse_text("Registry owner verified") for _ in range(2)],
        )
        users = []
        original = agent.async_process

        async def capture(user_input):
            users.append(user_input.context.user_id)
            return await original(user_input)

        with monkeypatch.context() as patch:
            patch.setattr(agent, "async_process", capture)
            for device, satellite, own, other in (
                (
                    old.id,
                    "assist_satellite.ownership_office",
                    "OFFICE_PRIVATE_REGISTRY",
                    "KITCHEN_PRIVATE_REGISTRY",
                ),
                (
                    replacement.id,
                    "assist_satellite.ownership_kitchen",
                    "KITCHEN_PRIVATE_REGISTRY",
                    "OFFICE_PRIVATE_REGISTRY",
                ),
            ):
                result = await conversation.async_converse(
                    hass=hass,
                    text="What is my registry calibration token?",
                    conversation_id=None,
                    context=Context(),
                    language="en",
                    agent_id=entry.entry_id,
                    device_id=device,
                    satellite_id=satellite,
                )
                assert result.response.error_code is None
                body = json.dumps(wire.requests[-1]["body"])
                assert own in body and other not in body
        assert users == [None, None]
        record(stress_trace, "voice_wire", native_registry_private_probes=2)
        return web.json_response(
            {"owners": 2, "private_markers": 2, "authenticated_users": users}
        )

    app = web.Application()
    app.router.add_post("/replace-registry", replace_registry)
    app.router.add_post("/reload", reload_state)
    app.router.add_post("/probe-voice", probe_voice)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    try:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parent.parent,
            spec="tests_browser/real-ha-shell.spec.mjs",
            config="playwright.real-ha-shell.config.mjs",
            env={
                **shell["env"],
                "EOAI_NATIVE_OWNERSHIP": "1",
                "REAL_HA_OWNERSHIP_CONTROL": url,
                "REAL_HA_OLD_DEVICE": old.id,
                "REAL_HA_NEW_DEVICE": replacement.id,
            },
            failure_label="Native editor ownership and registry recovery failed",
        )
        record(
            stress_trace,
            "summary",
            native_editor_ownership_cases=4,
            native_registry_recovery_cases=1,
        )
    finally:
        await runner.cleanup()
        await _close_native_shell(hass, shell)


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_HA_BROWSER") != "1",
    reason="scheduled/manual browser acceptance only",
)


async def _close_native_shell(hass, shell):
    await shell["http_client"].close()
    await hass.async_stop()
    async with asyncio.timeout(5):
        while hass.data.get(DATA_CONNECTIONS, 0):
            await asyncio.sleep(0.05)
    await asyncio.sleep(0.2)
    loop = asyncio.get_running_loop()
    for handle in tuple(loop._scheduled):
        socket = getattr(getattr(handle, "_callback", None), "__self__", None)
        if not handle.cancelled() and isinstance(socket, web.WebSocketResponse):
            await socket.close()
            socket._cancel_heartbeat()
            handle.cancel()


async def test_native_shell_accessibility_error_recovery(
    hass, real_ha_shell, monkeypatch, stress_trace
):
    from ci.enhanced_evidence import checkout_sha
    from ci.frontend_latency.review import POLICY, check_accessibility
    from ci.frontend_latency.test_frontend_accessibility import (
        test_genuine_shell_accessibility_semantics,
    )

    path = (
        Path(os.environ.get("STRESS_ARTIFACT_DIR", "stress-artifacts"))
        / "native-accessibility.json"
    )
    monkeypatch.setenv("EOAI_ACCESSIBILITY_OUTPUT", str(path.resolve()))
    try:
        await test_genuine_shell_accessibility_semantics(real_ha_shell)
        path = Path(
            os.environ.get(
                "EOAI_ACCESSIBILITY_OUTPUT", "latency-results/accessibility.json"
            )
        )
        result = json.loads(path.read_text(encoding="utf-8"))
        errors = check_accessibility(
            result, checkout_sha(), json.loads(POLICY.read_text(encoding="utf-8"))
        )
        assert not errors, "\n".join(errors)
        record(
            stress_trace,
            "summary",
            native_semantic_scans=len(result["scans"]),
            native_error_semantic_scans=sum(
                scan["state"] not in {"route", "tool-editor", "rule-editor"}
                for scan in result["scans"]
            ),
        )
    finally:
        await _close_native_shell(hass, real_ha_shell)


async def test_native_shell_lifetime_and_assistant_isolation(
    hass, real_ha_shell, monkeypatch, stress_seed, stress_trace
):
    """One real HA document warms native widgets and two distinct assistants."""
    from custom_components.extended_openai_conversation_responses import backup_transfer

    secondary = _entry("Nightly second assistant")
    await _setup_entry(hass, secondary)
    # The abandonment journey waits for a genuinely elapsed TTL, then invokes
    # supported lazy cleanup. It does not pretend disconnect immediately cancels.
    monkeypatch.setattr(backup_transfer, "TRANSFER_TTL_SECONDS", 10)
    monkeypatch.setattr(backup_transfer, "MAX_TRANSFER_SESSIONS", 1)
    owned_paths = []
    original_create = backup_transfer._async_create_upload_file

    async def track_file(hass):
        path = await original_create(hass)
        owned_paths.append(Path(path))
        return path

    monkeypatch.setattr(backup_transfer, "_async_create_upload_file", track_file)

    async def transfer_state(_request):
        sessions = list(backup_transfer._imports(hass).values())
        return web.json_response(
            {
                "imports": len(sessions),
                "received": sum(item.received for item in sessions),
                "reserved": sum(item.expected_size for item in sessions),
                "files_present": await hass.async_add_executor_job(
                    lambda: all(Path(item.path).exists() for item in sessions)
                ),
                "owned_files_on_disk": await hass.async_add_executor_job(
                    lambda: sum(path.exists() for path in owned_paths)
                ),
            }
        )

    control = web.Application()
    control.router.add_get("/transfer-state", transfer_state)
    runner = web.AppRunner(control)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    control_url = (
        f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/transfer-state"
    )
    profile = os.environ.get("REAL_HA_BROWSER_PROFILE", "chromium")
    evidence_path = (
        Path(os.environ.get("STRESS_ARTIFACT_DIR", "stress-artifacts"))
        / f"native-journeys-{profile}.json"
    )
    evidence_path.unlink(missing_ok=True)
    try:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parent.parent,
            spec="tests_browser/real-ha-shell.spec.mjs",
            config="playwright.real-ha-shell.config.mjs",
            env={
                **real_ha_shell["env"],
                "EOAI_NATIVE_ENDURANCE": "1",
                "STRESS_SEED": str(stress_seed),
                "REAL_HA_TRANSFER_STATE": control_url,
                "EOAI_NATIVE_EVIDENCE": str(evidence_path.resolve()),
            },
            failure_label="Native lifetime and assistant isolation failed",
        )
        reports = json.loads(evidence_path.read_text(encoding="utf-8"))
        expected = {"composite", "assistants"}
        if profile == "chromium":
            expected.update({"transfer", "retention"})
        assert set(reports) == expected
        measured = {
            key: value for report in reports.values() for key, value in report.items()
        }
        record(stress_trace, "summary", native_lifetime_journeys=1, **measured)
    finally:
        await runner.cleanup()
        await _close_native_shell(hass, real_ha_shell)


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
        async with asyncio.timeout(5):
            while hass.data.get(DATA_CONNECTIONS, 0):
                await asyncio.sleep(0.05)
        # Browser process exit and HA's socket-close callback cross event loops.
        # Let the aiohttp close callback cancel its heartbeat before pytest's
        # strict leftover-timer audit runs.
        await asyncio.sleep(0.2)
        loop = asyncio.get_running_loop()
        closed_sockets: set[web.WebSocketResponse] = set()
        for _ in range(10):
            handles = [
                (handle, socket)
                for handle in tuple(loop._scheduled)
                if not handle.cancelled()
                for socket in (
                    getattr(getattr(handle, "_callback", None), "__self__", None),
                )
                if isinstance(socket, web.WebSocketResponse)
            ]
            if not handles:
                break
            for handle, socket in handles:
                if not socket.closed:
                    await socket.close()
                socket._cancel_heartbeat()
                handle.cancel()
                closed_sockets.add(socket)
            await asyncio.sleep(0.05)
        assert not [
            handle
            for handle in tuple(loop._scheduled)
            if not handle.cancelled()
            and isinstance(
                getattr(getattr(handle, "_callback", None), "__self__", None),
                web.WebSocketResponse,
            )
        ]
        if closed_sockets:
            record(stress_trace, "closed_ha_test_sockets", count=len(closed_sockets))


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
