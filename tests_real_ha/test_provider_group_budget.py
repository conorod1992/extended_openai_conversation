"""Effective tool contracts through the real conversation group's loader."""

import pytest

from custom_components.extended_openai_conversation_responses.conversation import (
    _ACTIVE_FUNCTION_GROUP_SESSION,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    FunctionGroupRuntime,
)
from homeassistant.components import conversation
from tests.test_function_groups import _group, _tool
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry


@pytest.mark.parametrize(
    "provider,mode,limited",
    [("azure", "chat_completions", True), ("openai", "responses", False)],
)
async def test_group_loading_obeys_effective_provider_budget(
    hass, monkeypatch, provider, mode, limited
):
    tools = [_tool(f"tool_{index}") for index in range(160)]
    groups = [
        _group("first", [tool["spec"]["name"] for tool in tools[:80]]),
        _group("second", [tool["spec"]["name"] for tool in tools[80:]]),
    ]
    entry = _make_entry(
        include_ai_task=False,
        data={"api_provider": provider, "api_version": "2024-10-21"},
        conversation_options={
            "functions": tools,
            "function_groups": groups,
            "api_mode": mode,
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    session = FunctionGroupRuntime().begin("budget", 30)
    token = _ACTIVE_FUNCTION_GROUP_SESSION.set(session)
    try:
        assert agent._load_function_groups(["first"])["status"] == "success"
        second = agent._load_function_groups(["second"])
        if limited:
            assert second["status"] == "error"
            assert session.loaded_group_ids == {"first"}
            assert len(agent._get_function_tools()) <= 128
        else:
            assert second["status"] == "success"
            assert len(agent._get_function_tools()) >= 160
    finally:
        _ACTIVE_FUNCTION_GROUP_SESSION.reset(token)
