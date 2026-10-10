"""Function-only edits retain the provider settings and routed-model defaults."""

from copy import deepcopy

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    management_function_repair as repair,
    request as provider_requests,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.management_function_repair import (
    persisted_config_projection,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    validate_rule_model_request,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_function_groups_mutation import _group
from tests.test_management_ui import _entry_pair, _tool
from tests.test_request_rules import routing_rule


@pytest.mark.parametrize("edit", ["description", "group"])
@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"chat_model": "gpt-5-mini"},
        {
            "chat_model": "gpt-5.6",
            "reasoning_effort": "low",
            "api_mode": "responses",
            "max_tokens": 750,
        },
        {"chat_model": "gpt-4.1", "temperature": 0.2, "top_p": 0.7},
    ],
)
def test_function_edits_preserve_implicit_and_explicit_settings(hass, settings, edit):
    entry, subentry = _entry_pair()
    original = {
        **deepcopy(settings),
        "functions": [_tool("example")],
        "function_groups": [],
        "extension_data": {"version": 2},
    }
    subentry.data = deepcopy(original)
    hass.config_entries.async_update_subentry.side_effect = (
        lambda _entry, target, **changes: setattr(target, "data", changes["data"])
    )
    route = routing_rule()
    route["action"].update(model="gpt-5.6", reasoning_effort=None)
    validate_rule_model_request(route, subentry.data, entry.data)
    before = build_provider_request_snapshot(
        {**subentry.data, "chat_model": "gpt-5.6"}, entry.data
    )
    tools = deepcopy(original["functions"])
    groups = []
    if edit == "description":
        tools[0]["spec"]["description"] = "Updated description"
    else:
        # Exercise the projection reuse path, preserving exact Function YAML.
        subentry.data["functions"] = yaml.safe_dump(tools)
        original["functions"] = subentry.data["functions"]
        persisted_config_projection(subentry).snapshot = {
            **agent_config_defaults(),
            "functions": deepcopy(tools),
            "function_groups": [],
        }
        groups = [_group("example_group", ["example"])]
    result = repair.persist_valid_function_configuration(
        hass, entry, subentry, tools, groups
    )
    assert result["revision"]
    for key in set(original) - {"functions", "function_groups"}:
        assert subentry.data[key] == original[key]
    assert set(subentry.data) == set(original)
    assert subentry.data["function_groups"] == groups
    if edit == "group":
        assert subentry.data["functions"] == original["functions"]
    validate_rule_model_request(route, subentry.data, entry.data)
    after = build_provider_request_snapshot(
        {**subentry.data, "chat_model": "gpt-5.6"}, entry.data
    )
    assert after.api_kwargs == before.api_kwargs
    if not settings.get("reasoning_effort"):
        assert (
            after.api_kwargs.get("reasoning", {}).get(
                "effort", after.api_kwargs.get("reasoning_effort")
            )
            == "low"
        )


def test_function_extra_updates_are_validated_and_preserved(hass):
    entry, subentry = _entry_pair()
    subentry.data = {
        "functions": [_tool("example")],
        "function_groups": [],
        "guest_allowed_function_names": ["old"],
        "guest_policy_version": 2,
    }
    hass.config_entries.async_update_subentry.side_effect = (
        lambda _entry, target, **changes: setattr(target, "data", changes["data"])
    )
    repair.persist_valid_function_configuration(
        hass,
        entry,
        subentry,
        subentry.data["functions"],
        [],
        extra_updates={"guest_allowed_function_names": ["example"]},
    )
    assert subentry.data["guest_allowed_function_names"] == ["example"]
    assert "reasoning_effort" not in subentry.data


def test_function_save_still_validates_actual_provider_settings(hass, monkeypatch):
    capabilities = deepcopy(provider_requests.get_model_capabilities("gpt-5.6"))
    capabilities["tools"]["function"]["chat_completions"] = {"support": "never"}
    monkeypatch.setattr(
        provider_requests, "get_model_capabilities", lambda _model: capabilities
    )
    entry, subentry = _entry_pair()
    subentry.data = {
        "functions": [],
        "function_groups": [],
        "chat_model": "gpt-5.6",
        "api_mode": "chat_completions",
        "reasoning_effort": "low",
    }
    original = deepcopy(subentry.data)
    build_provider_request_snapshot(subentry.data, entry.data)
    # Isolate this validation boundary from future model-catalog changes.
    with pytest.raises(HomeAssistantError, match="function/tool calling"):
        repair.persist_valid_function_configuration(
            hass, entry, subentry, [_tool("example")], []
        )
    assert subentry.data == original
    hass.config_entries.async_update_subentry.assert_not_called()
