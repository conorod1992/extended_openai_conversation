"""Browser-originated management changes must affect the loaded HA conversation agent."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
from typing import Any

import pytest
from homeassistant.components import conversation
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


async def _run_runtime_browser(repo_root: Path, backend_url: str) -> None:
    """Create the routing rule through the shipped browser management UI."""
    npx = shutil.which("npx")
    assert npx is not None, "npx is required for genuine HA browser acceptance"
    process = await asyncio.create_subprocess_exec(
        npx,
        "playwright",
        "test",
        "tests_browser/real-ha-runtime.spec.mjs",
        "--config=playwright.config.mjs",
        cwd=repo_root,
        env={
            **os.environ,
            "CI": "1",
            "REAL_HA_BACKEND_URL": backend_url,
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    async with asyncio.timeout(180):
        stdout, _ = await process.communicate()
    output = stdout.decode("utf-8", errors="replace")
    assert process.returncode == 0, (
        "Playwright browser-to-live-runtime setup failed:\n" + output
    )


@pytest.mark.asyncio
async def test_browser_created_request_rule_changes_live_provider_request(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: Any,
) -> None:
    """A browser save must reach the live agent and alter its provider request."""
    entry = _make_entry(
        "Browser Runtime Acceptance",
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
    runner, backend_url = await _start_ws_bridge(client)

    repo_root = Path(__file__).resolve().parent.parent
    try:
        await _run_runtime_browser(repo_root, backend_url)
    finally:
        await runner.cleanup()

    # The browser has finished. Exercise Home Assistant's public conversation API
    # against the actual currently-loaded agent; do not read the management store
    # directly as a substitute for proving that runtime consumption is current.
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_text("Browser-created route reached the live agent.")],
    )

    result = await conversation.async_converse(
        hass=hass,
        text="browser runtime route",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )

    assert _speech(result) == "Browser-created route reached the live agent."
    assert len(wire.requests) == 1
    request = wire.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["body"]["model"] == "gpt-6-astra"
    assert request["body"]["reasoning_effort"] == "xhigh"
