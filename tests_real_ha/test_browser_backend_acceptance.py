"""Run the shipped management panel against genuine Home Assistant browser seams."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import time
from typing import Any

from aiohttp import web
import pytest
from pytest_homeassistant_custom_component.common import CLIENT_ID, MockUser
import yaml

from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
    CONF_GUEST_POLICY_VERSION,
    GUEST_POLICY_VERSION,
)
from homeassistant.components import conversation, onboarding
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from tests_real_ha.test_acceptance_lifecycle import _make_entry
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _entry,
    _management_call,
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
    async with asyncio.timeout(600):
        stdout, _ = await process.communicate()
    output = stdout.decode("utf-8", errors="replace")
    assert process.returncode == 0, f"{failure_label}:\n{output}"


async def _start_ws_bridge(
    client: Any, *, restricted_client: Any | None = None
) -> tuple[web.AppRunner, str]:
    """Expose a transparent HTTP bridge into HA's authenticated WS test client."""
    lock = asyncio.Lock()
    state = {"identity": "owner", "delay_ms": 0}

    async def control(request: web.Request) -> web.Response:
        payload = await request.json()
        identity = payload.get("identity", state["identity"])
        if identity not in ("owner", "restricted", "expired"):
            return web.json_response({"message": "Unknown identity"}, status=400)
        if identity == "restricted" and restricted_client is None:
            return web.json_response({"message": "No restricted HA client"}, status=400)
        state["identity"] = identity
        state["delay_ms"] = max(0, min(2000, int(payload.get("delay_ms", 0))))
        return web.json_response(state, headers={"Access-Control-Allow-Origin": "*"})

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

        if state["identity"] == "expired":
            return web.json_response(
                {"message": "Home Assistant session expired"},
                status=401,
                headers={"Access-Control-Allow-Origin": "*"},
            )
        if state["delay_ms"]:
            await asyncio.sleep(state["delay_ms"] / 1000)
        selected = restricted_client if state["identity"] == "restricted" else client
        async with lock:
            await selected.send_json_auto_id(message)
            response = await selected.receive_json()
            if (
                message.get("section") == "configuration"
                and message.get("action") in {"update", "duplicate", "import"}
                and (test_hass := getattr(selected, "_eoai_test_hass", None))
            ):
                await test_hass.async_block_till_done()

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
    # Both assistants are genuine loaded entries; identity coverage must not skip.
    await _setup_entry(hass, _entry("Dependent Journey"))

    class BrowserClient:
        # Slow runner startup must not age an authenticated, unpolled test socket.
        # Authenticate once on first use; a later disconnect still fails the test.
        client = None
        _eoai_test_hass = hass

        async def send_json_auto_id(self, message):
            if self.client is None:
                self.client = await _admin_client(hass, hass_ws_client)
            await self.client.send_json_auto_id(message)

        async def receive_json(self):
            return await self.client.receive_json()

    runner, backend_url = await _start_ws_bridge(BrowserClient())

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
async def test_function_repair_controls_on_cold_functions_route(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """A direct Functions visit exposes and repairs quarantined tools."""
    valid = {
        "spec": {
            "name": "generated_valid_sibling",
            "description": "Preserved sibling",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "get_user_from_user_id"},
        "enabled": False,
    }
    invalid = {
        "spec": {
            "name": "generated_invalid",
            "description": "Needs repair",
            "parameters": {
                "type": "object",
                "properties": {},
                "description": 123,
            },
        },
        "function": {"type": "native", "name": "get_user_from_user_id"},
    }
    entry = _make_entry(
        "Browser Function Repair",
        include_ai_task=False,
        conversation_options={CONF_FUNCTION_TOOLS: yaml.safe_dump([valid, invalid])},
    )
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    before = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    assert len(before["function_repair"]["invalid_tools"]) == 1
    assert [tool["spec"]["name"] for tool in before["config"]["functions"]] == [
        "generated_valid_sibling"
    ]

    class BrowserClient:
        # Authenticate after Chromium starts, so slow browser startup cannot
        # expire an idle Home Assistant test WebSocket.
        client = None

        async def send_json_auto_id(self, message):
            if self.client is None:
                self.client = await _admin_client(
                    hass, hass_ws_client, user_id="browser-function-repair-admin"
                )
            await self.client.send_json_auto_id(message)

        async def receive_json(self):
            return await self.client.receive_json()

    runner, backend_url = await _start_ws_bridge(BrowserClient())
    try:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parent.parent,
            spec="tests_browser/real-ha-function-repair-direct-route.spec.mjs",
            config="playwright.config.mjs",
            env={"REAL_HA_BACKEND_URL": backend_url},
            failure_label="Cold Function Repair controls were unavailable",
        )
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_catalogue", [False, True])
async def test_bulk_function_repair_acknowledgement_survives_failed_reads(
    hass: HomeAssistant,
    hass_ws_client: Any,
    fail_catalogue: bool,
) -> None:
    """A real bulk write supplies its baseline before optional browser refreshes."""
    entry = _make_entry(
        "Browser Bulk Repair",
        include_ai_task=False,
        conversation_options={
            CONF_FUNCTION_TOOLS: "[unterminated",
            "prompt": "Saved prompt before bulk repair",
        },
    )
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    before = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    assert before["function_repair"]["isolatable"] is False
    assert before["function_repair"]["validation_error"]

    class BrowserClient:
        client = None

        async def send_json_auto_id(self, message):
            if self.client is None:
                self.client = await _admin_client(
                    hass, hass_ws_client, user_id="browser-bulk-repair-admin"
                )
            await self.client.send_json_auto_id(message)

        async def receive_json(self):
            return await self.client.receive_json()

    runner, backend_url = await _start_ws_bridge(BrowserClient())
    try:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parent.parent,
            spec="tests_browser/real-ha-bulk-function-repair.spec.mjs",
            config="playwright.config.mjs",
            env={
                "REAL_HA_BACKEND_URL": backend_url,
                "BULK_REPAIR_FAIL_CATALOGUE": "1" if fail_catalogue else "0",
            },
            failure_label="Bulk repair acknowledgement did not survive refresh",
        )
    finally:
        await runner.cleanup()

    # Browser startup can outlive the original test client's idle WS timeout.
    client = await _admin_client(
        hass, hass_ws_client, user_id="bulk-repair-verification-admin"
    )
    persisted = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    assert persisted["revision"] != before["revision"]
    assert [tool["spec"]["name"] for tool in persisted["config"]["functions"]] == [
        "bulk_repaired_tool"
    ]
    assert persisted["config"]["prompt"] == "Saved prompt before bulk repair"
    assert "function_repair" not in persisted


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_render", [False, True])
async def test_configuration_acknowledgement_preserves_newer_browser_edits(
    hass: HomeAssistant,
    hass_ws_client: Any,
    fail_render: bool,
) -> None:
    """A confirmed save survives newer browser edits and presentation failures."""
    entry = _make_entry(
        "Browser Configuration Acknowledgement",
        include_ai_task=False,
        conversation_options={CONF_FUNCTION_TOOLS: [], "prompt": "Initial prompt"},
    )
    await _setup_entry(hass, entry)

    class BrowserClient:
        client = None

        async def send_json_auto_id(self, message):
            if self.client is None:
                self.client = await _admin_client(
                    hass, hass_ws_client, user_id="browser-configuration-ack-admin"
                )
            await self.client.send_json_auto_id(message)

        async def receive_json(self):
            return await self.client.receive_json()

    runner, backend_url = await _start_ws_bridge(BrowserClient())
    try:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parent.parent,
            spec="tests_browser/real-ha-configuration-acknowledgement.spec.mjs",
            config="playwright.config.mjs",
            env={
                "REAL_HA_BACKEND_URL": backend_url,
                "CONFIGURATION_ACK_FAIL_RENDER": "1" if fail_render else "0",
            },
            failure_label="Configuration acknowledgement lost a newer browser draft",
        )
    finally:
        await runner.cleanup()

    client = await _admin_client(
        hass, hass_ws_client, user_id="configuration-ack-verification-admin"
    )
    persisted = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    assert persisted["config"]["prompt"] == "Newer acknowledgement prompt"


