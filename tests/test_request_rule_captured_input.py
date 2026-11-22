"""Captured AI handoff and backup contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
    async_request_rule_match_preview,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRuleRuntime,
    RequestRules,
    async_evaluate_rule,
    validate_rule,
)
from homeassistant.helpers.typing import UNDEFINED
from tests.test_request_rules import FakeServices, MemoryStore, local_rule


def captured_routing_rule():
    rule = local_rule("Deep think", ["deep think {question}"], "sentence_pattern")
    rule["action_type"] = "model_routing"
    rule["action"] = {
        "model": "gpt-5",
        "reasoning_effort": "",
        "scope": "request",
        "reset": False,
        "continue_to_ai": True,
    }
    rule["ai_input_mode"] = "capture"
    rule["ai_input_capture"] = "question"
    return rule


def test_captured_ai_input_requires_every_variant_and_branch() -> None:
    rule = captured_routing_rule()
    assert validate_rule(rule)["ai_input_capture"] == "question"
    rule["phrases"].append("think carefully")
    with pytest.raises(ValueError, match=r"every trigger|same slots"):
        validate_rule(rule)
    rule["phrases"] = ["deep think [{question}]"]
    with pytest.raises(ValueError, match="every match"):
        validate_rule(rule)
    rule["phrases"] = ["deep think {question}"]
    rule["ai_input_capture"] = "stale"
    with pytest.raises(ValueError, match="every trigger"):
        validate_rule(rule)


@pytest.mark.parametrize(
    ("pattern", "text", "provider_input"),
    [
        (
            "deep think {question}",
            "deep think why is the sky blue?",
            "why is the sky blue?",
        ),
        ("deep think {question} then sky", "deep think sky then sky?", "sky"),
        ("deep think sky then {question}", "deep think sky then sky？", "sky？"),
    ],
)
async def test_captured_ai_handoff_and_preview_share_resolved_input(
    hass, pattern, text, provider_input
) -> None:
    rule = captured_routing_rule()
    rule["phrases"] = [pattern]
    stored = RequestRules(MemoryStore({"rules": [rule]}))
    await stored.async_initialize()
    evaluated = await async_evaluate_rule(
        hass, stored, RequestRuleRuntime(), text, "session"
    )
    preview = await async_request_rule_match_preview(hass, stored, text)
    assert evaluated is not None and not evaluated.consume
    assert evaluated.provider_input == provider_input
    assert preview["matched_rules"][0]["ai_input"] == {
        "mode": "capture",
        "capture": "question",
        "provider_input": evaluated.provider_input,
    }


async def test_captured_ai_input_survives_backup_and_old_rules_default_original() -> (
    None
):
    stored = RequestRules(MemoryStore({"rules": [captured_routing_rule()]}))
    await stored.async_initialize()
    backup = await stored.async_backup_data()
    restored = RequestRules.validate_backup_data(backup)
    assert restored["rules"][0]["ai_input_mode"] == "capture"
    assert restored["rules"][0]["ai_input_capture"] == "question"
    old = RequestRules.validate_backup_data({"rules": [local_rule()]})
    assert old["rules"][0]["ai_input_mode"] == "original"
    assert old["rules"][0]["ai_input_capture"] is None


async def test_local_captured_handoff_only_after_success(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    class FakeScript:
        def __init__(self, home_assistant, sequence, *_args, **_kwargs):
            self.hass = home_assistant
            self.sequence = sequence

        async def async_run(self, _variables, _context=None):
            variables = dict(_variables)
            for action in self.sequence:
                if "variables" in action:
                    values = action["variables"]
                    variables.update(
                        values.async_simple_render(variables)
                        if hasattr(values, "async_simple_render")
                        else values
                    )
                    continue
                domain, service = action["action"].split(".", 1)
                await self.hass.services.async_call(domain, service)
            return SimpleNamespace(variables=variables, conversation_response=UNDEFINED)

        async def async_unload(self):
            pass

    async def validate_actions(_hass, actions):
        return actions

    monkeypatch.setattr(request_rules, "Script", FakeScript)
    monkeypatch.setattr(
        request_rules, "async_validate_actions_config", validate_actions
    )
    rule = local_rule("Ask", ["ask {question}"], "sentence_pattern")
    rule["action"]["continue_to_ai"] = True
    rule["ai_input_mode"] = "capture"
    rule["ai_input_capture"] = "question"
    stored = RequestRules(MemoryStore({"rules": [rule]}))
    await stored.async_initialize()
    services = FakeServices()
    hass.services = services
    success = await async_evaluate_rule(
        hass, stored, RequestRuleRuntime(), "ask why", "session"
    )
    assert success is not None and not success.consume
    assert success.provider_input == "why"
    assert len(services.calls) == 1
    hass.services = FakeServices(fail=True)
    failed = await async_evaluate_rule(
        hass, stored, RequestRuleRuntime(), "ask why", "session"
    )
    assert failed is not None and failed.consume and not failed.successful
    assert failed.provider_input is None
