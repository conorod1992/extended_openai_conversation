"""Management save-to-native-execution tests for audited Request Rules."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    function_dependency_integrity as deps,
    management_ui,
    request_rules as rr,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    validate_function_arguments,
)
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from tests.test_audit_request_rules import prepare_native
from tests.test_request_rules_management import MemoryStore, local_rule


def tool(name, value_type=None):
    return {
        "enabled": True,
        "spec": {
            "name": name,
            "description": name,
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": value_type}} if value_type else {},
                "required": ["value"] if value_type else [],
                "additionalProperties": False,
            },
        },
        "function": {"type": "template", "value_template": "Done"},
    }


@pytest.mark.parametrize(
    "value_type,value", [("string", "123456"), ("integer", 7), ("boolean", True)]
)
async def test_management_save_then_native_execution_preserves_types(
    hass, management_agent, management_message, monkeypatch, value_type, value
):
    prepare_native(hass)
    hass.config.legacy_templates = False
    _, subentry = management_agent
    configured = [tool("consume", value_type), tool("lookup")]
    subentry.data = {**subentry.data, "functions": configured}
    manager = rr.RequestRules(MemoryStore())
    await manager.async_initialize()
    monkeypatch.setattr(
        management_ui, "async_get_request_rules", AsyncMock(return_value=manager)
    )
    rule = local_rule()
    if value_type == "string":
        rule.update(match_type="sentence_pattern", phrases=["remember {value}"])
        actions = [
            {
                "type": "function",
                "function": "consume",
                "arguments": {"value": {"source": "slot", "slot": "value"}},
            }
        ]
        text = "remember 123456"
    else:
        rule["phrases"] = ["run"]
        actions = [
            {
                "action": f"{rr.DOMAIN}.call_function",
                "data": {
                    "function": "lookup",
                    "arguments": {},
                    "result_alias": "lookup",
                },
            },
            {
                "action": f"{rr.DOMAIN}.call_function",
                "data": {"function": "consume", "arguments": {"value": "{lookup}"}},
            },
        ]
        text = "run"
    rule["action"]["actions"] = actions
    saved = await management_ui.async_management_command(
        hass, "admin", True, management_message("request_rules", "create", rule=rule)
    )
    assert saved["rule"]["id"]
    seen = []

    async def execute(name, arguments):
        spec = next(item["spec"] for item in configured if item["spec"]["name"] == name)
        validated = validate_function_arguments(spec, arguments)
        if name == "lookup":
            return value
        seen.append(validated["value"])
        assert type(validated["value"]) is type(value)
        return "Done"

    async def dispatch(domain, service, service_data, **_kwargs):
        data = service_data
        if service == "call_function":
            return {
                "result": await rr.async_call_active_function(
                    data["function"],
                    data.get("arguments", {}),
                    data.get("result_alias"),
                )
            }
        return None

    hass.services.async_call.side_effect = dispatch
    result = await rr.async_evaluate_rule(
        hass,
        manager,
        rr.RequestRuleRuntime(),
        text,
        "audit",
        context=Context(),
        function_executor=execute,
    )
    assert result.successful
    assert seen == [value]


async def test_management_disables_retained_invalid_pattern_but_cannot_activate_it(
    hass, management_message, monkeypatch
):
    raw = local_rule()
    raw.update(match_type="sentence_pattern", phrases=["bad {"])
    manager = rr.RequestRules(MemoryStore({"rules": [raw]}))
    await manager.async_initialize()
    monkeypatch.setattr(
        management_ui, "async_get_request_rules", AsyncMock(return_value=manager)
    )
    disabled = {**manager.snapshot()["rules"][0], "enabled": False}
    result = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message("request_rules", "update", rule_id=raw["id"], rule=disabled),
    )
    assert result["rule"]["enabled"] is False
    for action in ["create", "update"]:
        with pytest.raises((ValueError, HomeAssistantError)):
            await management_ui.async_management_command(
                hass,
                "admin",
                True,
                management_message(
                    "request_rules",
                    action,
                    rule_id=raw["id"],
                    rule={**disabled, "enabled": True},
                ),
            )
    assert (
        await deps.async_rename_function_reference_recursive(
            manager, "unrelated", "new"
        )
        == 0
    )


async def test_rename_changes_only_referencing_rules_with_inactive_legacy_sibling():
    invalid = local_rule(rule_id="invalid")
    invalid.update(match_type="sentence_pattern", phrases=["bad {"])
    valid = local_rule(rule_id="valid")
    valid["action"]["actions"] = [
        {
            "action": f"{rr.DOMAIN}.call_function",
            "data": {"function": "old", "arguments": {}},
        }
    ]
    manager = rr.RequestRules(MemoryStore({"rules": [invalid, valid]}))
    await manager.async_initialize()
    before = deepcopy(manager.snapshot()["rules"][0])
    assert (
        await deps.async_rename_function_reference_recursive(manager, "old", "new") == 1
    )
    snapshot = manager.snapshot()
    assert next(rule for rule in snapshot["rules"] if rule["id"] == "invalid") == before
    assert snapshot["diagnostics"]


async def test_management_azure_create_update_validates_underlying_model(
    hass, management_agent, management_message, monkeypatch
):
    entry, subentry = management_agent
    entry.data = {"api_provider": "azure"}
    subentry.data = {
        **subentry.data,
        "chat_model": "deployment",
        "azure_model": "o3",
        "api_mode": "chat_completions",
        "functions": [],
    }
    manager = rr.RequestRules(MemoryStore())
    await manager.async_initialize()
    monkeypatch.setattr(
        management_ui, "async_get_request_rules", AsyncMock(return_value=manager)
    )
    rule = local_rule()
    rule.update(
        action_type="model_routing",
        action={
            "model": "deployment",
            "reasoning_effort": "medium",
            "scope": "conversation",
        },
    )
    created = await management_ui.async_management_command(
        hass, "admin", True, management_message("request_rules", "create", rule=rule)
    )
    updated_rule = {
        **created["rule"],
        "action": {**created["rule"]["action"], "reasoning_effort": "high"},
    }
    updated = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "request_rules", "update", rule_id=created["rule"]["id"], rule=updated_rule
        ),
    )
    assert updated["rule"]["action"]["model"] == "deployment"
    subentry.data = {**subentry.data, "azure_model": "gpt-4o"}
    with pytest.raises(HomeAssistantError, match="reasoning"):
        await management_ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "request_rules",
                "update",
                rule_id=created["rule"]["id"],
                rule=updated_rule,
            ),
        )


def test_authored_and_already_migrated_jinja_are_preserved():
    for value in ["{{ value | int }}", "{{ value }}", "{{ request.slots['value'] }}"]:
        assert rr._migrate_slot_templates(value) == value
    assert (
        rr._migrate_slot_templates({"value_from": "slot", "slot": "value"}) == "{value}"
    )
