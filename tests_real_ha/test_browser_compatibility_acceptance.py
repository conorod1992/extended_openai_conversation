"""One genuine native-frontend smoke journey shared by supported HA versions."""
from __future__ import annotations

from importlib.metadata import version
import os
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses.const import CONF_API_MODE
from homeassistant.components import conversation
from tests_real_ha.test_browser_backend_acceptance import (
    _run_playwright,
    real_ha_shell as real_ha_shell,
)
from tests_real_ha.test_management_backend_acceptance import _entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_HA_BROWSER_COMPATIBILITY") != "1",
    reason="compact genuine browser compatibility matrix only",
)


async def test_compact_native_frontend_version_contract(hass, real_ha_shell, monkeypatch):
    shell = real_ha_shell
    entry = shell["entry"]
    subentry = next(iter(entry.subentries.values()))
    hass.config_entries.async_update_subentry(entry, subentry, data={**subentry.data, CONF_API_MODE:"chat_completions"})
    await hass.async_block_till_done()
    await _setup_entry(hass, _entry("Compatibility secondary assistant"))
    agent = conversation.async_get_agent(hass, entry.entry_id)
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text("Compatibility Assist response")])
    point = os.environ.get("HA_POINT", "local")
    installed = version("homeassistant")
    print(f"HA browser compatibility point={point} version={installed}", flush=True)
    await _run_playwright(
        repo_root=Path(__file__).resolve().parents[1],
        spec="tests_browser/real-ha-compatibility.spec.mjs",
        config="playwright.real-ha-shell.config.mjs",
        env={**shell["env"], "REAL_HA_SMOKE_USER_ID":shell["admin"].id, "PLAYWRIGHT_ARTIFACT_SUFFIX":f"compatibility-{point}-{installed}"},
        failure_label=f"Genuine HA browser compatibility failed: {point} / {installed}",
    )
    assert len(wire.requests) == 1
    assert wire.requests[0]["path"] == "/v1/chat/completions"
    assert wire.requests[0]["body"]["model"] == "gpt-5-mini"