@pytest.mark.asyncio
@pytest.mark.parametrize("rename_kind", ["function", "group"])
@pytest.mark.parametrize("dirty_draft", [False, True])
async def test_partial_function_acknowledgement_reloads_guest_policy(
    hass: HomeAssistant,
    hass_ws_client: Any,
    rename_kind: str,
    dirty_draft: bool,
) -> None:
    """Real rename writes must refresh coupled Guest allowlists before preview."""
    tool = {
        "spec": {
            "name": "old_tool",
            "description": "Allowed Guest tool",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "get_history"},
    }
    entry = _make_entry(
        "Browser Guest Rename",
        include_ai_task=False,
        conversation_options={
            CONF_FUNCTION_TOOLS: yaml.safe_dump([tool]),
            "function_groups": [{
                "id": "old_group", "name": "Allowed group",
                "description": "Allowed Guest functions", "loading_mode": "always",
                "functions": ["old_tool"], "guest_allowed": True,
            }],
            "guest_function_policy": "custom",
            "guest_allowed_function_names": ["old_tool"] if rename_kind == "function" else [],
            "guest_allowed_group_ids": ["old_group"] if rename_kind == "group" else [],
            CONF_GUEST_POLICY_VERSION: GUEST_POLICY_VERSION,
        },
    )
    await _setup_entry(hass, entry)

    class BrowserClient:
        client = None

        async def send_json_auto_id(self, message):
            if self.client is None:
                self.client = await _admin_client(
                    hass, hass_ws_client, user_id="browser-guest-rename-admin"
                )
            await self.client.send_json_auto_id(message)

        async def receive_json(self):
            return await self.client.receive_json()

    runner, backend_url = await _start_ws_bridge(BrowserClient())
    try:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parent.parent,
            spec="tests_browser/real-ha-configuration-acknowledgement.spec.mjs",
            config="playwright.config.mjs",
            env={"REAL_HA_BACKEND_URL": backend_url, "CONFIGURATION_ACK_GUEST_RENAME": rename_kind,
                 "CONFIGURATION_ACK_DIRTY_DRAFT": "1" if dirty_draft else "0"},
            failure_label="Partial Function acknowledgement retained stale Guest permissions",
        )
    finally:
        await runner.cleanup()


