"""Native UI authoring, durable reload and provider-wire semantic golden journeys."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_MODE,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from tests_real_ha.test_browser_backend_acceptance import (
    _run_playwright,
    real_ha_shell as real_ha_shell,
)
from tests_real_ha.test_knowledge_provider_wire_e2e import _chat_sse_tool_call
from tests_real_ha.test_management_backend_acceptance import _entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire, _speech

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_HA_BROWSER") != "1",
    reason="native shell golden journeys belong in browser acceptance/nightly",
)


@pytest.mark.parametrize("feature", ["tool", "context"])
async def test_native_shell_golden_runtime_journey(hass, real_ha_shell, monkeypatch, feature):
    shell = real_ha_shell
    entry = shell["entry"]
    subentry = next(iter(entry.subentries.values()))
    hass.config_entries.async_update_subentry(entry, subentry, data={
        **subentry.data, CONF_API_MODE: "chat_completions", CONF_FUNCTION_TOOLS: [],
        CONF_FUNCTION_GROUPS: [], CONF_MEMORY_AUTO_RETRIEVE_LIMIT: 3,
    })
    await hass.async_block_till_done()
    secondary = _entry("Golden secondary assistant")
    await _setup_entry(hass, secondary)
    phases = ["create", "edit", "disable"] if feature == "tool" else ["create", "disable"]
    for phase in phases:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parents[1],
            spec="tests_browser/real-ha-golden.spec.mjs",
            config="playwright.real-ha-shell.config.mjs",
            env={**shell["env"], "REAL_HA_GOLDEN_FEATURE": feature, "REAL_HA_GOLDEN_PHASE": phase},
            failure_label=f"Native shell golden {feature}/{phase} failed",
        )
        await hass.async_block_till_done()
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        agent = conversation.async_get_agent(hass, entry.entry_id)
        assert agent is not None
        enabled = phase != "disable"
        name = "golden_shell_tool" if feature == "tool" else "knowledge_search"
        replies = []
        if enabled:
            arguments = {} if feature == "tool" else {"query": "golden observatory", "limit": 3}
            replies.append(_chat_sse_tool_call("call-golden", name, arguments))
        expected = f"Golden {feature} {phase} complete"
        replies.append(_chat_sse_text(expected))
        wire = _install_wire(monkeypatch, agent, replies)
        result = await conversation.async_converse(
            hass=hass, text="golden observatory token and calibration", conversation_id=None,
            context=Context(user_id=shell["admin"].id), language="en", agent_id=entry.entry_id,
        )
        assert _speech(result) == expected
        assert len(wire.requests) == (2 if enabled else 1)
        first = wire.requests[0]["body"]
        advertised = {tool["function"]["name"] for tool in first.get("tools", [])}
        assert (name in advertised) is enabled
        if feature == "context":
            assert ("cobalt-golden-memory" in json.dumps(first["messages"])) is enabled
        if enabled:
            tool_messages = [item for item in wire.requests[1]["body"]["messages"] if item.get("role") == "tool"]
            assert len(tool_messages) == 1
            marker = f"golden-tool-{'v1' if phase == 'create' else 'v2'}" if feature == "tool" else "cobalt-golden-knowledge"
            assert marker in tool_messages[0]["content"]
        # A save through the first assistant must not affect its sibling.
        sibling = conversation.async_get_agent(hass, secondary.entry_id)
        assert sibling is not None
        assert all(tool.get("spec", {}).get("name") != "golden_shell_tool" for tool in sibling.subentry.data.get(CONF_FUNCTION_TOOLS, []))
