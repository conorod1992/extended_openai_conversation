"""Effective previews and fresh live requests share the resolved Guest projection."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
from custom_components.extended_openai_conversation_responses.function_groups import (
    assemble_function_tools,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    resolve_guest_policy,
)
from tests.test_function_groups import _group, _tool
from homeassistant.helpers import device_registry as dr, entity_registry as er


@pytest.mark.parametrize("mode", ["always", "on_demand"])
@pytest.mark.parametrize("flag", [None, False, True])
@pytest.mark.parametrize("policy_mode", ["on", "custom", "off", "legacy", "inactive"])
async def test_group_preview_equals_live_assembly(
    hass, monkeypatch, mode, flag, policy_mode
):
    tool = _tool("get_history")
    tool["function"]["name"] = "get_history"
    tool["guest_allowed"] = True
    group = _group("history", ["get_history"], mode)
    if flag is not None:
        group["guest_allowed"] = flag
    options = agent_config_snapshot(
        {
            "chat_model": "gpt-4.1",
            "api_mode": "responses",
            "functions": [tool],
            "function_groups": [group],
            "guest_policy_version": 2,
            "guest_function_policy": policy_mode
            if policy_mode in {"on", "custom", "off"}
            else "on",
            "guest_allowed_group_ids": ["history"],
            "memory_mode": "off",
            "temporary_memory": "off",
        }
    )
    if policy_mode == "legacy":
        options.pop("guest_policy_version", None)
        monkeypatch.setattr(dr, "async_get", lambda _: SimpleNamespace(async_get=lambda _: None))
        monkeypatch.setattr(er, "async_get", lambda _: SimpleNamespace(async_get=lambda _: None))
    manager = SimpleNamespace(
        is_active=lambda: policy_mode != "inactive",
        status=lambda: {"currently_active": policy_mode != "inactive"},
    )
    tools = options["functions"]
    policy = resolve_guest_policy(hass, options, manager, tools)
    live_tools, live_groups = ExtendedOpenAIAgentEntity._filter_guest_tools_and_groups(
        tools, options["function_groups"], policy
    )
    live = assemble_function_tools(live_tools, live_groups, set())
    monkeypatch.setattr(
        preview, "async_get_guest_mode", AsyncMock(return_value=manager)
    )
    monkeypatch.setattr(preview, "get_loaded_temporary_memory", lambda *_: None)
    monkeypatch.setattr(preview, "get_loaded_knowledge", lambda *_: None)
    monkeypatch.setattr(preview.SkillManager, "get_loaded_instance", lambda: None)
    monkeypatch.setattr(preview, "get_exposed_entities", lambda *_: [])
    monkeypatch.setattr(
        preview,
        "render_effective_prompt",
        lambda *_a, **_k: SimpleNamespace(text="Instructions", sections=[]),
    )
    result = await preview.async_preview_effective_request(
        hass,
        SimpleNamespace(entry_id="entry", data={}),
        SimpleNamespace(subentry_id="assistant"),
        options,
        "admin",
    )
    projected = [item for section in result["sections"] if section["key"] in {"function_tools", "function_group_loader"} for item in json.loads(section["content"])]
    actual = [item["name"] for item in projected]
    assert actual == [item["spec"]["name"] for item in live.tools]
    permitted = (
        policy_mode in {"on", "custom", "inactive"}
        or policy_mode == "legacy"
        and flag is True
    )
    assert (
        "get_history" in actual
        if mode == "always"
        else "load_function_groups" in actual
    ) is permitted
    assert result["function_group_savings"]["grouped_characters"] >= 2