@pytest.fixture
async def real_ha_shell(
    hass: HomeAssistant,
    aiohttp_client: Any,
    hass_storage: dict[str, Any],
    socket_enabled: Any,
) -> dict[str, Any]:
    """HA itself must register, serve, instantiate, and connect the shipped panel."""
    # A pristine pytest HA instance is in onboarding mode. Persist the normal
    # completed-onboarding state so Chromium reaches the actual application shell.
    hass_storage[onboarding.STORAGE_KEY] = {
        "version": onboarding.STORAGE_VERSION,
        "data": {"done": list(onboarding.STEPS)},
    }

    assert await async_setup_component(hass, "websocket_api", {})
    assert await async_setup_component(hass, "frontend", {})

    entry = _entry("Browser Frontend Shell Acceptance")
    await _setup_entry(hass, entry)

    registry = er.async_get(hass)
    for room in ("kitchen", "hall"):
        entity = registry.async_get_or_create(
            "sensor",
            "followup_fixture",
            f"cold-attribute-{room}",
            suggested_object_id=f"cold_attribute_{room}",
        )
        hass.states.async_set(entity.entity_id, "21", {"battery_level": 84})
        async_expose_entity(hass, conversation.DOMAIN, entity.entity_id, True)

    # A genuine satellite with two candidates per domain exercises both native
    # Quiet Hours selectors on a direct, fresh-document route visit.
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("cold_quiet_fixture", "satellite")},
        name="Cold Quiet Hours satellite",
    )
    for domain, object_id, label in (
        ("assist_satellite", "cold_quiet", "Cold Quiet Hours satellite"),
        ("media_player", "cold_quiet_a", "Cold speaker A"),
        ("media_player", "cold_quiet_b", "Cold speaker B"),
        ("switch", "cold_quiet_wake_a", "Cold wake sound A"),
        ("switch", "cold_quiet_wake_b", "Cold wake sound B"),
    ):
        entity = registry.async_get_or_create(
            domain, "cold_quiet_fixture", object_id,
            suggested_object_id=object_id, original_name=label,
            device_id=device.id, config_entry=entry,
        )
        attributes = {"friendly_name": label}
        if domain == "media_player":
            attributes["volume_level"] = 0.8
        hass.states.async_set(entity.entity_id, "on" if domain == "switch" else "idle", attributes)

    admin = MockUser(
        id="browser-frontend-shell-admin",
        name="Browser Frontend Shell Admin",
        is_owner=True,
    )
    admin.add_to_hass(hass)
    picker_user = await hass.auth.async_create_user("Native picker acceptance user")
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

    return {"entry": entry, "admin": admin, "http_client": client, "env": {
        "REAL_HA_FRONTEND_URL": base_url,
        "REAL_HA_FRONTEND_AUTH": json.dumps(auth_data),
        "REAL_HA_PICKER_USER_NAME": picker_user.name,
        "REAL_HA_SMOKE_USER_ID": picker_user.id,
    }}


