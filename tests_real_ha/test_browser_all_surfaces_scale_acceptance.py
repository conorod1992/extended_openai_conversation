"""Large all-surfaces browser acceptance against genuine Home Assistant."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_MODE,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    async_get_knowledge,
)
from custom_components.extended_openai_conversation_responses.memory import (
    async_get_memory,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    async_get_request_rules,
)
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_browser_backend_acceptance import (
    _run_playwright,
    _start_ws_bridge,
)
from tests_real_ha.test_management_backend_acceptance import (
    ADMIN_ID,
    _admin_client,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_HA_BROWSER") != "1",
    reason="enabled only by the browser-to-genuine-HA acceptance job",
)


def _tool(index: int) -> dict[str, Any]:
    return {
        "spec": {
            "name": f"scale_browser_tool_{index}",
            "description": f"Large browser fixture tool {index}",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "template",
            "value_template": f"scale-browser-{index}",
        },
        "enabled": True,
    }


@pytest.mark.asyncio
async def test_large_configuration_renders_across_all_browser_surfaces(
    hass,
    hass_ws_client,
) -> None:
    """One realistically large agent remains navigable across every management family."""
    tools = [_tool(index) for index in range(60)]
    groups = [
        {
            "id": f"scale-browser-group-{index}",
            "name": f"Scale browser group {index}",
            "description": "All-surfaces browser scale fixture",
            "loading_mode": "always" if index % 2 == 0 else "on_demand",
            "functions": [
                f"scale_browser_tool_{index * 3 + offset}"
                for offset in range(3)
            ],
            "enabled": True,
        }
        for index in range(20)
    ]
    entry = _make_entry(
        "Large Browser Surfaces",
        include_ai_task=False,
        conversation_options={
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_KNOWLEDGE_ENABLED: True,
            CONF_FUNCTION_TOOLS: tools,
            CONF_FUNCTION_GROUPS: groups,
        },
    )
    await _setup_entry(hass, entry)
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == "conversation"
    )

    client = await _admin_client(hass, hass_ws_client)
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)
    rules = await async_get_request_rules(hass, entry.entry_id, subentry.subentry_id)

    for index in range(100):
        created = await memory.async_add(
            ADMIN_ID,
            f"Large browser memory {index}",
            "browser-scale",
            "explicit",
            key=f"browser-scale-{index}",
        )
        assert created["status"] == "created"

    for index in range(80):
        await knowledge.async_create(
            f"Large browser source {index}",
            f"Browser scale description {index}",
            f"Large browser Knowledge body {index}",
        )

    for index in range(100):
        await rules.async_create(
            {
                "name": f"Large browser rule {index}",
                "phrases": [f"large browser command {index}"],
                "match_type": "equals",
                "action_type": "local_action",
                "action": {
                    "actions": [{"action": "script.turn_on"}],
                    "success_response": "Done",
                    "failure_response": "Failed",
                },
            }
        )

    runner, backend_url = await _start_ws_bridge(client)
    try:
        await _run_playwright(
            repo_root=Path(__file__).resolve().parent.parent,
            spec="tests_browser/real-ha-all-surfaces-scale.spec.mjs",
            config="playwright.config.mjs",
            env={"REAL_HA_BACKEND_URL": backend_url},
            failure_label="Large all-surfaces genuine HA browser acceptance failed",
        )
    finally:
        await runner.cleanup()

    # Browser reads must not mutate or truncate the authoritative large datasets.
    assert len(await memory.async_list(ADMIN_ID, limit=100)) == 100
    assert (await knowledge.async_catalog(limit=100))["total"] == 80
    assert len(rules.snapshot()["rules"]) == 100
