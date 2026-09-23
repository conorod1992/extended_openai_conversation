"""Keep Chromium open while a genuine Home Assistant process dies and returns."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

DOMAIN = "extended_openai_conversation_responses"
_CHILD_ENV = "REAL_HA_PROCESS_RESTART_CHILD"
_CONFIG_DIR_ENV = "REAL_HA_PROCESS_RESTART_CONFIG_DIR"
_PORT_ENV = "REAL_HA_PROCESS_RESTART_PORT"
_GENERATION_ENV = "REAL_HA_PROCESS_RESTART_GENERATION"
_SYNC_DIR_ENV = "REAL_HA_PROCESS_RESTART_SYNC_DIR"
_AUTH_FILE = "browser-auth.json"

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_HA_BROWSER") != "1",
    reason="enabled only by the browser-to-genuine-HA acceptance job",
)


def _free_port() -> int:
    """Reserve and release a loopback port for the child HA process."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_for_process_marker(
    process: asyncio.subprocess.Process,
    path: Path,
    *,
    timeout: float = 60,
) -> None:
    """Wait for a marker while also failing promptly if its child exits."""
    async with asyncio.timeout(timeout):
        while not path.exists():
            if process.returncode is not None:
                raise AssertionError(
                    f"Home Assistant child exited before creating {path.name}: "
                    f"return code {process.returncode}"
                )
            await asyncio.sleep(0.1)


async def _port_open(port: int) -> bool:
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
    except OSError:
        return False
    writer.close()
    await writer.wait_closed()
    del reader
    return True


async def _wait_for_port(port: int, *, available: bool, timeout: float = 60) -> None:
    """Wait for the real HA TCP listener to appear or disappear."""
    async with asyncio.timeout(timeout):
        while await _port_open(port) is not available:
            await asyncio.sleep(0.1)


def _contains_value(value: Any, expected: str) -> bool:
    """Find an exact durable ID/token in HA's versioned storage envelope."""
    if isinstance(value, dict):
        return any(_contains_value(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_contains_value(item, expected) for item in value)
    return value == expected


async def _wait_for_stored_values(path: Path, *values: str) -> None:
    """Wait for a complete atomic Store generation containing required values."""
    async with asyncio.timeout(15):
        while True:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if all(_contains_value(payload, value) for value in values):
                    return
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            await asyncio.sleep(0.05)


async def _start_ha_child(
    *,
    test_file: Path,
    config_dir: Path,
    sync_dir: Path,
    port: int,
    generation: int,
    log_file: Path,
) -> tuple[asyncio.subprocess.Process, Any]:
    """Start one independent Home Assistant Python process."""
    log_handle = log_file.open("wb")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(test_file),
        cwd=config_dir,
        env={
            **os.environ,
            _CHILD_ENV: "1",
            _CONFIG_DIR_ENV: str(config_dir),
            _PORT_ENV: str(port),
            _GENERATION_ENV: str(generation),
            _SYNC_DIR_ENV: str(sync_dir),
        },
        stdout=log_handle,
        stderr=asyncio.subprocess.STDOUT,
    )
    return process, log_handle


async def _stop_child(
    process: asyncio.subprocess.Process,
    log_handle: Any,
    *,
    kill: bool,
) -> None:
    """Stop a child process and close its log handle."""
    if process.returncode is None:
        if kill:
            process.kill()
        else:
            process.terminate()
        try:
            async with asyncio.timeout(15):
                await process.wait()
        except TimeoutError:
            process.kill()
            await process.wait()
    log_handle.close()


def _read_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "<no child log>"


