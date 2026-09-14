"""Browser-visible acceptance for upgrading a published release to the candidate."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from aiohttp import web
import pytest

from tests_real_ha.test_release_upgrade_acceptance import (
    DOMAIN,
    _STATE_FILE as UPGRADE_STATE_FILE,
    _assert_child_ok as assert_upgrade_child_ok,
    _conversation_subentry as conversation_subentry,
    _exercise_public_conversation as exercise_public_conversation,
    _install_component as install_component,
    _manifest as manifest,
    _run_child as run_upgrade_child,
)

_CHILD_PHASE_ENV = "UPGRADE_BROWSER_CHILD_PHASE"
_CONFIG_DIR_ENV = "UPGRADE_BROWSER_CONFIG_DIR"
_SAVED_TITLE = "Upgrade Acceptance Agent - Browser Saved"

pytestmark = pytest.mark.skipif(
    not os.environ.get("UPGRADE_FROM_COMPONENT_DIR")
    or not os.environ.get("UPGRADE_TO_COMPONENT_DIR")
    or os.environ.get("RUN_UPGRADE_BROWSER") != "1",
    reason="requires released/candidate payloads and browser-visible upgrade acceptance",
)


def _run_child(config_dir: Path, phase: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env[_CHILD_PHASE_ENV] = phase
    env[_CONFIG_DIR_ENV] = str(config_dir)
    repo_root = Path(__file__).resolve().parent.parent
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        os.pathsep.join((str(repo_root), existing_pythonpath))
        if existing_pythonpath
        else str(repo_root)
    )
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=config_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )


def _assert_child_ok(result: subprocess.CompletedProcess[str], phase: str) -> None:
    assert result.returncode == 0, (
        f"browser upgrade phase {phase!r} failed\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )


async def _start_management_bridge(hass: Any, user_id: str) -> tuple[web.AppRunner, str]:
    """Expose the candidate's real management command to the shipped browser fixture."""
    from custom_components.extended_openai_conversation_responses.management_ui import (
        async_management_command,
    )
    from homeassistant.exceptions import HomeAssistantError

    async def call_management(request: web.Request) -> web.Response:
        headers = {"Access-Control-Allow-Origin": "*"}
        try:
            message = json.loads(await request.text())
            if not isinstance(message, dict):
                raise HomeAssistantError("Management message must be an object")
            result = await async_management_command(hass, user_id, True, message)
        except (json.JSONDecodeError, HomeAssistantError, ValueError) as err:
            return web.json_response({"message": str(err)}, status=400, headers=headers)
        return web.json_response(result, headers=headers)

    app = web.Application()
    app.router.add_post("/callws", call_management)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = getattr(site._server, "sockets", None)  # noqa: SLF001 - test socket
    assert sockets, "browser upgrade management bridge did not bind"
    port = sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/callws"


