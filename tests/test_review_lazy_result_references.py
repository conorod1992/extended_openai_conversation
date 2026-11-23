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
from tests.test_request_rules import local_rule, manager


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
    assert "success response" in configured.snapshot()["diagnostics"][legacy["id"]]
    assert rules.RESULT_ALIAS_REPAIR not in saved
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
    with pytest.raises(ValueError, match="only available in the success response"):
        await configured.async_update(legacy["id"], saved)

    disabled = deepcopy(saved)
    disabled["enabled"] = False
    await configured.async_update(legacy["id"], disabled)
    assert configured.match("good night").rule["id"] == fallback["id"]

    repaired = deepcopy(saved)
    repaired["action"]["actions"][0]["response_variable"] = "sensor_data"
    repaired["action"]["actions"][1] = {
        "set_conversation_response": "{{ sensor_data.result.value }}"
    }
    await configured.async_update(legacy["id"], repaired)
    assert legacy["id"] not in configured.snapshot()["diagnostics"]
    assert not configured.match("good night").rule.get(rules.RESULT_ALIAS_REPAIR)


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
    assert configured.match("good night").rule[rules.RESULT_ALIAS_REPAIR]


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