async def _run_playwright(
    *,
    repo_root: Path,
    base_url: str,
    auth_data: dict[str, Any],
    sync_dir: Path,
) -> asyncio.subprocess.Process:
    """Launch the one Playwright process that remains alive across HA restart."""
    npx = shutil.which("npx")
    assert npx is not None, "npx is required for genuine HA browser acceptance"
    return await asyncio.create_subprocess_exec(
        npx,
        "playwright",
        "test",
        "tests_browser/real-ha-process-restart.spec.mjs",
        "--config=playwright.real-ha-process-restart.config.mjs",
        cwd=repo_root,
        env={
            **os.environ,
            "CI": "1",
            "REAL_HA_FRONTEND_URL": base_url,
            "REAL_HA_FRONTEND_AUTH": json.dumps(auth_data),
            "REAL_HA_RESTART_SYNC_DIR": str(sync_dir),
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )


def _write_onboarding_store(config_dir: Path) -> None:
    """Persist completed onboarding so Chromium reaches the HA application shell."""
    from homeassistant.components import onboarding

    storage_dir = config_dir / ".storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": onboarding.STORAGE_VERSION,
        "minor_version": 1,
        "key": onboarding.STORAGE_KEY,
        "data": {"done": list(onboarding.STEPS)},
    }
    (storage_dir / onboarding.STORAGE_KEY).write_text(
        json.dumps(payload), encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_open_browser_survives_true_home_assistant_process_restart(
    tmp_path: Path,
    socket_enabled: Any,
) -> None:
    """SIGKILL HA, restart it, and require the same Chromium document to recover."""
    repo_root = Path(__file__).resolve().parent.parent
    test_file = Path(__file__).resolve()
    source_component = repo_root / "custom_components" / DOMAIN

    config_dir = tmp_path / "ha-config"
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    shutil.copytree(source_component, destination)

    port = _free_port()
    (config_dir / "configuration.yaml").write_text(
        "\n".join(
            [
                "homeassistant:",
                "  name: Browser Process Restart Acceptance",
                "http:",
                "  server_host: 127.0.0.1",
                f"  server_port: {port}",
                "frontend:",
                "websocket_api:",
                "api:",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_onboarding_store(config_dir)

    sync_dir = tmp_path / "restart-sync"
    sync_dir.mkdir()
    first_log = tmp_path / "ha-first.log"
    second_log = tmp_path / "ha-second.log"

    first, first_handle = await _start_ha_child(
        test_file=test_file,
        config_dir=config_dir,
        sync_dir=sync_dir,
        port=port,
        generation=1,
        log_file=first_log,
    )
    second: asyncio.subprocess.Process | None = None
    second_handle: Any | None = None
    browser: asyncio.subprocess.Process | None = None
    try:
        try:
            await _wait_for_process_marker(first, sync_dir / "ha-ready-1")
            await _wait_for_port(port, available=True)
        except Exception as err:
            raise AssertionError(
                f"first Home Assistant process failed to become ready:\n{_read_log(first_log)}"
            ) from err

        auth_data = json.loads((sync_dir / _AUTH_FILE).read_text(encoding="utf-8"))
        base_url = f"http://127.0.0.1:{port}"
        browser = await _run_playwright(
            repo_root=repo_root,
            base_url=base_url,
            auth_data=auth_data,
            sync_dir=sync_dir,
        )
        # Playwright owns a 120-second test timeout. The parent must not kill a
        # still-valid browser journey halfway through that diagnostic window.
        await _wait_for_process_marker(browser, sync_dir / "browser-ready", timeout=130)

        # The browser save must be durable before SIGKILL. Waiting for the exact
        # subentry title keeps the crash boundary after HA's atomic Store commit.
        await _wait_for_stored_values(
            config_dir / ".storage" / "core.config_entries",
            "Before real HA restart",
        )

        # Hard-kill the HA process rather than invoking hass.async_stop(), unloading
        # the config entry, or restarting only the integration.
        await _stop_child(first, first_handle, kill=True)
        await _wait_for_port(port, available=False, timeout=20)
        (sync_dir / "ha-dead").write_text("dead\n", encoding="utf-8")

        # Do not start replacement HA until Chromium has itself confirmed that the
        # origin is unreachable while its existing document remains open.
        await _wait_for_process_marker(
            browser,
            sync_dir / "browser-confirmed-offline",
            timeout=30,
        )

        second, second_handle = await _start_ha_child(
            test_file=test_file,
            config_dir=config_dir,
            sync_dir=sync_dir,
            port=port,
            generation=2,
            log_file=second_log,
        )
        try:
            await _wait_for_process_marker(second, sync_dir / "ha-ready-2")
            await _wait_for_port(port, available=True)
        except Exception as err:
            raise AssertionError(
                f"restarted Home Assistant process failed to become ready:\n{_read_log(second_log)}"
            ) from err

        (sync_dir / "ha-restarted").write_text("restarted\n", encoding="utf-8")

        # Playwright itself has a 120-second ceiling for this deliberately heavy
        # process-boundary journey. Keep the parent guard above that so pytest does
        # not kill a still-valid browser/HA pair before Playwright can report its
        # own success or diagnostic failure.
        async with asyncio.timeout(150):
            stdout, _ = await browser.communicate()
        output = stdout.decode("utf-8", errors="replace")
        assert browser.returncode == 0, (
            "Playwright did not recover after the real HA process restart:\n" + output
        )
        assert (sync_dir / "browser-complete").exists()
    finally:
        if browser is not None and browser.returncode is None:
            browser.kill()
            await browser.wait()
        if first.returncode is None:
            await _stop_child(first, first_handle, kill=True)
        elif not first_handle.closed:
            first_handle.close()
        if second is not None and second_handle is not None:
            await _stop_child(second, second_handle, kill=False)


async def _ensure_entry(hass: Any) -> Any:
    """Create the persisted integration entry on first boot, reuse it thereafter."""
    from homeassistant.config_entries import ConfigEntryState, SOURCE_USER
    from homeassistant.const import CONF_API_KEY, CONF_NAME
    from homeassistant.data_entry_flow import FlowResultType

    entries = hass.config_entries.async_entries(DOMAIN)
    if entries:
        assert len(entries) == 1
        entry = entries[0]
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        return entry

    config_flow = __import__(
        f"custom_components.{DOMAIN}.config_flow", fromlist=["config_flow"]
    )
    const = __import__(f"custom_components.{DOMAIN}.const", fromlist=["const"])
    authenticate = AsyncMock(return_value=object())
    with patch.object(config_flow, "get_authenticated_client", authenticate):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Browser Process Restart Acceptance",
                CONF_API_KEY: "sk-browser-process-restart",
                const.CONF_BASE_URL: const.DEFAULT_CONF_BASE_URL,
                const.CONF_SKIP_AUTHENTICATION: True,
                const.CONF_API_PROVIDER: "openai",
            },
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def _ensure_browser_auth(hass: Any, sync_dir: Path, base_url: str) -> None:
    """Create one persisted owner refresh token and expose its browser token shape."""
    auth_path = sync_dir / _AUTH_FILE
    if auth_path.exists():
        return

    from homeassistant.auth.const import GROUP_ID_ADMIN
    from pytest_homeassistant_custom_component.common import CLIENT_ID

    user = await hass.auth.async_create_user(
        "Browser Process Restart Admin", group_ids=[GROUP_ID_ADMIN]
    )
    refresh_token = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    access_token = hass.auth.async_create_access_token(refresh_token)
    expires_in = int(refresh_token.access_token_expiration.total_seconds())
    auth_path.write_text(
        json.dumps(
            {
                "hassUrl": base_url,
                "clientId": CLIENT_ID,
                "expires": int(time.time() * 1000) + expires_in * 1000,
                "refresh_token": refresh_token.token,
                "access_token": access_token,
                "expires_in": expires_in,
            }
        ),
        encoding="utf-8",
    )


async def _child_main() -> None:
    """Run one real Home Assistant process until the parent kills/terminates it."""
    from homeassistant import bootstrap, runner
    from homeassistant.helpers import recorder as recorder_helper

    config_dir = Path(os.environ[_CONFIG_DIR_ENV]).resolve()
    port = int(os.environ[_PORT_ENV])
    generation = int(os.environ[_GENERATION_ENV])
    sync_dir = Path(os.environ[_SYNC_DIR_ENV]).resolve()

    # Force custom integration discovery from the staged HA config directory.
    sys.path.insert(0, str(config_dir))
    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=False)
    )
    assert hass is not None

    # The normal HA runner initializes recorder's shared data before recorder setup.
    # This child calls bootstrap.async_setup_hass() directly, so mirror that runner
    # contract before loading our integration and its recorder/history dependencies.
    if recorder_helper.DATA_RECORDER not in hass.data:
        recorder_helper.async_initialize_recorder(hass)

    await hass.async_start()

    entry = await _ensure_entry(hass)
    base_url = f"http://127.0.0.1:{port}"
    await _ensure_browser_auth(hass, sync_dir, base_url)

    if generation == 2:
        conversation_titles = {
            subentry.title
            for subentry in entry.subentries.values()
            if subentry.subentry_type == "conversation"
        }
        assert "Before real HA restart" in conversation_titles
        auth_data = json.loads((sync_dir / _AUTH_FILE).read_text(encoding="utf-8"))
        assert hass.auth.async_validate_access_token(auth_data["access_token"]) is not None

    # HA's delayed writes must contain this actual entry and refresh token before
    # the parent may SIGKILL the first generation.
    await hass.async_block_till_done()
    auth_data = json.loads((sync_dir / _AUTH_FILE).read_text(encoding="utf-8"))
    await _wait_for_stored_values(
        config_dir / ".storage" / "auth", auth_data["refresh_token"]
    )
    await _wait_for_stored_values(
        config_dir / ".storage" / "core.config_entries", entry.entry_id
    )

    (sync_dir / f"ha-ready-{generation}").write_text("ready\n", encoding="utf-8")
    await asyncio.Event().wait()


if __name__ == "__main__" and os.environ.get(_CHILD_ENV) == "1":
    asyncio.run(_child_main())
