"""Acceptance for released frontend JavaScript talking to the candidate backend."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
from typing import Any

from aiohttp import web
import pytest

from tests_real_ha.test_browser_release_upgrade_acceptance import _model_catalog_result
from tests_real_ha.test_cross_feature_acceptance import _agent

DOMAIN = "extended_openai_conversation_responses"
_OLD_FRONTEND_ENV = "VERSION_SKEW_OLD_FRONTEND_DIR"
_OLD_VERSION_ENV = "VERSION_SKEW_OLD_VERSION"
_RUN_ENV = "RUN_FRONTEND_BACKEND_SKEW"
_AUTHORITATIVE_TITLE = "Candidate authoritative title"
_STALE_TITLE = "Old tab stale title"

pytestmark = pytest.mark.skipif(
    not os.environ.get(_OLD_FRONTEND_ENV) or os.environ.get(_RUN_ENV) != "1",
    reason="requires a genuine released frontend payload",
)


class _SkewBridge:
    """Expose candidate Management calls and one controllable stale old-tab write."""

    def __init__(self, hass: Any, user_id: str) -> None:
        self.hass = hass
        self.user_id = user_id
        self.old_save_waiting = asyncio.Event()
        self.release_old_save = asyncio.Event()
        self.old_save_message: dict[str, Any] | None = None
        self.runner: web.AppRunner | None = None
        self.backend_url = ""
        self.control_url = ""

    async def start(self) -> None:
        from custom_components.extended_openai_conversation_responses.management_ui import (
            async_management_command,
        )
        from custom_components.extended_openai_conversation_responses.model_catalog_manager import (
            WS_CATALOG,
        )
        from homeassistant.exceptions import HomeAssistantError

        headers = {"Access-Control-Allow-Origin": "*"}

        async def call_management(request: web.Request) -> web.Response:
            try:
                message = json.loads(await request.text())
                if not isinstance(message, dict):
                    raise HomeAssistantError("Management message must be an object")

                client = request.query.get("client", "default")
                if (
                    client == "old"
                    and message.get("section") == "configuration"
                    and message.get("action") in {"save", "update"}
                ):
                    self.old_save_message = dict(message)
                    self.old_save_waiting.set()
                    async with asyncio.timeout(30):
                        await self.release_old_save.wait()

                if message.get("type") == WS_CATALOG:
                    result = await _model_catalog_result(self.hass, message)
                else:
                    result = await async_management_command(
                        self.hass,
                        self.user_id,
                        True,
                        message,
                    )
            except (json.JSONDecodeError, HomeAssistantError, ValueError) as err:
                return web.json_response(
                    {"message": str(err)},
                    status=400,
                    headers=headers,
                )
            return web.json_response(result, headers=headers)

        async def status(_request: web.Request) -> web.Response:
            return web.json_response(
                {
                    "old_save_waiting": self.old_save_waiting.is_set(),
                    "old_save_released": self.release_old_save.is_set(),
                },
                headers=headers,
            )

        async def release(_request: web.Request) -> web.Response:
            self.release_old_save.set()
            return web.json_response({"released": True}, headers=headers)

        app = web.Application()
        app.router.add_post("/callws", call_management)
        app.router.add_get("/control/status", status)
        app.router.add_post("/control/release", release)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        sockets = getattr(site._server, "sockets", None)
        assert sockets
        port = sockets[0].getsockname()[1]
        self.backend_url = f"http://127.0.0.1:{port}/callws"
        self.control_url = f"http://127.0.0.1:{port}/control"

    async def close(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()


async def _run_playwright(
    *,
    old_frontend_dir: Path,
    backend_url: str,
    control_url: str,
    expected_title: str,
    expected_model: str,
) -> None:
    npx = shutil.which("npx")
    assert npx is not None, "npx is required for frontend/backend skew acceptance"
    repo_root = Path(__file__).resolve().parents[1]
    served = (
        repo_root
        / "tests_browser"
        / f".frontend-skew-release-{os.getpid()}"
    )
    if served.exists():
        shutil.rmtree(served)
    shutil.copytree(old_frontend_dir, served)
    frontend_root = "/" + str(served.relative_to(repo_root)).replace(os.sep, "/")

    env = {
        **os.environ,
        "CI": "1",
        "VERSION_SKEW_BACKEND_URL": backend_url,
        "VERSION_SKEW_CONTROL_URL": control_url,
        "VERSION_SKEW_OLD_FRONTEND_ROOT": frontend_root,
        "VERSION_SKEW_EXPECTED_TITLE": expected_title,
        "VERSION_SKEW_EXPECTED_MODEL": expected_model,
        "VERSION_SKEW_AUTHORITATIVE_TITLE": _AUTHORITATIVE_TITLE,
        "VERSION_SKEW_STALE_TITLE": _STALE_TITLE,
    }
    process = await asyncio.create_subprocess_exec(
        npx,
        "playwright",
        "test",
        "tests_browser/frontend-backend-version-skew.spec.mjs",
        "--config=playwright.config.mjs",
        cwd=repo_root,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        async with asyncio.timeout(150):
            stdout, _ = await process.communicate()
        output = stdout.decode("utf-8", errors="replace")
        assert process.returncode == 0, (
            "frontend/backend version-skew Playwright failed:\n" + output
        )
    finally:
        shutil.rmtree(served, ignore_errors=True)


async def test_released_frontend_cannot_overwrite_newer_candidate_state(
    hass: Any,
    socket_enabled: Any,
) -> None:
    """Keep an old tab alive while a new tab saves against the candidate backend."""
    del socket_enabled
    old_frontend = Path(os.environ[_OLD_FRONTEND_ENV]).resolve()
    assert (old_frontend / "management-panel.js").is_file()

    agent = await _agent(hass, title="Version Skew Agent")
    entry_id = agent.entry.entry_id
    subentry_id = agent.subentry.subentry_id
    model = str(agent.subentry.data["chat_model"])

    from homeassistant.auth.const import GROUP_ID_ADMIN

    user = await hass.auth.async_create_user(
        "Frontend Backend Skew Admin",
        group_ids=[GROUP_ID_ADMIN],
    )
    bridge = _SkewBridge(hass, user.id)
    await bridge.start()
    try:
        await _run_playwright(
            old_frontend_dir=old_frontend,
            backend_url=bridge.backend_url,
            control_url=bridge.control_url,
            expected_title=agent.subentry.title,
            expected_model=model,
        )
    finally:
        await bridge.close()

    assert bridge.old_save_waiting.is_set()
    assert bridge.old_save_message is not None
    # The released 6.8.3 frontend predates revision-aware configuration saves.
    assert "revision" not in bridge.old_save_message

    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    subentry = entry.subentries[subentry_id]
    assert subentry.title == _AUTHORITATIVE_TITLE
    assert subentry.title != _STALE_TITLE


def test_skew_fixture_uses_the_requested_released_frontend() -> None:
    """The workflow must supply actual released assets, not a candidate imitation."""
    old_frontend = Path(os.environ[_OLD_FRONTEND_ENV]).resolve()
    version_value = os.environ.get(_OLD_VERSION_ENV)
    assert version_value
    assert old_frontend.name == "frontend"
    assert (old_frontend / "management-panel.js").is_file()
    # A release-era frontend shape intentionally differs from the candidate bundle.
    assert (old_frontend / "agent-config-editor-base.js").is_file()
