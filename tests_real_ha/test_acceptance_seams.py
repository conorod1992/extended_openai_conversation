"""Close the remaining high-value seams in genuine Home Assistant acceptance."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
    FUNCTION_GROUP_LOADING_ON_DEMAND,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
)
from homeassistant.components import conversation
from homeassistant.core import HomeAssistant

from tests_real_ha.test_cross_feature_acceptance import (
    _agent as _conversation_agent,
    _provider,
    _say,
    _speech,
)
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _entry,
    _fresh_reload,
    _management_call,
    _setup_entry,
)
from tests_real_ha.test_provider_wire_e2e import (
    _agent as _wire_agent,
    _chat_sse_tool_call,
    _install_wire,
    _prepare_service,
    _say as _wire_say,
)


def _routing_rule() -> dict[str, Any]:
    return {
        "name": "Acceptance conversation route",
        "enabled": True,
        "phrases": ["use the deep route"],
        "match_type": "equals",
        "action_type": "model_routing",
        "action": {
            "model": "gpt-6-astra",
            "reasoning_effort": "xhigh",
            "scope": "conversation",
            "reset": False,
            "continue_to_ai": True,
            "success_response": "Route selected",
        },
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": 0,
    }


async def test_explicit_continue_to_ai_conversation_route_persists_for_next_turn(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equals + Continue-to-AI reaches the provider and retains conversation scope."""
    agent = await _conversation_agent(hass)
    await agent._request_rules.async_create(_routing_rule())
    sent = _provider(monkeypatch, agent, ["Deep route active.", "Still deep."])

    first = await _say(hass, agent, "use the deep route")
    assert _speech(first) == "Deep route active."
    assert sent[0]["model"] == "gpt-6-astra"
    assert sent[0]["reasoning_effort"] == "xhigh"

    second = await _say(hass, agent, "And now?", first.conversation_id)
    assert second.conversation_id == first.conversation_id
    assert _speech(second) == "Still deep."
    assert len(sent) == 2
    assert sent[1]["model"] == "gpt-6-astra"
    assert sent[1]["reasoning_effort"] == "xhigh"