async def _run_playwright(backend_url: str, expected_title: str, model: str) -> None:
    npx = shutil.which("npx")
    assert npx is not None, "npx is required for browser-visible upgrade acceptance"
    repo_root = Path(__file__).resolve().parent.parent
    env = {
        **os.environ,
        "CI": "1",
        "REAL_HA_BACKEND_URL": backend_url,
        "UPGRADE_BROWSER_EXPECTED_TITLE": expected_title,
        "UPGRADE_BROWSER_EXPECTED_MODEL": model,
        "UPGRADE_BROWSER_SAVED_TITLE": _SAVED_TITLE,
    }
    process = await asyncio.create_subprocess_exec(
        npx,
        "playwright",
        "test",
        "tests_browser/release-upgrade-visible.spec.mjs",
        "--config=playwright.config.mjs",
        cwd=repo_root,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    async with asyncio.timeout(150):
        stdout, _ = await process.communicate()
    output = stdout.decode("utf-8", errors="replace")
    assert process.returncode == 0, f"browser-visible upgrade Playwright failed:\n{output}"


async def _browser_candidate_phase(hass: Any, config_dir: Path) -> None:
    """Render release-created state in the candidate UI and save through it."""
    from custom_components.extended_openai_conversation_responses import const
    from homeassistant.config_entries import ConfigEntryState

    state = json.loads((config_dir / UPGRADE_STATE_FILE).read_text(encoding="utf-8"))
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    entry = entries[0]
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.entry_id == state["entry_id"]
    assert entry.version == const.CONFIG_ENTRY_VERSION

    subentry = conversation_subentry(entry)
    assert subentry.subentry_id == state["subentry_id"]
    assert subentry.title == state["title"]
    for key, value in state["custom_values"].items():
        assert subentry.data.get(key) == value

    model = str(subentry.data[const.CONF_CHAT_MODEL])
    user = await hass.auth.async_create_user("Browser Upgrade Acceptance Admin")
    runner, backend_url = await _start_management_bridge(hass, user.id)
    try:
        await _run_playwright(backend_url, subentry.title, model)
    finally:
        await runner.cleanup()

    await hass.async_block_till_done()
    current_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert current_entry is not None
    current = conversation_subentry(current_entry)
    assert current.subentry_id == state["subentry_id"]
    assert current.title == _SAVED_TITLE
    assert current.data[const.CONF_CHAT_MODEL] == model

    state["browser_saved_title"] = current.title
    state["browser_saved_model"] = model
    state["candidate_entry_version"] = current_entry.version
    (config_dir / UPGRADE_STATE_FILE).write_text(json.dumps(state), encoding="utf-8")


async def _browser_restart_phase(hass: Any, config_dir: Path) -> None:
    """Prove the browser-written migrated configuration survives a cold restart."""
    from custom_components.extended_openai_conversation_responses import const
    from homeassistant.config_entries import ConfigEntryState

    state = json.loads((config_dir / UPGRADE_STATE_FILE).read_text(encoding="utf-8"))
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    entry = entries[0]
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.entry_id == state["entry_id"]
    assert entry.version == const.CONFIG_ENTRY_VERSION == state["candidate_entry_version"]

    subentry = conversation_subentry(entry)
    assert subentry.subentry_id == state["subentry_id"]
    assert subentry.title == state["browser_saved_title"] == _SAVED_TITLE
    assert subentry.data[const.CONF_CHAT_MODEL] == state["browser_saved_model"]

    await exercise_public_conversation(
        hass, entry.entry_id, "Browser-upgraded state survived a cold restart."
    )


async def _child_main() -> None:
    from homeassistant import bootstrap, runner

    config_dir = Path(os.environ[_CONFIG_DIR_ENV]).resolve()
    phase = os.environ[_CHILD_PHASE_ENV]
    sys.path.insert(0, str(config_dir))

    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=False)
    )
    assert hass is not None
    await hass.async_start()
    try:
        if phase == "candidate-browser":
            await _browser_candidate_phase(hass, config_dir)
        elif phase == "candidate-restart":
            await _browser_restart_phase(hass, config_dir)
        else:
            raise AssertionError(f"Unknown browser upgrade phase: {phase}")
        await hass.async_block_till_done()
    finally:
        await hass.async_stop()


def test_published_release_upgrade_is_visible_and_editable_in_candidate_browser(
    tmp_path: Path,
) -> None:
    """The candidate UI must render, edit, and persist release-created state."""
    from_component = Path(os.environ["UPGRADE_FROM_COMPONENT_DIR"]).resolve()
    to_component = Path(os.environ["UPGRADE_TO_COMPONENT_DIR"]).resolve()

    from_manifest = manifest(from_component)
    to_manifest = manifest(to_component)
    assert from_manifest["domain"] == DOMAIN
    assert to_manifest["domain"] == DOMAIN
    expected_from = os.environ.get("UPGRADE_FROM_VERSION")
    expected_to = os.environ.get("UPGRADE_TO_VERSION")
    if expected_from:
        assert from_manifest["version"] == expected_from
    if expected_to:
        assert to_manifest["version"] == expected_to

    config_dir = tmp_path / "ha-config"
    config_dir.mkdir()
    (config_dir / "configuration.yaml").write_text(
        "homeassistant:\n  name: Browser Release Upgrade Acceptance\n",
        encoding="utf-8",
    )

    install_component(from_component, config_dir)
    released = run_upgrade_child(config_dir, "released")
    assert_upgrade_child_ok(released, "released")
    assert (config_dir / ".storage" / "core.config_entries").exists()
    assert (config_dir / UPGRADE_STATE_FILE).exists()

    install_component(to_component, config_dir)
    browser = _run_child(config_dir, "candidate-browser")
    _assert_child_ok(browser, "candidate-browser")

    restarted = _run_child(config_dir, "candidate-restart")
    _assert_child_ok(restarted, "candidate-restart")


if __name__ == "__main__" and os.environ.get(_CHILD_PHASE_ENV):
    asyncio.run(_child_main())
