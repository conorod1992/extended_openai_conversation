"""Side-effect-free Request Rule management preview tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    request_rule_match_preview as preview_module,
)
from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
    request_rule_match_preview,
    wrap_management_command,
)
from custom_components.extended_openai_conversation_responses.request_rules import RuleMatch
from homeassistant.exceptions import HomeAssistantError


def _local_match() -> RuleMatch:
    return RuleMatch(
        rule={
            "id": "good-night",
            "name": "Good night",
            "match_type": "sentence_pattern",
            "action_type": "local_action",
            "action": {"actions": [{"action": "script.turn_on"}, {"delay": 1}]},
        },
        phrase="good night {room}",
        fuzzy=False,
        score=100.0,
        slots={"room": "kitchen"},
    )


def test_preview_summarizes_match_without_action_payloads() -> None:
    result = request_rule_match_preview(_local_match())
    assert result == {
        "matched": True,
        "rule": {
            "id": "good-night",
            "name": "Good night",
            "match_type": "sentence_pattern",
            "action_type": "local_action",
        },
        "matched_phrase": "good night {room}",
        "fuzzy": False,
        "score": 100.0,
        "captured_values": {"room": "kitchen"},
        "would_do": {"type": "local_action", "action_count": 2},
    }
    assert "actions" not in result["would_do"]


def test_preview_summarizes_model_routing_and_no_match() -> None:
    match = RuleMatch(
        rule={
            "id": "think",
            "name": "Think carefully",
            "match_type": "starts_with",
            "action_type": "model_routing",
            "action": {
                "reset": False,
                "model": "gpt-5",
                "reasoning_effort": "high",
                "scope": "conversation",
            },
        },
        phrase="think carefully",
        fuzzy=True,
        score=93.47,
    )
    assert request_rule_match_preview(match)["would_do"] == {
        "type": "model_routing",
        "reset": False,
        "model": "gpt-5",
        "reasoning_effort": "high",
        "scope": "conversation",
    }
    assert request_rule_match_preview(match)["score"] == 93.5
    assert request_rule_match_preview(None) == {"matched": False}


@pytest.mark.parametrize("action", ["test", "test_match"])
async def test_management_test_actions_never_delegate_to_real_processing(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    delegated = 0
    matched_text: list[str] = []

    async def original(*_args, **_kwargs):
        nonlocal delegated
        delegated += 1
        raise AssertionError("real management processing must not run")

    class Rules:
        def match(self, text: str):
            matched_text.append(text)
            return _local_match()

    monkeypatch.setattr(
        preview_module.management_ui,
        "entry_and_agent",
        lambda _hass, _entry_id, _subentry_id: (object(), object()),
    )

    async def get_rules(_hass, _entry_id, _subentry_id):
        return Rules()

    monkeypatch.setattr(preview_module, "async_get_request_rules", get_rules)
    wrapped = wrap_management_command(original)
    hass = SimpleNamespace(
        services=SimpleNamespace(
            async_call=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("Home Assistant services must not be called")
            )
        )
    )
    result = await wrapped(
        hass,
        "admin-user",
        True,
        {
            "section": "request_rules",
            "action": action,
            "entry_id": "entry",
            "subentry_id": "agent",
            "text": "  good night kitchen  ",
        },
    )
    assert result["matched"] is True
    assert result["rule"]["name"] == "Good night"
    assert matched_text == ["good night kitchen"]
    assert delegated == 0


async def test_match_preview_requires_admin_and_text() -> None:
    async def original(*_args, **_kwargs):
        raise AssertionError

    wrapped = wrap_management_command(original)
    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await wrapped(
            SimpleNamespace(),
            "user",
            False,
            {
                "section": "request_rules",
                "action": "test_match",
                "entry_id": "entry",
                "subentry_id": "agent",
                "text": "hello",
            },
        )