async def test_provider_failure_after_tool_execution_does_not_commit_continuity(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed second provider request must not commit the partial tool-loop turn."""
    agent = await _wire_agent(hass, "chat_completions")
    calls = await _prepare_service(hass)
    record = AsyncMock(wraps=agent._continuity.async_record_success)
    monkeypatch.setattr(agent._continuity, "async_record_success", record)
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(),
            (
                400,
                {
                    "error": {
                        "message": "acceptance failure after tool execution",
                        "type": "invalid_request_error",
                        "param": None,
                        "code": "acceptance_failure",
                    }
                },
            ),
        ],
    )

    result = await _wire_say(hass, agent)

    assert result.response.error_code is not None
    assert len(calls) == 1
    assert len(wire.requests) == 2
    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_configuration_write_is_observed_by_loaded_agent(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    entry = _entry("Configuration Runtime Observation")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    before = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )

    await _management_call(
        client,
        entry=entry,
        section="configuration",
        action="update",
        revision=before["revision"],
        config={CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES: 53},
    )
    await _fresh_reload(hass, entry)

    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    assert agent.subentry.data[CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES] == 53


@pytest.mark.asyncio
async def test_memory_edit_and_delete_survive_real_reload(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    entry = _entry("Memory CRUD Acceptance")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    created = await _management_call(
        client,
        entry=entry,
        section="memories",
        action="add",
        content="Original acceptance memory.",
        category="acceptance",
    )
    memory_id = created["memory"]["memory_id"]

    await _management_call(
        client,
        entry=entry,
        section="memories",
        action="update",
        memory_id=memory_id,
        content="Updated acceptance memory.",
        category="updated",
    )
    await _fresh_reload(hass, entry)
    reloaded = await _management_call(
        client, entry=entry, section="memories", action="list"
    )
    assert [
        (item["memory_id"], item["content"], item["category"])
        for item in reloaded["memories"]
    ] == [(memory_id, "Updated acceptance memory.", "updated")]

    deleted = await _management_call(
        client,
        entry=entry,
        section="memories",
        action="delete",
        memory_id=memory_id,
    )
    assert deleted["deleted"] == 1
    await _fresh_reload(hass, entry)
    final = await _management_call(
        client, entry=entry, section="memories", action="list"
    )
    assert final["memories"] == []


def _local_rule(rule_id: str, name: str, phrase: str, order: int) -> dict[str, Any]:
    return {
        "id": rule_id,
        "name": name,
        "enabled": True,
        "phrases": [phrase],
        "match_type": "equals",
        "action_type": "local_action",
        "action": {
            "actions": [
                {
                    "domain": "script",
                    "service": "turn_on",
                    "target": {"entity_id": [f"script.{rule_id.replace('-', '_')}"]},
                    "data": {},
                }
            ],
            "success_response": f"{name} complete",
            "failure_response": f"{name} failed",
        },
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": order,
    }


@pytest.mark.asyncio
async def test_request_rule_edit_reorder_delete_survive_real_reload(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    entry = _entry("Request Rule CRUD Acceptance")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    first = await _management_call(
        client,
        entry=entry,
        section="request_rules",
        action="create",
        rule=_local_rule("acceptance-first", "First rule", "first phrase", 0),
    )
    second = await _management_call(
        client,
        entry=entry,
        section="request_rules",
        action="create",
        rule=_local_rule("acceptance-second", "Second rule", "second phrase", 1),
    )

    await _management_call(
        client,
        entry=entry,
        section="request_rules",
        action="move",
        rule_id=second["rule"]["id"],
        direction="up",
    )
    replacement = deepcopy(first["rule"])
    replacement["name"] = "First rule edited"
    replacement["action"]["success_response"] = "Edited success"
    await _management_call(
        client,
        entry=entry,
        section="request_rules",
        action="update",
        rule_id=first["rule"]["id"],
        rule=replacement,
    )

    await _fresh_reload(hass, entry)
    reloaded = await _management_call(
        client, entry=entry, section="request_rules", action="list"
    )
    assert [rule["id"] for rule in reloaded["rules"][:2]] == [
        second["rule"]["id"],
        first["rule"]["id"],
    ]
    edited = next(
        rule for rule in reloaded["rules"] if rule["id"] == first["rule"]["id"]
    )
    assert edited["name"] == "First rule edited"
    assert edited["action"]["success_response"] == "Edited success"

    deleted = await _management_call(
        client,
        entry=entry,
        section="request_rules",
        action="delete",
        rule_id=second["rule"]["id"],
        confirm=True,
    )
    assert deleted["deleted"] is True
    await _fresh_reload(hass, entry)
    final = await _management_call(
        client, entry=entry, section="request_rules", action="list"
    )
    assert [rule["id"] for rule in final["rules"]] == [first["rule"]["id"]]


@pytest.mark.asyncio
async def test_function_tool_and_group_edit_delete_survive_real_reload(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    entry = _entry("Function CRUD Acceptance")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    tool = {
        "spec": {
            "name": "acceptance_crud_tool",
            "description": "Original description.",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "get_user_from_user_id"},
    }
    group = {
        "id": "acceptance_crud_group",
        "name": "Acceptance CRUD Group",
        "description": "Original group description.",
        "loading_mode": FUNCTION_GROUP_LOADING_ON_DEMAND,
        "functions": ["acceptance_crud_tool"],
        "enabled": True,
    }

    await _management_call(
        client, entry=entry, section="tools", action="save", tool=tool
    )
    await hass.async_block_till_done()
    await _management_call(
        client, entry=entry, section="tools", action="save_group", group=group
    )
    await hass.async_block_till_done()

    edited_tool = deepcopy(tool)
    edited_tool["spec"]["description"] = "Edited description."
    await _management_call(
        client,
        entry=entry,
        section="tools",
        action="save",
        tool=edited_tool,
        original_name="acceptance_crud_tool",
    )
    await hass.async_block_till_done()
    edited_group = deepcopy(group)
    edited_group["description"] = "Edited group description."
    await _management_call(
        client,
        entry=entry,
        section="tools",
        action="save_group",
        group=edited_group,
        original_id="acceptance_crud_group",
    )
    await hass.async_block_till_done()

    await _fresh_reload(hass, entry)
    reloaded = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    saved_tool = next(
        item
        for item in reloaded["config"]["functions"]
        if item["spec"]["name"] == "acceptance_crud_tool"
    )
    saved_group = next(
        item
        for item in reloaded["config"]["function_groups"]
        if item["id"] == "acceptance_crud_group"
    )
    assert saved_tool["spec"]["description"] == "Edited description."
    assert saved_group["description"] == "Edited group description."

    await _management_call(
        client,
        entry=entry,
        section="tools",
        action="delete_group",
        group_id="acceptance_crud_group",
        confirm=True,
    )
    await hass.async_block_till_done()
    await _management_call(
        client,
        entry=entry,
        section="tools",
        action="delete",
        name="acceptance_crud_tool",
        confirm=True,
    )
    await hass.async_block_till_done()
    await _fresh_reload(hass, entry)

    final = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    assert not any(
        item["spec"]["name"] == "acceptance_crud_tool"
        for item in final["config"]["functions"]
    )
    assert not any(
        item["id"] == "acceptance_crud_group"
        for item in final["config"]["function_groups"]
    )
