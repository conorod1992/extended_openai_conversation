"""Configuration repair retains the same model-route validation as normal edits."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_function_repair as repair,
    management_ui as ui,
    request_rules as rr,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
)
from homeassistant.exceptions import HomeAssistantError

from .test_function_repair_configuration_contract import _repairable_agent
from .test_request_rules import MemoryStore, routing_rule


@pytest.fixture
async def routed_agent(hass, monkeypatch):
    entry, subentry = _repairable_agent()
    subentry.data.update(chat_model="gpt-4o", api_mode="auto", reasoning_effort="")
    rules = rr.RequestRules(MemoryStore())
    await rules.async_initialize()
    route = routing_rule()
    route["action"].update(model="gpt-5-pro", reasoning_effort="high")
    await rules.async_create(route)
    monkeypatch.setattr(ui, "entry_and_agent", lambda *_: (entry, subentry))
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=rules))

    def persist(_entry, target, *, data, **kwargs):
        target.data = data
        target.title = kwargs.get("title", target.title)

    hass.config_entries.async_update_subentry.side_effect = persist
    return hass, entry, subentry, rules


async def _command(agent, section, action, config):
    hass, entry, subentry, _rules = agent
    return await ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "section": section,
            "action": action,
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "revision": repair.repair_revision(subentry),
            "config": config,
        },
    )


@pytest.mark.parametrize(
    "section,action",
    [
        ("configuration", "validate"),
        ("configuration", "save"),
        ("configuration", "update"),
        ("function_repair", "configuration_validate"),
        ("function_repair", "configuration_save"),
    ],
)
async def test_incompatible_retained_route_is_rejected_before_any_write(
    routed_agent, section, action
):
    hass, entry, subentry, rules = routed_agent
    if section == "configuration":
        subentry.data = repair.safe_function_configuration(subentry.data)
    before = deepcopy(subentry.data)
    rules_before = rules.snapshot()
    # gpt-4o itself supports this mode; only the retained gpt-5-pro route fails.
    ui._validated_model_request(
        {**repair.safe_function_configuration(before), "api_mode": "chat_completions"},
        entry.data,
    )
    if "validate" in action:
        result = await _command(
            routed_agent, section, action, {"api_mode": "chat_completions"}
        )
        assert result["valid"] is False
        assert "api_mode" in result["errors"]
        assert "gpt-5-pro" in result["errors"]["api_mode"]
    else:
        with pytest.raises(HomeAssistantError, match="gpt-5-pro"):
            await _command(
                routed_agent, section, action, {"api_mode": "chat_completions"}
            )
    assert subentry.data == before
    assert rules.snapshot() == rules_before
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_compatible_repair_preview_and_save_preserve_quarantined_data(
    routed_agent,
):
    hass, _entry, subentry, rules = routed_agent
    before = deepcopy(subentry.data)
    preview = await _command(
        routed_agent,
        "function_repair",
        "configuration_validate",
        {"api_mode": "responses"},
    )
    assert preview["valid"] is True
    assert subentry.data == before
    saved = await _command(
        routed_agent, "function_repair", "configuration_save", {"api_mode": "responses"}
    )
    assert saved["valid"] is True
    assert subentry.data["api_mode"] == "responses"
    for key in (CONF_FUNCTION_TOOLS, CONF_FUNCTION_GROUPS):
        assert subentry.data[key] == before[key]
    assert rules.snapshot()["rules"][0]["action"]["model"] == "gpt-5-pro"
    hass.config_entries.async_update_subentry.assert_called_once()


async def test_disabled_route_does_not_block_repair_save(routed_agent):
    _hass, _entry, subentry, rules = routed_agent
    rule = rules.snapshot()["rules"][0]
    await rules.async_update(rule["id"], {**rule, "enabled": False})
    saved = await _command(
        routed_agent,
        "function_repair",
        "configuration_save",
        {"api_mode": "chat_completions"},
    )
    assert saved["valid"] is True
    assert subentry.data["api_mode"] == "chat_completions"
