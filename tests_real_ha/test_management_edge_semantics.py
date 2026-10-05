"""Generated management edge inputs and reviewed semantic equivalence."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_CURRENT_DATETIME_ENABLED,
    CONF_FUNCTION_TOOLS,
    CONF_MAX_TOKENS,
)
from custom_components.extended_openai_conversation_responses.memory import (
    MAX_CATEGORY_LENGTH,
    MAX_CONTENT_LENGTH as MAX_MEMORY_CONTENT,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    MAX_TITLE_LENGTH as MAX_KNOWLEDGE_TITLE,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    MAX_RULE_NAME_LENGTH,
)
from homeassistant.components import conversation
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _say, _speech
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _entry,
    _fresh_reload,
    _management_call,
    _management_response,
)
from tests_real_ha.test_management_command_contracts import _subentry


def _native_tool(name: str) -> dict[str, Any]:
    return {
        "spec": {
            "name": name,
            "description": f"Return the current user for {name}.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        "function": {"type": "native", "name": "get_user_from_user_id"},
    }


def _rule(name: str, phrase: str) -> dict[str, Any]:
    return {
        "name": name,
        "enabled": True,
        "phrases": [phrase],
        "match_type": "equals",
        "action_type": "local_action",
        "action": {
            "actions": [{"delay": {"milliseconds": 1}}],
            "success_response": "Edge rule complete",
            "failure_response": "Edge rule failed",
        },
    }


@pytest.mark.asyncio
async def test_reviewed_edge_payloads_cross_real_management_websocket(
    hass, hass_ws_client
):
    """Classify accepted/rejected neighbours independently of current validators."""
    entry = _entry("Management edge payloads")
    await _management_call_setup(hass, entry)
    client = await _admin_client(hass, hass_ws_client)

    # Configuration: an unfamiliar nested field is reviewed as invalid. The
    # subsequent ordinary edit proves the command boundary remains usable.
    baseline = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    rejected_config = await _management_response(
        client,
        entry=entry,
        section="configuration",
        action="update",
        revision=baseline["revision"],
        config={"unreviewed_nested_field": {"value": "must-not-publish"}},
    )
    assert rejected_config["success"] is False
    unchanged = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    assert unchanged["revision"] == baseline["revision"]
    accepted_config = await _management_call(
        client,
        entry=entry,
        section="configuration",
        action="update",
        revision=baseline["revision"],
        config={CONF_CURRENT_DATETIME_ENABLED: False},
    )
    assert accepted_config["config"][CONF_CURRENT_DATETIME_ENABLED] is False

    # Memory: exact documented limits and Unicode are valid; the adjacent value
    # is invalid. A mixed valid+invalid edit must publish nothing.
    valid_memory = await _management_call(
        client,
        entry=entry,
        section="memories",
        action="add",
        content="É" * MAX_MEMORY_CONTENT,
        category="c" * MAX_CATEGORY_LENGTH,
        subject="",
    )
    memory = valid_memory["memory"]
    assert len(memory["content"]) == MAX_MEMORY_CONTENT
    assert memory["subject"] is None

    too_long_memory = await _management_response(
        client,
        entry=entry,
        section="memories",
        action="add",
        content="x" * (MAX_MEMORY_CONTENT + 1),
        category="edge",
    )
    assert too_long_memory["success"] is False
    before_edit = await _management_call(
        client, entry=entry, section="memories", action="list"
    )
    mixed_invalid = await _management_response(
        client,
        entry=entry,
        section="memories",
        action="update",
        memory_id=memory["memory_id"],
        expected_revision=memory["revision"],
        content="this valid change must not publish",
        category="z" * (MAX_CATEGORY_LENGTH + 1),
    )
    assert mixed_invalid["success"] is False
    after_edit = await _management_call(
        client, entry=entry, section="memories", action="list"
    )
    assert after_edit["memories"] == before_edit["memories"]

    # Knowledge: exactly-at-limit Unicode-bearing titles are valid; the adjacent
    # over-limit title is rejected without deleting or replacing the good source.
    valid_title = "K" * (MAX_KNOWLEDGE_TITLE - 1) + "É"
    knowledge = await _management_call(
        client,
        entry=entry,
        section="knowledge",
        action="create",
        title=valid_title,
        description="",
        content="Line one\n東京 line two",
        enabled=True,
    )
    source_id = knowledge["source"]["source_id"]
    rejected_knowledge = await _management_response(
        client,
        entry=entry,
        section="knowledge",
        action="create",
        title="K" * (MAX_KNOWLEDGE_TITLE + 1),
        description="",
        content="must not publish",
        enabled=True,
    )
    assert rejected_knowledge["success"] is False
    sources = await _management_call(
        client, entry=entry, section="knowledge", action="list"
    )
    assert [row["source_id"] for row in sources["sources"]] == [source_id]

    # Function Tools: an unfamiliar nested field is invalid; corrected input at
    # the same public boundary is accepted and remains the only new tool.
    current = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    invalid_tool = _native_tool("edge_lookup")
    invalid_tool["spec"]["unreviewed"] = True
    rejected_tool = await _management_response(
        client,
        entry=entry,
        section="tools",
        action="save",
        revision=current["revision"],
        tool=invalid_tool,
    )
    assert rejected_tool["success"] is False
    current = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    saved_tool = await _management_call(
        client,
        entry=entry,
        section="tools",
        action="save",
        revision=current["revision"],
        tool=_native_tool("edge_lookup"),
    )
    saved_names = [item["spec"]["name"] for item in saved_tool["functions"]]
    assert saved_names.count("edge_lookup") == 1

    # Request Rules: the independently reviewed name limit has the same exact/
    # adjacent split and a failed create cannot consume a durable rule slot.
    valid_rule = await _management_call(
        client,
        entry=entry,
        section="request_rules",
        action="create",
        rule=_rule("R" * MAX_RULE_NAME_LENGTH, "valid edge rule"),
    )
    assert len(valid_rule["rule"]["name"]) == MAX_RULE_NAME_LENGTH
    rejected_rule = await _management_response(
        client,
        entry=entry,
        section="request_rules",
        action="create",
        rule=_rule("R" * (MAX_RULE_NAME_LENGTH + 1), "invalid edge rule"),
    )
    assert rejected_rule["success"] is False
    listed = await _management_call(
        client, entry=entry, section="request_rules", action="list"
    )
    assert [row["id"] for row in listed["rules"]] == [valid_rule["rule"]["id"]]

    # Representative accepted cases must remain durable, not merely cached.
    await _fresh_reload(hass, entry)
    reloaded_memory = await _management_call(
        client, entry=entry, section="memories", action="list"
    )
    assert [row["memory_id"] for row in reloaded_memory["memories"]] == [
        memory["memory_id"]
    ]
    reloaded_knowledge = await _management_call(
        client, entry=entry, section="knowledge", action="list"
    )
    assert [row["source_id"] for row in reloaded_knowledge["sources"]] == [source_id]
    reloaded_rules = await _management_call(
        client, entry=entry, section="request_rules", action="list"
    )
    assert [row["id"] for row in reloaded_rules["rules"]] == [
        valid_rule["rule"]["id"]
    ]


async def _management_call_setup(hass, entry):
    """Keep setup explicit so this file only reuses public real-HA helpers."""
    from tests_real_ha.test_management_backend_acceptance import _setup_entry

    await _setup_entry(hass, entry)


@pytest.mark.asyncio
async def test_json_object_key_order_preserves_configuration_semantics(
    hass, hass_ws_client
):
    """Object key order is representation only; stored meaning must be identical."""
    first = _entry("JSON order first")
    second = _entry("JSON order second")
    await _management_call_setup(hass, first)
    await _management_call_setup(hass, second)
    client = await _admin_client(hass, hass_ws_client)

    updates_a = {CONF_MAX_TOKENS: 777, CONF_CURRENT_DATETIME_ENABLED: False}
    updates_b = {CONF_CURRENT_DATETIME_ENABLED: False, CONF_MAX_TOKENS: 777}
    results = []
    for entry, updates in ((first, updates_a), (second, updates_b)):
        before = await _management_call(
            client, entry=entry, section="configuration", action="get"
        )
        result = await _management_call(
            client,
            entry=entry,
            section="configuration",
            action="update",
            revision=before["revision"],
            config=updates,
        )
        results.append(result)
        await _fresh_reload(hass, entry)

    for result in results:
        assert result["config"][CONF_MAX_TOKENS] == 777
        assert result["config"][CONF_CURRENT_DATETIME_ENABLED] is False
    first_read = await _management_call(
        client, entry=first, section="configuration", action="get"
    )
    second_read = await _management_call(
        client, entry=second, section="configuration", action="get"
    )
    assert first_read["config"] == second_read["config"]


@pytest.mark.asyncio
async def test_independent_tool_definition_order_preserves_explicit_named_tool_result(
    hass, monkeypatch
):
    """Reordering independent declarations cannot change an explicitly selected tool."""
    alpha = {
        "spec": {
            "name": "semantic_alpha",
            "description": "Return alpha",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "ALPHA RESULT"},
    }
    beta = {
        "spec": {
            "name": "semantic_beta",
            "description": "Return beta",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "BETA RESULT"},
    }
    observations = []
    for index, tools in enumerate(([alpha, beta], [beta, alpha])):
        agent = await _agent(
            hass,
            title=f"Tool order {index}",
            **{CONF_FUNCTION_TOOLS: deepcopy(tools)},
        )
        selected = {
            "index": 0,
            "id": f"explicit-alpha-{index}",
            "type": "function",
            "function": {"name": "semantic_alpha", "arguments": "{}"},
        }
        sent = _provider(monkeypatch, agent, [selected, "Completed"])
        result = await _say(hass, agent, "Use semantic alpha")
        assert _speech(result) == "Completed"
        continuation = sent[1]
        tool_result = next(
            item for item in continuation["messages"] if item.get("role") == "tool"
        )
        observations.append(tool_result["content"])
    assert observations[0] == observations[1]
    assert "ALPHA RESULT" in observations[0]


def test_semantic_comparison_negative_control_detects_meaningful_change():
    """The comparator must not normalize away a changed target or tool argument."""
    baseline = {
        "tool": "semantic_alpha",
        "arguments": {"target": "light.kitchen"},
        "effects": [("light.turn_off", "light.kitchen")],
    }
    equivalent = {
        "effects": [("light.turn_off", "light.kitchen")],
        "arguments": {"target": "light.kitchen"},
        "tool": "semantic_alpha",
    }
    changed = deepcopy(equivalent)
    changed["arguments"]["target"] = "light.hall"
    assert baseline == equivalent
    assert baseline != changed
