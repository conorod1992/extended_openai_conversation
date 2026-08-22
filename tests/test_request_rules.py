"""Request Rule matching, persistence, execution, and routing tests."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GUEST_MODE_UNAVAILABLE,
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    RequestRuleRuntime,
    RequestRules,
    async_evaluate_rule,
    canonical_action_signature,
    normalize_text,
    request_rule_session_id,
    validate_rule,
)
from homeassistant.exceptions import HomeAssistantError


class MemoryStore:
    def __init__(self, data=None):
        self.data = deepcopy(data)
        self.saves = 0

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)
        self.saves += 1


def local_rule(
    name: str = "Good night",
    phrases=None,
    match_type: str = "equals",
    *,
    enabled: bool = True,
    order: int = 0,
    behavior: str = "defaults",
    matching=None,
):
    return {
        "id": name.casefold().replace(" ", "-"),
        "name": name,
        "enabled": enabled,
        "phrases": phrases or ["good night"],
        "match_type": match_type,
        "action_type": "local_action",
        "action": {
            "actions": [
                {
                    "domain": "script",
                    "service": "turn_on",
                    "target": {"entity_id": ["script.goodnight"]},
                    "data": {},
                }
            ],
            "success_response": "Done",
            "failure_response": "Failed safely",
        },
        "matching_behavior": behavior,
        "matching": matching or dict(DEFAULT_MATCHING),
        "order": order,
    }


async def manager(*rules, defaults=None):
    result = RequestRules(
        MemoryStore({"defaults": defaults or dict(DEFAULT_MATCHING), "rules": rules})
    )
    await result.async_initialize()
    return result


@pytest.mark.parametrize(
    ("match_type", "text", "phrase"),
    [
        ("equals", "Good night!", "good night"),
        ("starts_with", "Think carefully about this", "think carefully"),
        ("ends_with", "Please do it downstairs", "downstairs"),
        (
            "contains",
            "Could you turn everything downstairs off please",
            "everything downstairs off",
        ),
    ],
)
async def test_match_types_case_punctuation_and_whitespace(
    match_type, text, phrase
) -> None:
    rules = await manager(local_rule(phrases=[phrase], match_type=match_type))
    assert rules.match(f"  {text}  ") is not None


def test_normalization_is_conservative_and_predictable() -> None:
    settings = dict(DEFAULT_MATCHING)
    assert normalize_text("LIGHTS, reminders!", settings) == "light reminder"
    assert normalize_text("Switch   on the television", settings) == "turn on the tv"
    assert normalize_text("turn-down lights", settings) == "decrease light"
    assert normalize_text("news series species", settings) == "news series species"
    without_forms = {**settings, "word_forms": False}
    assert normalize_text("lights", without_forms) == "lights"


def test_session_identity_uses_continuity_or_actual_chat_log_id() -> None:
    assert request_rule_session_id("device:kitchen", "core-id") == (
        "continuity:device:kitchen"
    )
    assert request_rule_session_id(None, "core-created-id") == (
        "conversation:core-created-id"
    )


async def test_multiple_phrases_and_curated_wording_alternatives() -> None:
    rules = await manager(local_rule(phrases=["turn on the tv", "power up the tv"]))
    match = rules.match("Switch on the television")
    assert match is not None
    assert match.phrase == "turn on the tv"


async def test_disabled_rules_never_participate() -> None:
    rules = await manager(local_rule(enabled=False))
    assert rules.match("good night") is None


async def test_global_defaults_and_per_rule_override() -> None:
    defaults = {**DEFAULT_MATCHING, "word_forms": False}
    default_rule = local_rule(name="Default", phrases=["light"], order=0)
    custom = local_rule(
        name="Custom",
        phrases=["light"],
        order=1,
        behavior="custom",
        matching={**DEFAULT_MATCHING, "word_forms": True},
    )
    rules = await manager(default_rule, custom, defaults=defaults)
    assert rules.match("lights").rule["name"] == "Custom"


async def test_fuzzy_is_fallback_and_threshold_boundary() -> None:
    fuzzy = {**DEFAULT_MATCHING, "fuzzy": True, "fuzzy_threshold": 90}
    rules = await manager(
        local_rule(
            name="Fuzzy", phrases=["good night"], matching=fuzzy, behavior="custom"
        )
    )
    match = rules.match("good nigt")
    assert match is not None and match.fuzzy
    strict = await manager(
        local_rule(
            name="Too strict",
            phrases=["good night"],
            matching={**fuzzy, "fuzzy_threshold": 96},
            behavior="custom",
        )
    )
    assert strict.match("good nigt") is None


async def test_strict_match_wins_over_fuzzy_and_equals_beats_contains() -> None:
    fuzzy = {**DEFAULT_MATCHING, "fuzzy": True, "fuzzy_threshold": 70}
    rules = await manager(
        local_rule(
            name="Fuzzy early",
            phrases=["good knight"],
            matching=fuzzy,
            behavior="custom",
            order=0,
        ),
        local_rule(
            name="Contains", phrases=["good night"], match_type="contains", order=1
        ),
        local_rule(name="Exact", phrases=["good night"], match_type="equals", order=2),
    )
    match = rules.match("good night")
    assert match is not None
    assert match.rule["name"] == "Exact"
    assert match.fuzzy is False


async def test_crud_duplicate_and_persistence_round_trip() -> None:
    store = MemoryStore()
    rules = RequestRules(store)
    await rules.async_initialize()
    created = await rules.async_create(local_rule())
    updated = await rules.async_update(created["id"], {**created, "enabled": False})
    assert updated["enabled"] is False
    duplicate = await rules.async_duplicate(created["id"])
    assert duplicate["id"] != created["id"]
    assert duplicate["name"].endswith("copy")
    reloaded = RequestRules(store)
    await reloaded.async_initialize()
    assert len(reloaded.snapshot()["rules"]) == 2
    assert await reloaded.async_delete(created["id"])
    assert store.saves >= 4


async def test_backward_compatibility_ignores_invalid_stored_rules() -> None:
    rules = RequestRules(
        MemoryStore({"rules": [{"old": "unsupported"}], "defaults": {"bad": True}})
    )
    await rules.async_initialize()
    assert rules.snapshot()["rules"] == []
    assert rules.snapshot()["defaults"] == DEFAULT_MATCHING


class FakeServices:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def has_service(self, domain, service):
        return True

    async def async_call(self, domain, service, **kwargs):
        self.calls.append((domain, service, kwargs))
        if self.fail:
            raise HomeAssistantError("boom")


@pytest.mark.parametrize("fail", [False, True])
async def test_multiple_local_actions_and_failure_response(fail) -> None:
    rule = local_rule()
    rule["action"]["actions"].append(
        {
            "domain": "light",
            "service": "turn_off",
            "target": {"area_id": ["downstairs"]},
            "data": {},
        }
    )
    rules = await manager(rule)
    services = FakeServices(fail=fail)
    hass = SimpleNamespace(services=services)
    result = await async_evaluate_rule(
        hass, rules, RequestRuleRuntime(), "good night", "conversation:one"
    )
    assert result is not None and result.consume
    assert result.response == ("Failed safely" if fail else "Done")
    assert len(services.calls) == (1 if fail else 2)


async def test_guest_mode_prevalidates_entire_local_action_sequence(
    monkeypatch,
) -> None:
    rule = local_rule()
    rule["action"]["actions"].append(
        {
            "domain": "light",
            "service": "turn_off",
            "target": {"area_id": ["kitchen"]},
            "data": {},
        }
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.guest_mode.target_helpers.async_extract_referenced_entity_ids",
        lambda _hass, _selection: SimpleNamespace(
            referenced=set(),
            indirectly_referenced={"light.kitchen_ceiling", "light.kitchen_cabinet"},
        ),
    )
    rules = await manager(rule)
    services = FakeServices()
    policy = GuestCapabilityPolicy(
        True,
        readable_entity_ids=frozenset({"script.goodnight", "light.kitchen_ceiling"}),
        controllable_entity_ids=frozenset(
            {"script.goodnight", "light.kitchen_ceiling"}
        ),
    )
    result = await async_evaluate_rule(
        SimpleNamespace(services=services),
        rules,
        RequestRuleRuntime(),
        "good night",
        "conversation:guest",
        guest_policy=policy,
    )
    assert result is not None and result.consume
    assert result.response == GUEST_MODE_UNAVAILABLE
    assert services.calls == []


async def test_guest_mode_allows_permitted_local_action_without_ai() -> None:
    rules = await manager(local_rule())
    services = FakeServices()
    policy = GuestCapabilityPolicy(
        True,
        readable_entity_ids=frozenset({"script.goodnight"}),
        controllable_entity_ids=frozenset({"script.goodnight"}),
    )
    result = await async_evaluate_rule(
        SimpleNamespace(services=services),
        rules,
        RequestRuleRuntime(),
        "good night",
        "conversation:guest",
        guest_policy=policy,
    )
    assert result is not None and result.response == "Done"
    assert len(services.calls) == 1


async def test_guest_mode_rejects_unscoped_local_control() -> None:
    rule = local_rule()
    rule["action"]["actions"][0]["target"] = {}
    services = FakeServices()
    result = await async_evaluate_rule(
        SimpleNamespace(services=services),
        await manager(rule),
        RequestRuleRuntime(),
        "good night",
        "conversation:guest",
        guest_policy=GuestCapabilityPolicy(True),
    )
    assert result is not None and result.response == GUEST_MODE_UNAVAILABLE
    assert services.calls == []


def routing_rule(*, scope="request", reset=False, match_type="starts_with"):
    return {
        "id": f"route-{scope}-{reset}",
        "name": "Think carefully",
        "enabled": True,
        "phrases": ["think carefully"],
        "match_type": match_type,
        "action_type": "model_routing",
        "action": {
            "model": None if reset else "gpt-5",
            "reasoning_effort": None if reset else "high",
            "scope": scope,
            "reset": reset,
            "success_response": "Updated",
        },
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": 0,
    }


async def test_single_request_override_and_provider_assembly() -> None:
    rules = await manager(routing_rule())
    runtime = RequestRuleRuntime()
    result = await async_evaluate_rule(
        SimpleNamespace(), rules, runtime, "think carefully about this", "session"
    )
    assert result is not None and not result.consume
    effective = runtime.effective_options(
        {CONF_CHAT_MODEL: "gpt-4o"}, "session", result.request_override
    )
    snapshot = build_provider_request_snapshot(effective, {})
    assert snapshot.api_kwargs["model"] == "gpt-5"
    effort = snapshot.api_kwargs.get("reasoning", {}).get(
        "effort"
    ) or snapshot.api_kwargs.get("reasoning_effort")
    assert effort == "high"
    assert runtime.get("session") == {}


async def test_conversation_override_precedence_reset_and_new_session() -> None:
    runtime = RequestRuleRuntime()
    conversation_rules = await manager(routing_rule(scope="conversation"))
    result = await async_evaluate_rule(
        SimpleNamespace(),
        conversation_rules,
        runtime,
        "think carefully about this",
        "one",
    )
    assert result is not None
    assert (
        runtime.effective_options({CONF_CHAT_MODEL: "default"}, "one")[CONF_CHAT_MODEL]
        == "gpt-5"
    )
    assert (
        runtime.effective_options(
            {CONF_CHAT_MODEL: "default"}, "one", {CONF_CHAT_MODEL: "request"}
        )[CONF_CHAT_MODEL]
        == "request"
    )
    assert (
        runtime.effective_options({CONF_CHAT_MODEL: "default"}, "two")[CONF_CHAT_MODEL]
        == "default"
    )

    reset_rules = await manager(routing_rule(reset=True, match_type="equals"))
    reset = await async_evaluate_rule(
        SimpleNamespace(), reset_rules, runtime, "think carefully", "one"
    )
    assert reset is not None and reset.consume
    assert runtime.get("one") == {}


async def test_conversation_overrides_compose_across_separate_rules() -> None:
    runtime = RequestRuleRuntime()
    model_rule = routing_rule(scope="conversation")
    model_rule["action"]["reasoning_effort"] = None
    reasoning_rule = routing_rule(scope="conversation")
    reasoning_rule["action"]["model"] = None

    await async_evaluate_rule(
        SimpleNamespace(),
        await manager(model_rule),
        runtime,
        "think carefully about this",
        "one",
    )
    await async_evaluate_rule(
        SimpleNamespace(),
        await manager(reasoning_rule),
        runtime,
        "think carefully about this",
        "one",
    )
    assert runtime.get("one") == {
        CONF_CHAT_MODEL: "gpt-5",
        CONF_REASONING_EFFORT: "high",
    }

    next_model = routing_rule(scope="conversation")
    next_model["action"]["model"] = "gpt-5-mini"
    next_model["action"]["reasoning_effort"] = None
    await async_evaluate_rule(
        SimpleNamespace(),
        await manager(next_model),
        runtime,
        "think carefully about this",
        "one",
    )
    next_reasoning = routing_rule(scope="conversation")
    next_reasoning["action"]["model"] = None
    next_reasoning["action"]["reasoning_effort"] = "low"
    await async_evaluate_rule(
        SimpleNamespace(),
        await manager(next_reasoning),
        runtime,
        "think carefully about this",
        "one",
    )
    assert runtime.get("one") == {
        CONF_CHAT_MODEL: "gpt-5-mini",
        CONF_REASONING_EFFORT: "low",
    }


async def test_invalid_conversation_update_is_atomic() -> None:
    runtime = RequestRuleRuntime()
    runtime.set("one", {CONF_CHAT_MODEL: "gpt-5", CONF_REASONING_EFFORT: "high"})
    rule = routing_rule(scope="conversation")
    rule["action"]["model"] = "gpt-4o"
    rule["action"]["reasoning_effort"] = None
    with pytest.raises(HomeAssistantError, match="does not support reasoning"):
        await async_evaluate_rule(
            SimpleNamespace(),
            await manager(rule),
            runtime,
            "think carefully about this",
            "one",
        )
    assert runtime.get("one") == {
        CONF_CHAT_MODEL: "gpt-5",
        CONF_REASONING_EFFORT: "high",
    }


def test_conversation_override_expires_after_inactivity(monkeypatch) -> None:
    clock = iter((0.0, 0.0, 61.0))
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.monotonic",
        lambda: next(clock),
    )
    runtime = RequestRuleRuntime()
    runtime.set("one", {CONF_CHAT_MODEL: "gpt-5"}, timeout_minutes=1)
    assert runtime.get("one", timeout_minutes=1) == {}


def test_exact_routing_command_is_consumed_but_substantive_request_is_not() -> None:
    exact = validate_rule(routing_rule(scope="conversation", match_type="equals"))
    starts = validate_rule(routing_rule(match_type="starts_with"))
    assert exact["match_type"] == "equals"
    assert starts["match_type"] == "starts_with"


def test_invalid_model_reasoning_combination_fails_validation() -> None:
    rule = routing_rule()
    rule["action"]["model"] = "gpt-4o"
    with pytest.raises(ValueError, match="does not support reasoning"):
        validate_rule(rule)


def test_request_only_equals_routing_is_rejected_as_pointless() -> None:
    with pytest.raises(ValueError, match="rest of the conversation"):
        validate_rule(routing_rule(scope="request", match_type="equals"))


async def test_runtime_rejects_request_model_with_conversation_reasoning() -> None:
    runtime = RequestRuleRuntime()
    runtime.set("one", {CONF_CHAT_MODEL: "gpt-5", CONF_REASONING_EFFORT: "high"})
    rule = routing_rule()
    rule["action"]["model"] = "gpt-4o"
    rule["action"]["reasoning_effort"] = None
    rules = await manager(rule)
    with pytest.raises(HomeAssistantError, match="does not support reasoning"):
        await async_evaluate_rule(
            SimpleNamespace(),
            rules,
            runtime,
            "think carefully about this",
            "one",
        )


def test_canonical_action_signature_is_stable() -> None:
    actions = local_rule()["action"]["actions"]
    assert canonical_action_signature(actions) == canonical_action_signature(
        [
            {
                "service": "turn_on",
                "domain": "script",
                "data": {},
                "target": {"entity_id": ["script.goodnight"]},
            }
        ]
    )


async def test_management_api_permissions_crud_and_delete_confirmation(
    monkeypatch,
) -> None:
    rules = await manager()
    subentry = SimpleNamespace(subentry_id="agent", subentry_type="conversation")
    entry = SimpleNamespace(
        domain="extended_openai_conversation_responses",
        subentries={"agent": subentry},
    )
    config_entries = SimpleNamespace(async_get_entry=lambda entry_id: entry)
    hass = SimpleNamespace(config_entries=config_entries)

    async def get_rules(*args):
        return rules

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.async_get_request_rules",
        get_rules,
    )
    base = {
        "section": "request_rules",
        "entry_id": "entry",
        "subentry_id": "agent",
    }
    with pytest.raises(HomeAssistantError, match="Administrator"):
        await async_management_command(hass, "user", False, {**base, "action": "list"})
    created = await async_management_command(
        hass,
        "admin",
        True,
        {**base, "action": "create", "rule": local_rule()},
    )
    rule_id = created["rule"]["id"]
    with pytest.raises(HomeAssistantError, match="confirmation"):
        await async_management_command(
            hass,
            "admin",
            True,
            {**base, "action": "delete", "rule_id": rule_id},
        )
    deleted = await async_management_command(
        hass,
        "admin",
        True,
        {**base, "action": "delete", "rule_id": rule_id, "confirm": True},
    )
    assert deleted == {"deleted": True}
