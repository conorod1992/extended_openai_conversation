"""Saved dependency, bounded matching and native template regression tests."""

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_ui,
    request_rules as rules,
)
from custom_components.extended_openai_conversation_responses.function_dependency_integrity import (
    async_validate_request_rule_functions,
)
from custom_components.extended_openai_conversation_responses.request_rule_packs import (
    async_append_rule_pack,
    export_rule_pack,
    prepare_rule_pack,
    validate_rule_pack,
)
from custom_components.extended_openai_conversation_responses.request_rule_patterns import (
    MatchBudget,
    SentenceMatchLimitError,
)
from custom_components.extended_openai_conversation_responses.skills import (
    Skill,
    SkillMdParser,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_function_dependency_integrity import _call, _tool
from tests.test_request_rules import MemoryStore, local_rule


@pytest.mark.parametrize("description", [123, True, ["x"], {"x": ["y"]}, None])
def test_skill_description_requires_text(description):
    with pytest.raises(ValueError, match="must be a string"):
        Skill("audit", description, Path("audit"))


def test_alias_description_is_rejected_without_expansion(tmp_path):
    content = "---\na: &a [long, long, long]\nb: &b [*a, *a, *a]\ndescription: [*b, *b, *b]\n---\nbody"
    assert (
        SkillMdParser.parse(content, tmp_path / "audit" / "SKILL.md", tmp_path) is None
    )


@pytest.mark.asyncio
async def test_disabled_dependency_can_be_saved_and_repaired(hass):
    rule = local_rule()
    rule["action"]["actions"] = [_call()]
    rule["enabled"] = False
    await async_validate_request_rule_functions(hass, rule, [])
    management_ui._validate_request_rule_functions(rule, [])
    disabled = {**_tool(), "enabled": False}
    await async_validate_request_rule_functions(hass, rule, [disabled])
    rule["enabled"] = True
    with pytest.raises(HomeAssistantError, match="unavailable or disabled"):
        await async_validate_request_rule_functions(hass, rule, [disabled])
    await async_validate_request_rule_functions(
        hass, rule, [disabled], require_ready=False
    )
    await async_validate_request_rule_functions(hass, rule, [_tool()])
    from custom_components.extended_openai_conversation_responses.transfer import (
        _async_validate_request_rule_function_dependencies,
    )

    await _async_validate_request_rule_function_dependencies(
        hass, {"rules": [rule]}, {"functions": [disabled]}
    )


@pytest.mark.parametrize(
    "action",
    [
        {"action": "light.turn_on", "data": {"brightness": "{{ 128 "}},
        {"if": "{{ broken ", "then": [{"stop": "done"}]},
    ],
)
def test_original_jinja_is_syntax_checked(hass, action):
    with pytest.raises(ValueError, match="invalid Home Assistant action sequence"):
        rules._validate_script_sequence([action])
    assert rules._validate_script_sequence(
        [{"variables": {"x": "{{ runtime_only | int }}"}}]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("capture", ["123456", "True", "[1, 2]", "{'a': 1}"])
async def test_captures_reach_real_native_script_as_strings(hass, capture):
    hass.loop = asyncio.get_running_loop()
    hass.async_create_task_internal.side_effect = lambda coro, **kwargs: (
        asyncio.create_task(coro)
    )
    rule = local_rule("Capture", ["remember {value}"], "sentence_pattern")
    rule["action"]["actions"] = [
        {"variables": {"captured": "{value}"}},
        {"set_conversation_response": "{{ captured ~ ':' ~ (captured is string) }}"},
    ]
    manager = rules.RequestRules(MemoryStore({"rules": [rule]}))
    await manager.async_initialize()
    result = await rules.async_evaluate_rule(
        hass, manager, rules.RequestRuleRuntime(), "remember " + capture, "audit"
    )
    assert result.successful
    assert result.response == capture + ":True"


def test_fuzzy_comparisons_charge_shared_budget():
    budget = MatchBudget(maximum=200)
    assert rules._fuzzy_score("turn on lamp", "turn on lamp", "contains", budget) == 100
    with pytest.raises(SentenceMatchLimitError):
        for _ in range(20):
            rules._fuzzy_score("turn on lamp", "turn of lamp", "contains", budget)


@pytest.mark.asyncio
async def test_exported_template_actions_round_trip_on_loop(hass, monkeypatch):
    rule = local_rule()
    rule["conditions"] = [{"condition": "template", "value_template": "{{ true }}"}]
    rule["action"]["actions"] = [
        {"delay": "{{ runtime_delay }}"},
        {"set_conversation_response": "{{ runtime_response }}"},
    ]
    manager = rules.RequestRules(MemoryStore({"rules": [rule]}))
    await manager.async_initialize()
    exported = export_rule_pack(manager, "all")
    prepared = await asyncio.to_thread(prepare_rule_pack, deepcopy(exported))
    reviewed = validate_rule_pack(prepared)
    target = rules.RequestRules(MemoryStore({"rules": []}))
    await target.async_initialize()
    imported = await async_append_rule_pack(
        target, reviewed, expected_revision=target.revision()
    )
    assert imported["rules"][0]["enabled"] is False
    assert imported["rules"][0]["conditions"] == reviewed["rules"][0]["conditions"]
    from unittest.mock import AsyncMock

    from tests.test_management_rule_pack_coverage import _management_request

    monkeypatch.setattr(
        management_ui, "async_get_request_rules", AsyncMock(return_value=target)
    )
    monkeypatch.setattr(management_ui, "_validate_request_rule_conditions", AsyncMock())
    hass.async_add_executor_job.side_effect = asyncio.to_thread
    request = _management_request(hass, "rule_pack_review", pack=exported)
    request.subentry.data = {"functions": []}
    review = await management_ui.async_request_rules_command(request)
    request.message.update(
        action="rule_pack_import",
        confirm=True,
        revision=review["revision"],
        review_token=review["review_token"],
    )
    result = await management_ui.async_request_rules_command(request)
    assert result["rules"][0]["enabled"] is False
