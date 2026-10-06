"""Request Rule continuation, dynamic guest authorization and cleanup contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    model_lifecycle,
    request_rules as rr,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.typing import UNDEFINED
from tests.test_request_rules import local_rule, manager


@pytest.fixture
def local_script(monkeypatch):
    monkeypatch.setattr(rr.cv, "SCRIPT_SCHEMA", lambda actions: actions)
    monkeypatch.setattr(
        rr,
        "async_validate_actions_config",
        AsyncMock(side_effect=lambda _hass, actions: actions),
    )
    script = SimpleNamespace(async_run=AsyncMock(), async_stop=AsyncMock())

    def build(_hass, actions, *_args, **_kwargs):
        marker = next(
            step["variables"] for step in reversed(actions) if "variables" in step
        )
        script.async_run.return_value = SimpleNamespace(
            variables=marker, conversation_response=UNDEFINED
        )
        script.actions = actions
        return script

    monkeypatch.setattr(rr, "Script", build)
    return script


async def _evaluate(hass, rule, **kwargs):
    return await rr._async_evaluate_matched_rule(
        hass,
        rr.RuleMatch(rule, "run", False, 100, {}),
        "run",
        rr.RequestRuleRuntime(),
        "session",
        rr.DEFAULT_CHAT_MODEL,
        None,
        10,
        None,
        None,
        **kwargs,
    )


async def test_local_action_uses_older_script_stop_and_clears_context(
    hass, local_script
):
    result = await _evaluate(hass, rr.validate_rule(local_rule()))
    assert result.successful is True
    local_script.async_run.assert_awaited_once()
    local_script.async_stop.assert_awaited_once_with()
    assert rr._ACTIVE_ACTION_GUARD.get() is None
    with pytest.raises(HomeAssistantError, match="only available"):
        await rr.async_call_active_function("outside-request", {})


@pytest.mark.parametrize("guest,allowed", [(False, True), (True, True), (True, False)])
async def test_pending_scene_rechecks_live_guest_policy(
    hass, monkeypatch, local_script, guest, allowed
):
    authorizations = Mock(return_value=allowed)
    monkeypatch.setattr(rr, "_guest_script_allowed", authorizations)

    async def run(*_args):
        rr._ACTIVE_ACTION_GUARD.get()({"scene": "scene.evening"})
        return local_script.async_run.return_value

    local_script.async_run.side_effect = run
    result = await _evaluate(
        hass,
        rr.validate_rule(local_rule()),
        live_guest_policy=lambda: GuestCapabilityPolicy(guest),
    )
    if guest:
        authorizations.assert_called_once()
        assert authorizations.call_args.args[1] == [
            {"action": "scene.turn_on", "target": {"entity_id": "scene.evening"}}
        ]
    else:
        authorizations.assert_not_called()
    assert result.successful is allowed
    local_script.async_stop.assert_awaited_once()
    assert rr._ACTIVE_ACTION_GUARD.get() is None


async def test_captured_results_with_live_policy_remain_guarded(hass, local_script):
    rule = local_rule()
    rule["action"]["actions"] = [
        {
            "action": f"{rr.DOMAIN}.call_function",
            "data": {"function": "read", "arguments": {}, "result_alias": "reading"},
        }
    ]
    result = await _evaluate(
        hass,
        rr.validate_rule(rule),
        live_guest_policy=GuestCapabilityPolicy.unrestricted,
    )
    assert result.successful
    assert any(
        step.get("action") == f"{rr.DOMAIN}.{rr._GUARD_SERVICE}"
        for step in local_script.actions
    )
    assert any(
        step.get("response_variable") == "__eoai_result_reading"
        for step in local_script.actions
    )


async def test_script_failure_rechecks_revision_and_cleans_up(hass, local_script):
    local_script.async_run.side_effect = RuntimeError("action failed")
    revision = Mock()
    result = await _evaluate(
        hass, rr.validate_rule(local_rule()), require_matching_revision=revision
    )
    assert not result.successful
    assert result.response == "Failed safely"
    assert revision.call_count == 3
    local_script.async_stop.assert_awaited_once()


@pytest.mark.parametrize(
    "error",
    [ValueError("bad format"), TypeError("bad format"), KeyError("invalid key!")],
    ids=["value", "type", "non-variable-key"],
)
async def test_response_format_failure_does_not_repeat_completed_actions(
    hass, monkeypatch, local_script, error
):
    monkeypatch.setattr(rr, "resolve_result_values", Mock(side_effect=error))
    result = await _evaluate(hass, rr.validate_rule(local_rule()))
    assert not result.successful
    assert "action execution succeeded" in result.response
    assert "invalid response formatting" in result.response
    local_script.async_run.assert_awaited_once()
    local_script.async_stop.assert_awaited_once()


@pytest.mark.parametrize("successful_first", [False, True])
async def test_sentence_budget_failure_after_match_cannot_silently_handoff(
    hass, monkeypatch, successful_first
):
    rule = local_rule()
    rule["continue_matching"] = True
    rules = await manager(rule)

    async def matches(*_args):
        if successful_first:
            yield rr.RuleMatch(rr.validate_rule(rule), "run", False, 100, {})
        raise rr.SentenceMatchLimitError("sentence budget exhausted")

    monkeypatch.setattr(rules, "async_eligible_matches", matches)
    monkeypatch.setattr(
        rr,
        "_async_evaluate_matched_rule",
        AsyncMock(
            return_value=rr.RuleEvaluation(
                rr.RuleMatch(rr.validate_rule(rule), "run", False, 100, {}),
                True,
                "Done",
            )
        ),
    )
    if successful_first:
        with pytest.raises(HomeAssistantError, match="could not safely continue"):
            await rr.async_evaluate_rule(
                hass, rules, rr.RequestRuleRuntime(), "run", "session"
            )
    else:
        assert (
            await rr.async_evaluate_rule(
                hass, rules, rr.RequestRuleRuntime(), "run", "session"
            )
            is None
        )


async def test_fuzzy_cursor_skips_rule_already_matched_strictly():
    rule = local_rule(phrases=["good night", "good nite"])
    rules = await manager(rule)
    cursor = rr._MatchCursor(rules._committed_matching_snapshot, "good night")
    assert cursor.next_match().rule["id"] == rule["id"]
    assert cursor.next_match() is None


async def test_request_rule_manager_without_loaded_entry_skips_lifecycle(
    hass, monkeypatch
):
    rules = await manager()
    hass.data[rr._MANAGERS] = {("entry", "agent"): rules}
    hass.config_entries.async_get_entry.return_value = None
    reconcile = Mock()
    monkeypatch.setattr(model_lifecycle, "sync_entry_model_lifecycle", reconcile)
    assert await rr.async_get_request_rules(hass, "entry", "agent") is rules
    reconcile.assert_not_called()


async def test_instrumented_matcher_skips_failed_conditions_without_repeating_rule(
    hass, monkeypatch
):
    first, second = local_rule("First"), local_rule("Second")
    rules = await manager(first, second)
    matches = [
        rr.RuleMatch(rr.validate_rule(rule), "run", False, 100, {})
        for rule in (first, second)
    ]
    matching = Mock(side_effect=matches)
    monkeypatch.setattr(rules, "match", matching)
    monkeypatch.setattr(
        rules, "_async_conditions_pass", AsyncMock(side_effect=[False, True])
    )
    found, skipped = await rules.async_match_with_skipped(hass, "run")
    assert found.rule["id"] == second["id"]
    assert skipped == [
        {"id": first["id"], "name": first["name"], "reason": "conditions_false"}
    ]
    assert matching.call_args_list[1].args == ("run", frozenset({first["id"]}))


@pytest.mark.parametrize("reset", [False, True])
@pytest.mark.parametrize("field", [rr.CONF_CHAT_MODEL, rr.CONF_REASONING_EFFORT])
async def test_continued_conversation_route_reconciles_request_overrides(
    hass, reset, field
):
    effort = rr.get_reasoning_effort_options("gpt-6-astra")[0]

    def route(name, scope, *, reset=False, model=None, reasoning=None, handoff=False):
        rule = local_rule(name, phrases=["run"])
        rule.update(
            action_type="model_routing",
            continue_matching=True,
            order=0 if scope == "request" else 1,
        )
        rule["action"] = {
            "scope": scope,
            "reset": reset,
            "model": model,
            "reasoning_effort": reasoning,
            "continue_to_ai": handoff,
            "success_response": "Routed",
        }
        return rule

    first = route(
        "Request",
        "request",
        reset=reset,
        model=None if reset else "gpt-6-astra",
        reasoning=None if reset else effort,
    )
    second = route(
        "Conversation",
        "conversation",
        model="gpt-6-astra" if field == rr.CONF_CHAT_MODEL else None,
        reasoning=effort if field == rr.CONF_REASONING_EFFORT else None,
        handoff=True,
    )
    runtime = rr.RequestRuleRuntime()
    rules = await manager(first, second)
    evaluation = await rr.async_evaluate_rule(
        hass, rules, runtime, "run", "session", configured_model="gpt-6-astra"
    )
    assert evaluation is not None and not evaluation.consume
    expected = "gpt-6-astra" if field == rr.CONF_CHAT_MODEL else effort
    assert runtime.get("session", 30)[field] == expected
    if reset:
        assert evaluation.request_override[rr._REQUEST_RESET_SENTINEL] == "1"
        assert evaluation.request_override[field] == expected
    else:
        assert field not in (evaluation.request_override or {})