@pytest.mark.asyncio
async def test_shipped_browser_frontend_loads_inside_real_home_assistant_shell(real_ha_shell) -> None:
    """Reuse the same genuine shell bootstrap for acceptance and compact journeys."""
    await _run_playwright(
        repo_root=Path(__file__).resolve().parent.parent,
        spec="tests_browser/real-ha-shell.spec.mjs",
        config="playwright.real-ha-shell.config.mjs",
        env=real_ha_shell["env"],
        failure_label="Playwright genuine Home Assistant frontend-shell acceptance failed",
    )


@pytest.mark.asyncio
async def test_shipped_frontend_treats_hostile_knowledge_and_memory_as_text(
    real_ha_shell,
) -> None:
    """Run hostile managed-content rendering through native HA authentication."""
    await _run_playwright(
        repo_root=Path(__file__).resolve().parent.parent,
        spec="tests_browser/real-ha-adversarial-content.spec.mjs",
        config="playwright.real-ha-shell.config.mjs",
        env=real_ha_shell["env"],
        failure_label="Genuine HA hostile-content rendering failed",
    )


@pytest.mark.parametrize("operation,mutate", [("guest",False),("guest",True),("repair",False),("repair",True)])
async def test_sequential_shared_draft_is_preserved_and_savable(hass, hass_ws_client, operation, mutate):
    if operation == "repair":
        valid = {"spec":{"name":"audit_valid_sibling", "description":"Unchanged sibling", "parameters":{"type":"object", "properties":{}}}, "function":{"type":"native", "name":"get_user_from_user_id"}, "enabled":False}
        invalid = {"spec":{"name":"audit_invalid", "description":"Needs repair", "parameters":{"type":"object", "properties":{}, "description":123}}, "function":{"type":"native", "name":"get_user_from_user_id"}}
        entry = _make_entry("Sequential repair draft audit", include_ai_task=False,
            conversation_options={CONF_FUNCTION_TOOLS:yaml.safe_dump([valid, invalid])})
    else:
        entry = _make_entry("Sequential Guest draft audit", include_ai_task=False,
            conversation_options={CONF_GUEST_POLICY_VERSION:GUEST_POLICY_VERSION})
    await _setup_entry(hass, entry)

    class BrowserClient:
        client = None
        async def send_json_auto_id(self, message):
            if self.client is None:
                self.client = await _admin_client(hass, hass_ws_client, user_id=f"audit-{operation}-admin")
            await self.client.send_json_auto_id(message)
        async def receive_json(self):
            return await self.client.receive_json()

    runner, backend_url = await _start_ws_bridge(BrowserClient())
    with socket.socket() as audit_socket:
        audit_socket.bind(("127.0.0.1", 0))
        browser_port = str(audit_socket.getsockname()[1])
    try:
        await _run_playwright(repo_root=Path(__file__).parents[1],
            spec="tests_browser/real-ha-sequential-drafts.spec.mjs",
            config="playwright.config.mjs",
            env={"REAL_HA_BACKEND_URL":backend_url, "AUDIT_OPERATION":operation,
                "AUDIT_MUTATE":"1" if mutate else "0",
                "PLAYWRIGHT_PORT":browser_port, "SHIPPED_BUNDLE":"1"},
            failure_label=f"Public sequential {operation} operation violated draft invariant")
    finally:
        await runner.cleanup()
        await hass.async_block_till_done()
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["configuration_rules", "rule_pack", "usage"])
async def test_frontend_mutation_effects_preserve_real_revisions_and_projections(
    hass: HomeAssistant, hass_ws_client: Any, scenario: str
) -> None:
    """The shipped browser uses actual HA acknowledgements and refreshed projections."""
    entry = _make_entry(
        "Browser Mutation Effects",
        include_ai_task=False,
        conversation_options={
            CONF_FUNCTION_TOOLS: [],
            "prompt": "Initial mutation prompt",
        },
    )
    await _setup_entry(hass, entry)
    if scenario == "usage":
        from custom_components.extended_openai_conversation_responses.usage import (
            RequestUsage,
            async_get_usage,
        )

        subentry = next(iter(entry.subentries.values()))
        manager = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)
        async with manager.async_run():
            await manager.async_record_request(
                successful=True,
                usage=RequestUsage(input_tokens=12, output_tokens=5, total_tokens=17),
                provider="openai",
                model="gpt-5-mini",
                api_mode="responses",
            )

    class BrowserClient:
        client = None

        async def send_json_auto_id(self, message):
            if self.client is None:
                self.client = await _admin_client(
                    hass, hass_ws_client, user_id="browser-mutation-effects-admin"
                )
            await self.client.send_json_auto_id(message)

        async def receive_json(self):
            return await self.client.receive_json()

    runner, backend_url = await _start_ws_bridge(BrowserClient())
    try:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parent.parent,
            spec="tests_browser/real-ha-mutation-effects.spec.mjs",
            config="playwright.config.mjs",
            env={
                "REAL_HA_BACKEND_URL": backend_url,
                "MUTATION_EFFECTS_SCENARIO": scenario,
            },
            failure_label="Frontend mutation effects diverged from genuine HA state",
        )
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_frontend_usability_transactions_in_native_ha(real_ha_shell, hass):
    """Use real validation, group dependencies, persisted retention and native UI."""
    from custom_components.extended_openai_conversation_responses.usage import (
        RequestUsage,
        async_get_usage,
    )

    entry = real_ha_shell["entry"]
    subentry = next(iter(entry.subentries.values()))
    manager = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)
    async with manager.async_run():
        await manager.async_record_request(
            successful=True,
            usage=RequestUsage(input_tokens=12, output_tokens=5, total_tokens=17),
            provider="openai",
            model="gpt-5-mini",
            api_mode="responses",
        )
    await _run_playwright(
        repo_root=Path(__file__).resolve().parent.parent,
        spec="tests_browser/real-ha-usability.spec.mjs",
        config="playwright.real-ha-shell.config.mjs",
        env=real_ha_shell["env"],
        failure_label="Native HA usability transactions failed",
    )
