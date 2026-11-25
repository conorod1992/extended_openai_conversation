"""Legacy action aliases are retained for repair without executing a script."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    request_rules as rules,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
    async_request_rule_match_preview,
)
from tests.test_request_rules import MemoryStore, local_rule, manager


@pytest.mark.parametrize(
    "consumer",
    [
        {"wait_template": "{lookup.ready}"},
        {"variables": {"value": "{lookup}"}},
        {
            "if": [{"condition": "template", "value_template": "{{ lookup.ready }}"}],
            "then": [{"delay": 0}],
        },
        {"action": "test.consume", "data": {"{{ lookup.key }}": True}},
        {"action": "test.consume", "data": {"result_alias": "{lookup.value}"}},
        {"set_conversation_response": "{{ __eoai_result_lookup.result }}"},
    ],
)
@pytest.mark.parametrize("restore", [False, True])
async def test_legacy_action_alias_stops_eligible_match_without_execution(
    hass, consumer, restore
):
    legacy = local_rule()
    legacy["continue_matching"] = True
    legacy["action"].update(
        actions=[
            {
                "action": f"{DOMAIN}.call_function",
                "data": {
                    "function": "read_sensor",
                    "arguments": {},
                    "result_alias": "lookup",
                },
            },
            consumer,
        ],
        continue_to_ai=True,
    )
    fallback = local_rule("Fallback", order=1)
    configured = await manager(legacy, fallback)
    if restore:
        backup = await configured.async_backup_data()
        configured = await manager()
        await configured.async_replace_backup(backup)
    saved = configured.rule_snapshot(legacy["id"])
    assert saved["action"]["actions"][1] == consumer
    status = configured.diagnostic_snapshot()
    assert "result_alias" in status["diagnostics"][legacy["id"]]
    assert status["matching_diagnostic_ids"] == [legacy["id"]]
    assert rules.MATCH_REPAIR not in saved
    preview = await async_request_rule_match_preview(hass, configured, "good night")
    assert preview["would_do"]["needs_repair"]
    assert preview["would_do"]["provider_input"] == "none"
    assert len(preview["matched_rules"]) == 1

    execute = AsyncMock()
    outcome = await rules.async_evaluate_rule(
        hass,
        configured,
        rules.RequestRuleRuntime(),
        "good night",
        "session",
        function_executor=execute,
    )
    assert outcome.consume and outcome.terminal and not outcome.successful
    assert "needs repair" in outcome.response
    execute.assert_not_awaited()
    hass.services.async_call.assert_not_awaited()
    with pytest.raises(ValueError, match="result_alias"):
        await configured.async_update(legacy["id"], saved)

    disabled = deepcopy(saved)
    disabled["enabled"] = False
    await configured.async_update(legacy["id"], disabled)
    assert configured.diagnostic_snapshot()["matching_diagnostic_ids"] == []
    assert configured.match("good night").rule["id"] == fallback["id"]

    repaired = deepcopy(saved)
    repaired["action"]["actions"][0]["data"].pop("result_alias")
    repaired["action"]["actions"][0]["response_variable"] = "sensor_data"
    repaired["action"]["actions"][1] = {
        "set_conversation_response": "{{ sensor_data.result.value }}"
    }
    await configured.async_update(legacy["id"], repaired)
    assert legacy["id"] not in configured.snapshot()["diagnostics"]
    assert not configured.match("good night").rule.get(rules.MATCH_REPAIR)


async def test_function_rename_preserves_legacy_alias_execution_block():
    legacy = local_rule()
    legacy["action"]["actions"] = [
        {
            "action": f"{DOMAIN}.call_function",
            "data": {
                "function": "read_sensor",
                "result_alias": "lookup",
            },
        },
        {"set_conversation_response": "{{ lookup.value }}"},
    ]
    configured = await manager(legacy)
    await configured.async_rename_function_reference("read_sensor", "read_battery")
    assert configured.match("good night").rule[rules.MATCH_REPAIR]


async def test_function_rename_preserves_legacy_matching_repair():
    legacy = local_rule()
    legacy["continue_matching"] = True
    legacy["action"]["actions"] = [
        {"action": f"{DOMAIN}.call_function", "data": {"function": "read_sensor"}}
    ]
    configured = await manager(legacy)
    assert await configured.async_rename_function_reference("read_sensor", "read_battery") == 1
    raw = configured.rule_snapshot(legacy["id"])
    assert raw["action"]["actions"][0]["data"]["function"] == "read_battery"
    assert raw["continue_matching"] is True
    assert configured.match("good night").rule[rules.MATCH_REPAIR]
    assert configured.diagnostic_snapshot()["matching_diagnostic_ids"] == [legacy["id"]]


async def test_restore_preflight_retains_legacy_rule_with_unavailable_function(hass):
    from custom_components.extended_openai_conversation_responses.transfer import (
        _async_validate_request_rule_function_dependencies,
    )

    legacy = local_rule()
    legacy["action"]["actions"] = [
        {
            "action": f"{DOMAIN}.call_function",
            "data": {
                "function": "unavailable",
                "result_alias": "lookup",
            },
        },
        {"set_conversation_response": "{lookup.value}"},
    ]
    backup = rules.RequestRules.validate_backup_data({"rules": [legacy]})
    await _async_validate_request_rule_function_dependencies(hass, backup, {})
    active = await manager()
    await active.async_replace_backup(backup)
    assert legacy["id"] in active.snapshot()["diagnostics"]


async def test_group_removal_preserves_legacy_alias_execution_block():
    legacy = local_rule()
    legacy["group_id"] = "sensors"
    legacy["action"]["actions"] = [
        {
            "action": f"{DOMAIN}.call_function",
            "data": {"function": "read_sensor", "result_alias": "lookup"},
        },
        {"set_conversation_response": "{{ lookup.value }}"},
    ]
    configured = await manager()
    await configured.async_replace_backup(
        {"groups": [{"id": "sensors", "name": "Sensors"}], "rules": [legacy]}
    )
    await configured.async_set_groups([])
    assert configured.rule_snapshot(legacy["id"])["group_id"] is None
    assert configured.match("good night").rule[rules.MATCH_REPAIR]


@pytest.mark.parametrize("mutation", ["update", "create", "duplicate"])
async def test_capacity_mutations_keep_admitted_alias_repair_in_matching(
    monkeypatch, mutation
):
    monkeypatch.setattr(rules, "MAX_AGENT_PATTERN_STATES", 6)
    first = local_rule("First", ["morning"], match_type="sentence_pattern")
    legacy = local_rule(
        "Legacy", ["check sensor"], match_type="sentence_pattern", order=1
    )
    legacy["action"]["actions"] = [
        {
            "action": f"{DOMAIN}.call_function",
            "data": {"function": "read_sensor", "result_alias": "lookup"},
        },
        {"set_conversation_response": "{lookup.value}"},
    ]
    fallback = local_rule("Fallback", ["check sensor"], order=2)
    store = MemoryStore()
    configured = rules.RequestRules(store)
    await configured.async_replace_backup({"rules": [first, legacy, fallback]})
    before = deepcopy(store.data)
    revision = configured.revision()
    assert configured.match("check sensor").rule[rules.MATCH_REPAIR]

    with pytest.raises(ValueError, match="state limit"):
        if mutation == "update":
            await configured.async_update(
                first["id"], {**first, "phrases": ["good morning"]}
            )
        elif mutation == "create":
            await configured.async_create(
                local_rule(
                    "Another", ["lights"], match_type="sentence_pattern", order=0
                )
            )
        else:
            await configured.async_duplicate(first["id"])

    assert configured.revision() == revision
    assert store.data == before
    assert configured.match("check sensor").rule[rules.MATCH_REPAIR]
    restored = await manager()
    await restored.async_replace_backup(await configured.async_backup_data())
    assert restored.match("check sensor").rule[rules.MATCH_REPAIR]


@pytest.mark.parametrize("service", [f"{DOMAIN}.call_function", "{{ selected_action }}"])
async def test_legacy_native_response_review_survives_backup_and_reorder(hass, service):
    legacy = local_rule("Read battery")
    steps = [
        {"action": "test.before"},
        {"action": service, "data": {"function": "read_sensor"}, "response_variable": "battery"},
        {"set_conversation_response": "{{ battery.get('result').get('result').level }}"},
    ]
    legacy["action"].update(actions=steps, continue_to_ai=True)
    fallback = local_rule("Fallback", order=1)
    spare = local_rule("Spare", ["other"], order=2)
    configured = await manager(legacy, fallback, spare)
    assert configured.snapshot()["diagnostics"][legacy["id"]].startswith(
        "Function response format changed."
    )

    exported = await configured.async_backup_data()
    assert "function_response_version" not in exported["rules"][0]
    await configured.async_replace_backup(exported)
    saved = configured.rule_snapshot(legacy["id"])
    assert saved["action"]["actions"] == steps
    await configured.async_move(spare["id"], "top")
    assert configured.match("good night").rule[rules.MATCH_REPAIR]

    execute = AsyncMock()
    outcome = await rules.async_evaluate_rule(
        hass, configured, rules.RequestRuleRuntime(), "good night", "session",
        function_executor=execute,
    )
    assert outcome.consume and outcome.terminal and not outcome.successful
    assert "Function response format changed." in outcome.response
    execute.assert_not_awaited()
    hass.services.async_call.assert_not_awaited()
    preview = await async_request_rule_match_preview(hass, configured, "good night")
    assert preview["would_do"]["needs_repair"]
    assert preview["would_do"]["provider_input"] == "none"

    candidate = deepcopy(saved)
    candidate["action"]["actions"][2]["set_conversation_response"] = "{{ battery.result.level }}"
    with pytest.raises(ValueError, match="Function response format changed"):
        await configured.async_update(legacy["id"], candidate)
    assert configured.match("good night").rule[rules.MATCH_REPAIR]

    await configured.async_update(legacy["id"], {"enabled": False})
    assert configured.match("good night").rule["id"] == fallback["id"]
    with pytest.raises(ValueError, match="Function response format changed"):
        await configured.async_update(
            legacy["id"], {"enabled": True}, confirm_function_response_change=True
        )
    await configured.async_update(
        legacy["id"], candidate, confirm_function_response_change=True
    )
    fresh = await manager()
    await fresh.async_replace_backup(await configured.async_backup_data())
    assert legacy["id"] not in fresh.snapshot()["diagnostics"]
    assert fresh.rule_snapshot(legacy["id"])["action"]["actions"] == candidate["action"]["actions"]


async def test_legacy_native_response_review_requires_management_confirmation(
    hass, management_agent, management_message, monkeypatch
):
    from custom_components.extended_openai_conversation_responses import management_ui

    legacy = local_rule("Battery lookup")
    legacy["action"]["actions"] = [
        {"action": f"{DOMAIN}.call_function", "data": {"function": "read_sensor"}, "response_variable": "reply"},
        {"set_conversation_response": "{{ reply.result.result.level }}"},
    ]
    configured = await manager(legacy)
    monkeypatch.setattr(management_ui, "async_get_request_rules", AsyncMock(return_value=configured))
    monkeypatch.setattr(management_ui, "_async_validate_request_rule_mutation", AsyncMock(return_value=None))
    edited = configured.rule_snapshot(legacy["id"])
    edited["function_response_version"] = 2  # Browser metadata cannot acknowledge a repair.
    edited["action"]["actions"][1]["set_conversation_response"] = "{{ reply.result.level }}"

    async def save(**extras):
        return await management_ui.async_management_command(
            hass, "admin", True, management_message(
                "request_rules", "update", rule_id=legacy["id"], rule=edited,
                revision=configured.revision(), **extras
            )
        )

    with pytest.raises(ValueError, match="Function response format changed"):
        await save()
    assert configured.snapshot()["matching_diagnostic_ids"] == [legacy["id"]]
    result = await save(confirm=True)
    assert not result["diagnostics"]
    assert result["rule"]["function_response_version"] == 2
