"""Voice fallback and runtime-generation cleanup regressions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.function_groups import (
    FunctionGroupRuntime,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRuleRuntime,
    request_rule_session_id,
)
from custom_components.extended_openai_conversation_responses.scope import (
    UNRETAINED_SCOPE_ID,
    resolve_data_scope,
)
from custom_components.extended_openai_conversation_responses.voice_identity_runtime import (
    voice_identity_scope,
)


@pytest.mark.parametrize(
    "device,mappings",
    [(None, {}), (None, []), ("unknown", {}), ("kitchen", {"kitchen": "user:deleted"})],
)
@pytest.mark.parametrize(
    "fallback,expected",
    [("shared", "shared:household"), ("default_user", "user:alice")],
)
async def test_unselectable_owner_uses_validated_fallback(
    device, mappings, fallback, expected
):
    options = {
        "voice_scope_policy": "device_mapping",
        "voice_device_mappings": mappings,
        "voice_unmapped_policy": fallback,
        "voice_default_user_id": "user:alice",
    }
    lookup = AsyncMock(
        side_effect=lambda uid: (
            SimpleNamespace(is_active=True) if uid == "alice" else None
        )
    )
    agent = SimpleNamespace(
        subentry=SimpleNamespace(data=options),
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=lookup)),
    )
    request = SimpleNamespace(
        device_id=device, satellite_id=None, context=SimpleNamespace(user_id=None)
    )
    async with voice_identity_scope(agent, request):
        assert resolve_data_scope(request, options).scope_id == expected
    assert all(call.args[0] != "user:alice" for call in lookup.await_args_list)


async def test_inactive_default_stays_unretained_and_explicit_assignment_wins():
    options = {
        "voice_scope_policy": "device_mapping",
        "voice_unmapped_policy": "default_user",
        "voice_default_user_id": "user:alice",
        "voice_device_mappings": {},
    }
    lookup = AsyncMock(return_value=SimpleNamespace(is_active=False))
    agent = SimpleNamespace(
        subentry=SimpleNamespace(data=options),
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=lookup)),
    )
    request = SimpleNamespace(
        device_id=None, satellite_id=None, context=SimpleNamespace(user_id=None)
    )
    async with voice_identity_scope(agent, request):
        assert resolve_data_scope(request, options).scope_id == UNRETAINED_SCOPE_ID
    options["voice_device_mappings"] = {"kitchen": UNRETAINED_SCOPE_ID}
    request.device_id = "kitchen"
    lookup.reset_mock()
    async with voice_identity_scope(agent, request):
        assert resolve_data_scope(request, options).scope_id == UNRETAINED_SCOPE_ID
    lookup.assert_not_awaited()


def test_ending_continuity_invalidates_generations_without_touching_other_owners():
    rules = RequestRuleRuntime()
    groups = FunctionGroupRuntime()
    own = [request_rule_session_id("owner", id) for id in ("old", "new")]
    other = request_rule_session_id("owner-longer", "other")
    tokens = []
    for key in [*own, other]:
        rules.set(key, {"chat_model": "gpt-4.1"})
        tokens.append(rules.begin_request(key, 5))
        groups.begin(key, 5)
    rules.reset_continuity("owner")
    groups.end_continuity("owner")
    assert all(rules.get(key) == {} for key in own)
    assert rules.get(other) == {"chat_model": "gpt-4.1"}
    assert set(groups._sessions) == {other}
    assert rules._requests.owners == {other}


def test_continuity_cleanup_handles_delimiter_in_conversation_and_owner_ids():
    rules = RequestRuleRuntime()
    groups = FunctionGroupRuntime()
    owner = "device:owner:conversation:custom"
    own = request_rule_session_id(owner, "external:conversation:identifier")
    sibling = request_rule_session_id(
        owner + "-other", "external:conversation:identifier"
    )
    for key in [own, sibling]:
        rules.set(key, {"chat_model": "gpt-4.1"})
        groups.begin(key, 5)
    rules.reset_continuity(owner)
    groups.end_continuity(owner)
    assert rules.get(own) == {} and set(groups._sessions) == {sibling}
    assert rules.get(sibling) == {"chat_model": "gpt-4.1"}


@pytest.mark.parametrize("continuation", [False, True])
@pytest.mark.parametrize("reset_first", [False, True])
async def test_ended_conversation_cannot_be_recreated_by_inflight_route(
    hass, continuation, reset_first
):
    from custom_components.extended_openai_conversation_responses.request_rules import (
        RuleMatch,
        async_evaluate_rule,
    )
    from tests.test_request_rules import manager, routing_rule, validate_rule

    runtime = RequestRuleRuntime()
    session_id = request_rule_session_id("owner", "old")
    token = runtime.begin_request(session_id, 5)
    if reset_first:
        runtime.reset(session_id)
    rule = routing_rule()
    rule["action"].update(scope="conversation", model="gpt-4.1", reasoning_effort=None)
    rules = await manager(rule)

    if continuation:

        async def matches(*_args):
            runtime.reset_continuity("owner")
            yield RuleMatch(validate_rule(rule), "route", False, 100.0)

        rules._has_continuation = True
        rules.async_eligible_matches = matches
    else:

        async def match(*_args):
            runtime.reset_continuity("owner")
            return RuleMatch(validate_rule(rule), "route", False, 100.0)

        rules.async_match = match

    evaluation = await async_evaluate_rule(hass, rules, runtime, "route", session_id)
    assert evaluation.request_override == {"chat_model": "gpt-4.1"}
    assert runtime.get(session_id) == {}
    assert not runtime._requests.tokens(session_id)


async def test_ended_request_reset_cannot_clear_a_new_request_route(hass):
    from custom_components.extended_openai_conversation_responses.request_rules import (
        RuleMatch,
        async_evaluate_rule,
    )
    from tests.test_request_rules import manager, routing_rule, validate_rule

    runtime = RequestRuleRuntime()
    session_id = request_rule_session_id("owner", "reused")
    runtime.begin_request(session_id, 5)
    rule = routing_rule(scope="conversation", reset=True)
    rules = await manager(rule)

    async def match(*_args):
        runtime.reset_continuity("owner")
        runtime.set(session_id, {"chat_model": "gpt-4.1"})
        return RuleMatch(validate_rule(rule), "reset", False, 100.0)

    rules.async_match = match
    evaluation = await async_evaluate_rule(hass, rules, runtime, "reset", session_id)
    assert evaluation.request_override
    assert runtime.effective_options(
        {"chat_model": "gpt-4"}, session_id, evaluation.request_override
    ) == {"chat_model": "gpt-4"}
    assert runtime.get(session_id) == {"chat_model": "gpt-4.1"}
