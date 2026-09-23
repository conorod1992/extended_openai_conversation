"""Browser-originated management changes must affect the loaded HA conversation agent."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
from typing import Any

import pytest
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_REASONING_EFFORT,
)
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_browser_backend_acceptance import _start_ws_bridge
from tests_real_ha.test_management_backend_acceptance import _admin_client
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _speech,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_HA_BROWSER") != "1",
    reason="enabled only by the browser-to-genuine-HA acceptance job",
)


async def _run_runtime_browser(
    repo_root: Path,
    backend_url: str,
    *,
    spec: str,
    extra_env: dict[str, str] | None = None,
    failure_label: str = "Playwright browser-to-live-runtime setup failed",
) -> None:
    """Exercise one shipped browser/runtime acceptance journey."""
    npx = shutil.which("npx")
    assert npx is not None, "npx is required for genuine HA browser acceptance"
    process = await asyncio.create_subprocess_exec(
        npx,
        "playwright",
        "test",
        spec,
        "--config=playwright.config.mjs",
        cwd=repo_root,
        env={
            **os.environ,
            "CI": "1",
            "REAL_HA_BACKEND_URL": backend_url,
            **(extra_env or {}),
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    async with asyncio.timeout(180):
        stdout, _ = await process.communicate()
    output = stdout.decode("utf-8", errors="replace")
    assert process.returncode == 0, f"{failure_label}:\n{output}"


async def _run_reload_browser_phase(
    repo_root: Path,
    backend_url: str,
    phase: str,
) -> None:
    """Run one side of the browser-authored reload-persistence journey."""
    await _run_runtime_browser(
        repo_root,
        backend_url,
        spec="tests_browser/real-ha-runtime-reload.spec.mjs",
        extra_env={"REAL_HA_RUNTIME_RELOAD_PHASE": phase},
        failure_label=f"Playwright browser runtime reload {phase} phase failed",
    )


@pytest.mark.asyncio
async def test_browser_created_request_rule_survives_unload_reload_and_stays_live(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: Any,
) -> None:
    """Browser-authored routing must survive a genuine HA unload/reload cycle."""
    entry = _make_entry(
        "Browser Runtime Reload Acceptance",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_REASONING_EFFORT: "medium",
            CONF_FUNCTION_TOOLS: [],
            CONF_FUNCTION_GROUPS: [],
        },
    )
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    repo_root = Path(__file__).resolve().parent.parent

    runner, backend_url = await _start_ws_bridge(client)
    try:
        await _run_reload_browser_phase(repo_root, backend_url, "create")
    finally:
        await runner.cleanup()

    await hass.async_block_till_done()
    original_agent = conversation.async_get_agent(hass, entry.entry_id)
    assert original_agent is not None

    # Prove the browser-authored route reaches the currently loaded agent before
    # testing that the same contract survives a genuine unload/reload.
    before_wire = _install_wire(
        monkeypatch,
        original_agent,
        [_chat_sse_text("Browser-created route reached the live agent.")],
    )
    before = await conversation.async_converse(
        hass=hass,
        text="browser reload runtime route",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )
    assert _speech(before) == "Browser-created route reached the live agent."
    assert len(before_wire.requests) == 1
    before_request = before_wire.requests[0]
    assert before_request["path"] == "/v1/chat/completions"
    assert before_request["body"]["model"] == "gpt-6-astra"
    assert before_request["body"]["reasoning_effort"] == "xhigh"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert conversation.async_get_agent(hass, entry.entry_id) is None

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    reloaded_agent = conversation.async_get_agent(hass, entry.entry_id)
    assert reloaded_agent is not None
    assert reloaded_agent is not original_agent

    runner, backend_url = await _start_ws_bridge(client)
    try:
        await _run_reload_browser_phase(repo_root, backend_url, "verify")
    finally:
        await runner.cleanup()

    wire = _install_wire(
        monkeypatch,
        reloaded_agent,
        [_chat_sse_text("Reloaded browser route reached the live agent.")],
    )
    result = await conversation.async_converse(
        hass=hass,
        text="browser reload runtime route",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )

    assert _speech(result) == "Reloaded browser route reached the live agent."
    assert len(wire.requests) == 1
    request = wire.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["body"]["model"] == "gpt-6-astra"
    assert request["body"]["reasoning_effort"] == "xhigh"

