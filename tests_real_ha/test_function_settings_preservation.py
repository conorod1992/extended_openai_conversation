"""A UI-authored model route survives an unrelated Function edit in real HA."""

from copy import deepcopy

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    validate_rule_model_request,
)
from homeassistant.components import conversation
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry


async def test_sparse_function_save_preserves_model_route_reasoning(hass):
    tool = {
        "spec": {
            "name": "example",
            "description": "Example",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "ok"},
    }
    entry = _make_entry(
        include_ai_task=False,
        conversation_options={"functions": [tool], "function_groups": []},
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    subentry = agent.subentry
    route = {
        "name": "Routed reasoning",
        "phrases": ["think carefully"],
        "match_type": "equals",
        "action_type": "model_routing",
        "action": {
            "model": "gpt-5.6",
            "reasoning_effort": None,
            "scope": "request",
            "reset": False,
            "continue_to_ai": True,
        },
    }
    message = {"entry_id": entry.entry_id, "subentry_id": subentry.subentry_id}
    saved = await management_ui.async_management_command(
        hass,
        None,
        True,
        {
            **message,
            "section": "request_rules",
            "action": "create",
            "rule": route,
            "revision": agent._request_rules.revision(),
        },
    )
    route = saved["rule"]
    validate_rule_model_request(route, subentry.data, entry.data)
    before = build_provider_request_snapshot(
        {**subentry.data, "chat_model": "gpt-5.6"}, entry.data
    )
    before_settings = deepcopy(
        {
            key: value
            for key, value in subentry.data.items()
            if key not in {"functions", "function_groups"}
        }
    )
    edited = deepcopy(tool)
    edited["spec"]["description"] = "An unrelated description edit"
    await management_ui.async_management_command(
        hass,
        None,
        True,
        {
            **message,
            "section": "tools",
            "action": "save",
            "tool": edited,
            "original_name": "example",
            "revision": management_ui._agent_config_revision(
                subentry.data, subentry.title
            ),
        },
    )
    await hass.async_block_till_done()
    assert {
        key: value
        for key, value in subentry.data.items()
        if key not in {"functions", "function_groups"}
    } == before_settings
    assert "chat_model" not in subentry.data
    assert "reasoning_effort" not in subentry.data
    validate_rule_model_request(route, subentry.data, entry.data)
    after = build_provider_request_snapshot(
        {**subentry.data, "chat_model": "gpt-5.6"}, entry.data
    )
    assert after.api_kwargs == before.api_kwargs
    assert after.api_kwargs["reasoning"]["effort"] == "low"
