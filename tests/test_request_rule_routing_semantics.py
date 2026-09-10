"""Request Rule routing and ordering regressions."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
)
from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
    request_rule_match_preview,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    RequestRuleRuntime,
    RequestRules,
    RuleMatch,
    _REQUEST_RESET_SENTINEL,
    async_evaluate_rule,
    validate_rule,
)
from homeassistant.exceptions import HomeAssistantError


class MemoryStore:
    def __init__(self, data=None) -> None:
        self.data = deepcopy(data)

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data) -> None:
        self.data = deepcopy(data)


def _rule(
    rule_id: str,
    phrase: str,
    *,
    match_type: str = "equals",
    action: dict | None = None,
    order: int = 0,
) -> dict:
    return {
        "id": rule_id,
        "name": rule_id,
        "enabled": True,
        "phrases": [phrase],
        "match_type": match_type,
        "action_type": "model_routing",
        "action": action
        or {
            "model": "gpt-5.6",
            "reasoning_effort": None,
            "scope": "conversation" if match_type in {"equals", "sentence_pattern"} else "request",
            "reset": False,
            "success_response": "Updated",
        },
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": order,
    }


def test_standalone_routing_rejects_request_scope_even_for_reset() -> None:
    for match_type in ("equals", "sentence_pattern"):
        for reset in (False, True):
            rule = _rule(
                f"{match_type}-{reset}",
                "route now" if match_type == "equals" else "route {model}",
                match_type=match_type,
                action={
                    "model": None if reset else "gpt-5.6",
                    "reasoning_effort": None,
                    "scope": "request",
                    "reset": reset,
                    "continue_to_ai": False,
                    "success_response": "Updated",
                },
            )
            with pytest.raises(ValueError, match="Continue to AI"):
                validate_rule(rule)


async def test_saved_complete_request_scope_is_migrated_not_discarded() -> None:
    stored_rule = _rule(
        "legacy-exact",
        "route now",
        action={
            "model": "gpt-5.6",
            "reasoning_effort": None,
            "scope": "request",
            "reset": False,
            "success_response": "Updated",
        },
    )
    store = MemoryStore({"rules": [stored_rule]})
    manager = RequestRules(store)

    await manager.async_initialize()

    saved = manager.snapshot()["rules"]
    assert len(saved) == 1
    assert saved[0]["id"] == "legacy-exact"
    assert saved[0]["action"]["scope"] == "conversation"
    assert store.data["rules"][0]["action"]["scope"] == "conversation"

    restored = RequestRules.validate_backup_data({"rules": [stored_rule]})
    assert restored["rules"][0]["action"]["scope"] == "conversation"


def test_broad_request_only_reset_bypasses_but_does_not_clear_conversation_route() -> None:
    runtime = RequestRuleRuntime()
    session = "conversation:test"
    runtime.set(
        session,
        {CONF_CHAT_MODEL: "gpt-6-astra", CONF_REASONING_EFFORT: "xhigh"},
    )

    reset = {_REQUEST_RESET_SENTINEL: "1"}
    reset_options = runtime.effective_options(
        {CONF_CHAT_MODEL: "gpt-5.6", CONF_REASONING_EFFORT: "medium"},
        session,
        reset,
    )
    assert reset_options[CONF_CHAT_MODEL] == "gpt-5.6"
    assert reset_options[CONF_REASONING_EFFORT] == "medium"

    next_options = runtime.effective_options(
        {CONF_CHAT_MODEL: "gpt-5.6", CONF_REASONING_EFFORT: "medium"},
        session,
    )
    assert next_options[CONF_CHAT_MODEL] == "gpt-6-astra"
    assert next_options[CONF_REASONING_EFFORT] == "xhigh"


async def test_broad_request_reset_evaluation_keeps_saved_override() -> None:
    rule = _rule(
        "reset-one",
        "default for",
        match_type="starts_with",
        action={
            "model": None,
            "reasoning_effort": None,
            "scope": "request",
            "reset": True,
            "continue_to_ai": True,
            "success_response": "Using defaults",
        },
    )
    match = RuleMatch(rule, "default for", False, 100.0)

    class Rules:
        async def async_match(self, _hass, _text):
            return match

    runtime = RequestRuleRuntime()
    runtime.set("session", {CONF_CHAT_MODEL: "gpt-6-astra"})
    evaluation = await async_evaluate_rule(
        SimpleNamespace(), Rules(), runtime, "default for this", "session", "gpt-5.6"
    )
    assert evaluation is not None
    assert evaluation.consume is False
    assert evaluation.request_override == {_REQUEST_RESET_SENTINEL: "1"}
    assert runtime.get("session")[CONF_CHAT_MODEL] == "gpt-6-astra"


def test_astra_reasoning_values_are_model_specific() -> None:
    for effort in ("xhigh", "max"):
        assert validate_rule(
            _rule(
                effort,
                effort,
                action={
                    "model": "gpt-6-astra",
                    "reasoning_effort": effort,
                    "scope": "conversation",
                    "reset": False,
                    "success_response": "Updated",
                },
            )
        )["action"]["reasoning_effort"] == effort

        with pytest.raises(ValueError, match="not supported by model"):
            validate_rule(
                _rule(
                    f"bad-{effort}",
                    effort,
                    action={
                        "model": "gpt-5.6",
                        "reasoning_effort": effort,
                        "scope": "conversation",
                        "reset": False,
                        "success_response": "Updated",
                    },
                )
            )


async def test_captured_reasoning_uses_effective_routed_model_before_publish() -> None:
    runtime = RequestRuleRuntime()
    runtime.set("session", {CONF_CHAT_MODEL: "gpt-5.6"})
    rule = _rule(
        "dynamic",
        "route {model} {effort}",
        match_type="sentence_pattern",
        action={
            "model": "{model}",
            "reasoning_effort": "{effort}",
            "scope": "conversation",
            "reset": False,
            "continue_to_ai": False,
            "success_response": "Updated",
        },
    )

    class Rules:
        def __init__(self, match):
            self.match = match

        async def async_match(self, _hass, _text):
            return self.match

    good = RuleMatch(
        rule, "route {model} {effort}", False, 100.0, {"model": "gpt-6-astra", "effort": "max"}
    )
    evaluation = await async_evaluate_rule(
        SimpleNamespace(), Rules(good), runtime, "route gpt-6-astra max", "session", "gpt-5.6"
    )
    assert evaluation is not None and evaluation.consume is True
    assert runtime.get("session") == {
        CONF_CHAT_MODEL: "gpt-6-astra",
        CONF_REASONING_EFFORT: "max",
    }

    runtime.set("other", {CONF_CHAT_MODEL: "gpt-6-astra"})
    bad = RuleMatch(
        rule, "route {model} {effort}", False, 100.0, {"model": "gpt-5.6", "effort": "max"}
    )
    before = runtime.get("other")
    with pytest.raises(HomeAssistantError, match="not supported by model gpt-5.6"):
        await async_evaluate_rule(
            SimpleNamespace(), Rules(bad), runtime, "route gpt-5.6 max", "other", "gpt-5.6"
        )
    assert runtime.get("other") == before


async def test_changing_model_revalidates_existing_conversation_reasoning() -> None:
    runtime = RequestRuleRuntime()
    runtime.set(
        "session",
        {CONF_CHAT_MODEL: "gpt-6-astra", CONF_REASONING_EFFORT: "max"},
    )
    rule = _rule(
        "model-only",
        "use older model",
        action={
            "model": "gpt-5.6",
            "reasoning_effort": None,
            "scope": "conversation",
            "reset": False,
            "continue_to_ai": False,
            "success_response": "Updated",
        },
    )

    class Rules:
        async def async_match(self, _hass, _text):
            return RuleMatch(rule, "use older model", False, 100.0)

    before = runtime.get("session")
    with pytest.raises(HomeAssistantError, match="not supported by model gpt-5.6"):
        await async_evaluate_rule(
            SimpleNamespace(), Rules(), runtime, "use older model", "session", "gpt-5.6"
        )
    assert runtime.get("session") == before


async def test_duplicate_is_inserted_immediately_after_source_before_renumbering() -> None:
    rules = [
        _rule("alpha", "same", match_type="contains", order=0),
        _rule("beta", "same", match_type="contains", order=1),
        _rule("gamma", "same", match_type="contains", order=2),
    ]
    manager = RequestRules(MemoryStore({"rules": rules}))
    await manager.async_initialize()
    duplicate = await manager.async_duplicate("alpha")
    snapshot = manager.snapshot()["rules"]
    assert [item["id"] for item in snapshot] == ["alpha", duplicate["id"], "beta", "gamma"]
    assert [item["order"] for item in snapshot] == [0, 1, 2, 3]
    assert manager.match("same").rule["id"] == "alpha"


def test_preview_exposes_consumed_vs_original_provider_input() -> None:
    exact = _rule("exact", "route now")
    exact_preview = request_rule_match_preview(RuleMatch(exact, "route now", False, 100.0))
    assert exact_preview["would_do"]["consumed"] is True
    assert exact_preview["would_do"]["provider_input"] == "none"

    broad = _rule("broad", "route", match_type="starts_with")
    broad_preview = request_rule_match_preview(RuleMatch(broad, "route", False, 100.0))
    assert broad_preview["would_do"]["consumed"] is False
    assert broad_preview["would_do"]["provider_input"] == "original"
