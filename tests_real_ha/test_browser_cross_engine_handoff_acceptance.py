"""True cross-browser persisted-state handoff across one HA backend and restart."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import pytest

from tests_real_ha.test_browser_process_restart_acceptance import (
    _AUTH_FILE,
    _CHILD_ENV,
    _CONFIG_DIR_ENV,
    _GENERATION_ENV,
    _PORT_ENV,
    _SYNC_DIR_ENV,
    _ensure_browser_auth,
    _ensure_entry,
    _free_port,
    _start_ha_child,
    _stop_child,
    _wait_for_port,
    _wait_for_process_marker,
    _write_onboarding_store,
)

DOMAIN = "extended_openai_conversation_responses"

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_HA_CROSS_BROWSER_HANDOFF") != "1",
    reason="dedicated cross-browser persisted-state acceptance only",
)


async def _run_playwright(
    *,
    repo_root: Path,
    base_url: str,
    auth_data: dict[str, Any],
    middle: str,
    phase: str,
) -> None:
    npx = shutil.which("npx")
    assert npx is not None
    process = await asyncio.create_subprocess_exec(
        npx,
        "playwright",
        "test",
        "tests_browser/real-ha-cross-browser-handoff.spec.mjs",
        "--config=playwright.real-ha-cross-browser.config.mjs",
        cwd=repo_root,
        env={
            **os.environ,
            "CI": "1",
            "REAL_HA_FRONTEND_URL": base_url,
            "REAL_HA_FRONTEND_AUTH": json.dumps(auth_data),
            "REAL_HA_CROSS_BROWSER_MIDDLE": middle,
            "REAL_HA_CROSS_BROWSER_PHASE": phase,
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    async with asyncio.timeout(220):
        stdout, _ = await process.communicate()
    output = stdout.decode("utf-8", errors="replace")
    assert process.returncode == 0, (
        f"cross-browser handoff failed for {middle}/{phase}:\n{output}"
    )


@pytest.mark.parametrize("middle", ["firefox", "webkit"])
@pytest.mark.asyncio
async def test_cross_browser_state_handoff_survives_true_ha_restart(
    tmp_path: Path,
    socket_enabled: Any,
    middle: str,
) -> None:
    """Chromium -> other engine -> Chromium state survives a fresh HA process."""
    del socket_enabled
    repo_root = Path(__file__).resolve().parent.parent
    source = repo_root / "custom_components" / DOMAIN
    config_dir = tmp_path / f"ha-cross-browser-{middle}"
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    shutil.copytree(source, destination)

    port = _free_port()
    (config_dir / "configuration.yaml").write_text(
        "\n".join(
            [
                "homeassistant:",
                f"  name: Cross Browser Handoff {middle}",
                "  time_zone: Europe/Dublin",
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
    sync_dir = tmp_path / f"sync-{middle}"
    sync_dir.mkdir()
    first_log = tmp_path / f"ha-{middle}-first.log"
    second_log = tmp_path / f"ha-{middle}-second.log"
    test_file = Path(__file__).resolve()

    first, first_handle = await _start_ha_child(
        test_file=test_file,
        config_dir=config_dir,
        sync_dir=sync_dir,
        port=port,
        generation=1,
        log_file=first_log,
    )
    second = None
    second_handle = None
    try:
        await _wait_for_process_marker(first, sync_dir / "ha-ready-1")
        await _wait_for_port(port, available=True)
        auth = json.loads((sync_dir / _AUTH_FILE).read_text(encoding="utf-8"))
        base_url = f"http://127.0.0.1:{port}"

        await _run_playwright(
            repo_root=repo_root,
            base_url=base_url,
            auth_data=auth,
            middle=middle,
            phase="handoff",
        )

        # Stop the first HA process normally. The next phase uses the exact same
        # config directory, so browser agreement cannot be explained by caches.
        await _stop_child(first, first_handle, kill=False)
        await _wait_for_port(port, available=False, timeout=20)

        second, second_handle = await _start_ha_child(
            test_file=test_file,
            config_dir=config_dir,
            sync_dir=sync_dir,
            port=port,
            generation=2,
            log_file=second_log,
        )
        await _wait_for_process_marker(second, sync_dir / "ha-ready-2")
        await _wait_for_port(port, available=True)

        await _run_playwright(
            repo_root=repo_root,
            base_url=base_url,
            auth_data=auth,
            middle=middle,
            phase="post-restart",
        )
    finally:
        if first.returncode is None:
            await _stop_child(first, first_handle, kill=True)
        elif not first_handle.closed:
            first_handle.close()
        if second is not None and second_handle is not None:
            await _stop_child(second, second_handle, kill=False)


async def _child_main() -> None:
    """Run one independent HA process against the shared durable config."""
    from homeassistant import bootstrap, runner
    from homeassistant.helpers import recorder as recorder_helper

    config_dir = Path(os.environ[_CONFIG_DIR_ENV]).resolve()
    port = int(os.environ[_PORT_ENV])
    generation = int(os.environ[_GENERATION_ENV])
    sync_dir = Path(os.environ[_SYNC_DIR_ENV]).resolve()
    sys.path.insert(0, str(config_dir))

    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=False)
    )
    assert hass is not None
    if recorder_helper.DATA_RECORDER not in hass.data:
        recorder_helper.async_initialize_recorder(hass)
    await hass.async_start()

    entry = await _ensure_entry(hass)
    await _ensure_browser_auth(hass, sync_dir, f"http://127.0.0.1:{port}")
    await hass.async_block_till_done()

    if generation == 2:
        titles = {
            sub.title
            for sub in entry.subentries.values()
            if sub.subentry_type == "conversation"
        }
        assert any(title.startswith("Cross-engine ") for title in titles)

    (sync_dir / f"ha-ready-{generation}").write_text("ready\n", encoding="utf-8")
    try:
        await asyncio.Event().wait()
    finally:
        await hass.async_stop()


if __name__ == "__main__" and os.environ.get(_CHILD_ENV) == "1":
    asyncio.run(_child_main())
