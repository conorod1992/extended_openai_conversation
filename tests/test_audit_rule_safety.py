"""Saved dependency, bounded matching and native template regression tests."""

import asyncio
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses import (
    request_rules as rules,
)
from custom_components.extended_openai_conversation_responses.function_dependency_integrity import (
    async_validate_request_rule_functions,
)
from custom_components.extended_openai_conversation_responses.request_rule_patterns import (
    MAX_MATCH_INPUT_CHARS,
    MAX_MATCH_INPUT_WORDS,
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


@pytest.mark.parametrize("text", ["x" * (MAX_MATCH_INPUT_CHARS + 1), "x " * (MAX_MATCH_INPUT_WORDS + 1)])
async def test_legacy_fuzzy_settings_cannot_bypass_matching_input_bounds(text):
    manager = rules.RequestRules(MemoryStore({
        "defaults": {**rules.DEFAULT_MATCHING, "fuzzy": True, "fuzzy_threshold": 70},
        "rules": [local_rule(phrases=["turn on lamp"])],
    }))
    await manager.async_initialize()
    assert manager.match("turn on lamp") is not None
    with pytest.raises(SentenceMatchLimitError, match="at most"):
        manager.match(text)
