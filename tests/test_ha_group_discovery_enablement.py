"""Group switches gate discovery without depending on advertised schemas."""

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_request_preview as preview,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    async_discover,
    current_snapshot,
    new_reference_tool,
)
from tests.test_function_groups import _group
from tests.test_ha_llm_tools import Echo, TestAPI, context, register


@pytest.mark.parametrize(
    "active", ["none", "ungrouped", "always", "on_demand", "disabled_member"]
)
async def test_only_enabled_references_discover_shared_source_and_reenable(
    hass, active
):
    other = Echo()
    other.name = "other"
    api = register(hass, TestAPI(hass, tools=[Echo(), other]))
    discovered = await async_discover(hass, context())
    saved = [
        new_reference_tool(item.reference, set()) for item in discovered.tools.values()
    ]
    held, shared = saved
    held["spec"]["name"] = "held_echo"
    shared["spec"]["name"] = "shared_other"
    held["enabled"] = True
    shared["enabled"] = active != "disabled_member"
    groups = [_group("held", ["held_echo"], "always", enabled=False)]
    tools = [held]
    if active != "none":
        tools.append(shared)
        if active != "ungrouped":
            groups.append(
                _group(
                    "active",
                    ["shared_other"],
                    "on_demand" if active == "on_demand" else "always",
                )
            )
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    entity.subentry = SimpleNamespace(data={"function_groups": groups})
    entity._configured_function_tools_from_data = MagicMock(return_value=tools)
    entity._effective_guest_policy = lambda: SimpleNamespace(guest_active=False)
    snapshots = []

    async def handle(*_args):
        snapshots.append(current_snapshot())
        return "done"

    entity._async_handle_message = AsyncMock(side_effect=handle)
    user = SimpleNamespace(as_llm_context=lambda _: context())
    before = deepcopy(tools)
    api.contexts.clear()
    await entity._async_handle_message_with_ha_tools(user, object())
    assert len(api.contexts) == int(active not in {"none", "disabled_member"})
    assert len(snapshots[-1].tools) == int(active not in {"none", "disabled_member"})
    # No group session was loaded, including the enabled on-demand group.
    assert tools == before
    groups[0]["enabled"] = True
    await entity._async_handle_message_with_ha_tools(user, object())
    assert len(api.contexts) == 1 + int(active not in {"none", "disabled_member"})
    assert len(snapshots[-1].tools) == 1 + int(
        active not in {"none", "disabled_member"}
    )
    assert tools == before


@pytest.mark.parametrize(
    ("group_enabled", "tool_enabled", "mode", "budget", "guest", "expected"),
    [
        (False, True, "always", 5, False, 0),
        (True, False, "always", 5, False, 0),
        (True, True, "always", 0, False, 0),
        (True, True, "always", 5, True, 0),
        (True, True, "always", 5, False, 1),
        (True, True, "on_demand", 5, False, 1),
    ],
)
async def test_preview_and_live_request_discover_the_same_eligible_references(
    hass, monkeypatch, group_enabled, tool_enabled, mode, budget, guest, expected
):
    api = register(hass, TestAPI(hass))
    discovered = await async_discover(hass, context())
    tool = new_reference_tool(next(iter(discovered.tools.values())).reference, set())
    tool["enabled"] = tool_enabled
    name = tool["spec"]["name"]
    options = agent_config_snapshot(
        {
            "chat_model": "gpt-4.1",
            "functions": [tool],
            "function_groups": [_group("remote", [name], mode, enabled=group_enabled)],
            "max_function_calls_per_conversation": budget,
            "memory_mode": "off",
            "temporary_memory": "off",
            "guest_policy_version": 2,
        }
    )
    hass.config.language = "en"
    entry = SimpleNamespace(entry_id="entry", data={})
    subentry = SimpleNamespace(subentry_id="assistant", data=options)
    guest_manager = SimpleNamespace(is_active=lambda: guest, status=lambda: {})
    policy = preview.resolve_guest_policy(
        hass, options, guest_manager, options["functions"], exposed_entities=[]
    )
    monkeypatch.setattr(
        preview,
        "resolve_guest_policy",
        lambda *_a: policy,
    )
    monkeypatch.setattr(
        preview, "async_get_guest_mode", AsyncMock(return_value=guest_manager)
    )
    monkeypatch.setattr(preview, "get_exposed_entities", lambda *_: [])
    monkeypatch.setattr(
        preview,
        "render_effective_prompt",
        lambda *_a, **_k: SimpleNamespace(text="Instructions", sections=[]),
    )
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    entity.subentry = subentry
    entity._configured_function_tools_from_data = lambda data: data["functions"]
    entity._effective_guest_policy = lambda: policy
    entity._async_handle_message = AsyncMock()

    api.contexts.clear()
    await entity._async_handle_message_with_ha_tools(
        SimpleNamespace(as_llm_context=lambda _: context("alice", "speaker")),
        object(),
    )
    assert [(ctx.context.user_id, ctx.device_id) for ctx in api.contexts] == [
        ("alice", "speaker")
    ] * expected
    api.contexts.clear()
    result = await preview.async_preview_effective_request(
        hass, entry, subentry, options, "admin"
    )
    assert [(ctx.context.user_id, ctx.device_id) for ctx in api.contexts] == [
        ("admin", None)
    ] * expected
    by_key = {section["key"]: section["content"] for section in result["sections"]}
    assert bool(json.loads(by_key["function_tools"])) == (
        expected == 1 and mode == "always"
    )
    assert bool(json.loads(by_key["function_group_loader"])) == (
        expected == 1 and mode == "on_demand"
    )
