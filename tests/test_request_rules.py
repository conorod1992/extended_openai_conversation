"""Request Rule matching, persistence, execution, and routing tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
    DOMAIN,
    SERVICE_CALL_FUNCTION,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    validate_function_arguments,
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
    _ACTIVE_FUNCTION_EXECUTOR,
    _ACTIVE_FUNCTION_RESULTS,
    DEFAULT_MATCHING,
    DEFAULT_WORDING_GROUPS,
    RequestRuleRuntime,
    RequestRules,
    RequestRuleStore,
    _assign_missing_result_step_ids,
    _bounded_function_result,
    _MatchCursor,
    _validate_result_dependencies,
    async_call_active_function,
    async_evaluate_rule,
    canonical_action_signature,
    normalize_text,
    request_rule_session_id,
    resolve_result_values,
    validate_rule,
    validate_rule_groups,
    validate_wording_groups,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.typing import UNDEFINED


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


@pytest.mark.parametrize("fuzzy", [False, True])
async def test_direct_match_skips_previously_excluded_rule_ids(fuzzy: bool) -> None:
    settings = {**DEFAULT_MATCHING, "fuzzy": fuzzy}
    rule = local_rule("Kitchen lights", phrases=["turn on the kitchen light"])
    rule["matching"] = settings
    fallback = local_rule("Fallback lights", phrases=["turn on the kitchen light"])
    fallback["matching"] = settings
    rules = await manager(rule, fallback)

    match = rules.match("turn on the kitchen light", frozenset({rule["id"]}))

    assert match is not None
    assert match.rule["id"] == fallback["id"]


def test_rule_group_metadata_and_result_step_id_assignment_are_stable() -> None:
    assert validate_rule_groups([{"id": "rooms", "name": "Rooms"}]) == [
        {"id": "rooms", "name": "Rooms"}
    ]
    with pytest.raises(ValueError, match="group ids must be unique"):
        validate_rule_groups(
            [{"id": "rooms", "name": "Rooms"}, {"id": "rooms", "name": "Other"}]
        )

    rule = {
        "action": {
            "actions": [
                {"type": "function", "result_alias": "reading"},
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {"result_alias": "status"},
                },
                {"type": "function", "name": "no-result"},
            ]
        }
    }
    assigned = _assign_missing_result_step_ids(rule)

    assert rule["action"]["actions"][0].get("step_id") is None
    assert len(assigned["action"]["actions"][0]["step_id"]) == 32
    assert len(assigned["action"]["actions"][1]["data"]["step_id"]) == 32
    assert "step_id" not in assigned["action"]["actions"][2]


async def test_only_when_uses_first_eligible_text_match_and_preview_trace(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
        async_request_rule_match_preview,
    )

    first = local_rule("First", phrases=["hello"])
    second = local_rule("Second", phrases=["hello"], order=1)
    first["conditions"] = [
        {"condition": "state", "entity_id": "input_boolean.first", "state": "on"}
    ]
    second["conditions"] = [
        {"condition": "state", "entity_id": "input_boolean.second", "state": "on"}
    ]
    checks = []

    async def validate(_hass, config):
        return config

    async def build(_hass, config):
        entity = config["entity_id"][0]
        return SimpleNamespace(
            async_check=lambda **_kwargs: (
                checks.append(entity) or entity.endswith("second")
            )
        )

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.ha_condition.async_validate_condition_config",
        validate,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.ha_condition.async_from_config",
        build,
    )
    rules = await manager(first, second)
    from custom_components.extended_openai_conversation_responses import (
        request_rules as rule_module,
    )

    compared = []
    original_match = rule_module._deterministic_match

    def counted_match(text, phrase, match_type):
        compared.append(phrase)
        return original_match(text, phrase, match_type)

    monkeypatch.setattr(rule_module, "_deterministic_match", counted_match)
    preview = await async_request_rule_match_preview(hass, rules, "hello")
    assert preview["rule"]["name"] == "Second"
    assert preview["skipped_conditions"] == [
        {"id": "first", "name": "First", "reason": "conditions_false"}
    ]
    assert checks == ["input_boolean.first", "input_boolean.second"]
    assert len(compared) == 2
    await rules.async_match(hass, "hello")
    assert checks == ["input_boolean.first", "input_boolean.second"] * 2
    assert len(compared) == 4


async def test_condition_checker_is_rebuilt_after_edit_and_restore(
    hass, monkeypatch
) -> None:
    rule = local_rule("Conditional", phrases=["hello"])
    rule["conditions"] = [
        {"condition": "state", "entity_id": "input_boolean.old", "state": "on"}
    ]
    built = []

    async def validate(_hass, config):
        return config

    async def build(_hass, config):
        built.append(config["entity_id"])
        return SimpleNamespace(async_check=lambda **_kwargs: True)

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.ha_condition.async_validate_condition_config",
        validate,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.ha_condition.async_from_config",
        build,
    )
    rules = await manager(rule)
    await rules.async_match(hass, "hello")
    await rules.async_match(hass, "hello")
    assert built == [["input_boolean.old"]]
    edited = deepcopy(rules.snapshot()["rules"][0])
    edited["conditions"][0]["entity_id"] = "input_boolean.new"
    await rules.async_update(edited["id"], edited)
    await rules.async_match(hass, "hello")
    assert built == [["input_boolean.old"], ["input_boolean.new"]]
    backup = await rules.async_backup_data()
    await rules.async_replace_backup(backup)
    await rules.async_match(hass, "hello")
    assert built == [
        ["input_boolean.old"],
        ["input_boolean.new"],
        ["input_boolean.new"],
    ]


async def test_only_when_does_not_check_nonmatching_rule_and_stops_on_error(
    hass, monkeypatch
) -> None:
    first = local_rule("First", phrases=["unrelated"])
    first["conditions"] = [
        {"condition": "state", "entity_id": "input_boolean.first", "state": "on"}
    ]
    second = local_rule("Second", phrases=["hello"], order=1)
    second["conditions"] = [
        {"condition": "state", "entity_id": "input_boolean.second", "state": "on"}
    ]

    async def fail(_hass, config):
        assert config["entity_id"] == ["input_boolean.second"]
        raise RuntimeError("condition unavailable")

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.ha_condition.async_validate_condition_config",
        fail,
    )
    rules = await manager(
        first, second, local_rule("Third", phrases=["hello"], order=2)
    )
    with pytest.raises(HomeAssistantError, match="condition could not be evaluated"):
        await rules.async_match(hass, "hello")


async def test_indeterminate_condition_stops_before_later_local_rule(
    hass, monkeypatch
) -> None:
    first = local_rule("First", phrases=["hello"])
    first["conditions"] = [
        {"condition": "state", "entity_id": "input_boolean.first", "state": "on"}
    ]

    async def validate(_hass, config):
        return config

    async def build(_hass, _config):
        return SimpleNamespace(async_check=lambda **_kwargs: None)

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.ha_condition.async_validate_condition_config",
        validate,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.ha_condition.async_from_config",
        build,
    )
    rules = await manager(first, local_rule("Later", phrases=["hello"], order=1))
    with pytest.raises(HomeAssistantError, match="condition could not be evaluated"):
        await rules.async_match(hass, "hello")


async def test_sentence_and_fuzzy_conditions_skip_to_next_eligible(
    hass, monkeypatch
) -> None:
    sentence = local_rule(
        "Sentence", phrases=["Set {room} light"], match_type="sentence_pattern"
    )
    sentence["conditions"] = [
        {"condition": "state", "entity_id": "input_boolean.no", "state": "on"}
    ]
    later = local_rule(
        "Later", phrases=["Set {room} light"], match_type="sentence_pattern", order=1
    )
    later["conditions"] = [
        {"condition": "state", "entity_id": "input_boolean.yes", "state": "on"}
    ]

    async def validate(_hass, config):
        return config

    async def build(_hass, config):
        return SimpleNamespace(
            async_check=lambda **_kwargs: config["entity_id"][0].endswith("yes")
        )

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.ha_condition.async_validate_condition_config",
        validate,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.ha_condition.async_from_config",
        build,
    )
    rules = await manager(sentence, later)
    assert (await rules.async_match(hass, "Set kitchen light")).rule["name"] == "Later"
    first = local_rule(
        "Fuzzy first",
        phrases=["hello"],
        behavior="custom",
        matching={**DEFAULT_MATCHING, "fuzzy": True, "fuzzy_threshold": 70},
    )
    first["conditions"] = sentence["conditions"]
    second = local_rule(
        "Fuzzy second",
        phrases=["hello"],
        order=1,
        behavior="custom",
        matching={**DEFAULT_MATCHING, "fuzzy": True, "fuzzy_threshold": 70},
    )
    second["conditions"] = later["conditions"]
    rules = await manager(first, second)
    assert (await rules.async_match(hass, "hellp")).rule["name"] == "Fuzzy second"


def test_only_when_validation_and_legacy_default() -> None:
    assert validate_rule(local_rule())["conditions"] == []
    rule = local_rule()
    rule["conditions"] = [
        {"condition": "state", "entity_id": "input_boolean.ready", "state": "on"}
    ]
    assert validate_rule(rule)["conditions"] == rule["conditions"]
    rule["conditions"] = [{"condition": "state"}]
    with pytest.raises(ValueError, match="Only when"):
        validate_rule(rule)


@pytest.mark.parametrize(
    ("condition", "local_time", "expected"),
    [
        ({"condition": "time", "after": "00:00:00"}, "2026-09-28T12:00:00+00:00", True),
        (
            {"condition": "time", "after": "11:00:00", "before": "13:00:00"},
            "2026-09-28T12:00:00+00:00",
            True,
        ),
        (
            {"condition": "time", "after": "22:00:00", "before": "06:00:00"},
            "2026-09-29T02:00:00+00:00",
            True,
        ),
        (
            {
                "condition": "time",
                "after": "11:00:00",
                "before": "13:00:00",
                "weekday": ["mon"],
            },
            "2026-09-28T12:00:00+00:00",
            True,
        ),
        (
            {"condition": "time", "after": "14:00:00", "before": "16:00:00"},
            "2026-09-28T12:00:00+00:00",
            False,
        ),
        (
            {"condition": "time", "weekday": ["tue"]},
            "2026-09-28T12:00:00+00:00",
            False,
        ),
    ],
)
async def test_native_time_conditions_agree_in_preview_and_request_execution(
    hass, freezer, condition, local_time, expected
) -> None:
    """Saved Home Assistant time conditions execute consistently at runtime."""
    from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
        async_request_rule_match_preview,
    )

    freezer.move_to(datetime.fromisoformat(local_time))
    rule = local_rule("Time conditioned", phrases=["good night"])
    rule["conditions"] = [condition]
    saved_rule = validate_rule(rule)
    assert saved_rule["conditions"] == [condition]
    rules = await manager(saved_rule)

    preview = await async_request_rule_match_preview(hass, rules, "good night")
    assert preview["matched"] is expected
    hass.services = FakeServices()
    outcome = await async_evaluate_rule(
        hass, rules, RequestRuleRuntime(), "good night", "time-condition-session"
    )
    if expected:
        assert outcome is not None and outcome.successful
        assert len(hass.services.calls) == 1
    else:
        assert outcome is None
        assert hass.services.calls == []


def test_invalid_native_time_condition_is_rejected_before_save() -> None:
    rule = local_rule("Invalid time", phrases=["good night"])
    rule["conditions"] = [{"condition": "time", "after": "not-a-time"}]
    with pytest.raises(ValueError, match="Only when"):
        validate_rule(rule)


async def test_local_continue_to_ai_success_and_failure(hass) -> None:
    rule = local_rule()
    rule["action"]["continue_to_ai"] = True
    rules = await manager(rule)
    hass.services = FakeServices()
    passed = await async_evaluate_rule(
        hass, rules, RequestRuleRuntime(), "good night", "session"
    )
    assert passed is not None and not passed.consume and passed.response is None
    assert len(hass.services.calls) == 1
    hass.services = FakeServices(fail=True)
    failed = await async_evaluate_rule(
        hass, rules, RequestRuleRuntime(), "good night", "session"
    )
    assert failed is not None and failed.consume and not failed.successful
    assert failed.response == "Failed safely"


async def test_continue_matching_runs_each_local_rule_once_and_preview_is_safe(
    hass,
) -> None:
    from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
        async_request_rule_match_preview,
    )

    first = local_rule("First", phrases=["hello"])
    first["continue_matching"] = True
    second = local_rule("Second", phrases=["hello"], order=1)
    second["continue_matching"] = True
    third = local_rule("Third", phrases=["hello"], order=2)
    rules = await manager(first, second, third)
    services = FakeServices()
    hass.services = services

    preview = await async_request_rule_match_preview(hass, rules, "hello")
    assert [item["rule"]["name"] for item in preview["matched_rules"]] == [
        "First",
        "Second",
        "Third",
    ]
    assert [item["status"] for item in preview["matched_rules"]] == [
        "continued",
        "continued",
        "stopped",
    ]
    assert services.calls == []

    outcome = await async_evaluate_rule(
        hass, rules, RequestRuleRuntime(), "hello", "session"
    )
    assert outcome is not None and outcome.match.rule["name"] == "Third"
    assert outcome.consume and outcome.successful
    assert len(services.calls) == 3


async def test_continue_matching_defaults_off_and_legacy_rules_migrate() -> None:
    rule = local_rule()
    assert validate_rule(rule)["continue_matching"] is False
    rule["continue_matching"] = "yes"
    with pytest.raises(ValueError, match="continue_matching"):
        validate_rule(rule)


async def test_continue_matching_persists_and_round_trips_backup() -> None:
    original = local_rule("First", phrases=["hello"])
    original["continue_matching"] = True
    source = await manager(original)
    backup = await source.async_backup_data()
    assert backup["rules"][0]["continue_matching"] is True
    assert (
        RequestRules.validate_backup_data(backup)["rules"][0]["continue_matching"]
        is True
    )
    restored = RequestRules(MemoryStore())
    await restored.async_replace_backup(backup)
    assert restored.snapshot()["rules"][0]["continue_matching"] is True


async def test_global_drag_reorder_keeps_groups_and_compiled_patterns() -> None:
    first = local_rule("First", phrases=["first"])
    second = local_rule("Second", phrases=["second"], order=1)
    third = local_rule("Third", phrases=["third"], order=2)
    rules = await manager(first, second, third)
    compiled = rules._matching_snapshot.phrases[0][2]
    await rules.async_move("first", "after", target_rule_id="third")
    assert [rule["name"] for rule in rules.snapshot()["rules"]] == [
        "Second",
        "Third",
        "First",
    ]
    assert rules._matching_snapshot.phrases[-1][2] is compiled
    await rules.async_move("first", "before", target_rule_id="second")
    assert [rule["name"] for rule in rules.snapshot()["rules"]] == [
        "First",
        "Second",
        "Third",
    ]


async def test_move_at_order_boundaries_is_a_noop() -> None:
    store = MemoryStore(
        {
            "rules": [
                local_rule("First", phrases=["first"]),
                local_rule("Second", phrases=["second"], order=1),
            ]
        }
    )
    rules = RequestRules(store)
    await rules.async_initialize()
    store.saves = 0

    assert await rules.async_move("first", "up") == rules.snapshot()["rules"][0]
    assert await rules.async_move("first", "top") == rules.snapshot()["rules"][0]
    assert await rules.async_move("second", "down") == rules.snapshot()["rules"][1]
    assert await rules.async_move("second", "bottom") == rules.snapshot()["rules"][1]
    assert store.saves == 0
    with pytest.raises(ValueError, match="direction"):
        await rules.async_move("first", "sideways")
    with pytest.raises(ValueError, match="target rule id"):
        await rules.async_move("first", "before")


async def test_initialize_recovers_each_invalid_stored_matcher_section() -> None:
    store = MemoryStore(
        {
            "groups": [{"id": "broken"}],
            "defaults": {"fuzzy": "yes"},
            "wording_groups": [{"canonical": "home", "alternatives": "bad"}],
        }
    )
    rules = RequestRules(store)

    await rules.async_initialize()

    assert rules.snapshot()["groups"] == []
    assert rules.snapshot()["defaults"] == DEFAULT_MATCHING
    assert rules.snapshot()["wording_groups"] == list(DEFAULT_WORDING_GROUPS)
    assert store.saves == 1


def test_backup_rejects_rules_with_missing_group_references() -> None:
    rule = local_rule("Grouped", phrases=["hello"])
    rule["group_id"] = "missing-group"

    with pytest.raises(ValueError, match="unknown group"):
        RequestRules.validate_backup_data({"groups": [], "rules": [rule]})


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("conditions", "not-a-list", "Only when conditions must be a list"),
        ("actions", [None], "Home Assistant action must be an object"),
    ],
)
def test_rule_validation_rejects_invalid_condition_and_action_containers(
    field: str, value: Any, message: str
) -> None:
    rule = local_rule()
    if field == "conditions":
        rule[field] = value
    else:
        rule["action"][field] = value

    with pytest.raises(ValueError, match=message):
        validate_rule(rule)


async def test_group_name_change_does_not_rebuild_matcher() -> None:
    rule = local_rule("First", phrases=["hello"])
    rule["group_id"] = "group-1"
    rules = RequestRules(
        MemoryStore({"groups": [{"id": "group-1", "name": "Old"}], "rules": [rule]})
    )
    await rules.async_initialize()
    snapshot = rules._matching_snapshot
    await rules.async_set_groups([{"id": "group-1", "name": "New"}])
    assert rules._matching_snapshot is snapshot
    await rules.async_set_groups([])
    assert rules.snapshot()["rules"][0]["group_id"] is None
    assert rules._matching_snapshot.phrases[0][2] is snapshot.phrases[0][2]


async def test_condition_failed_exact_keeps_earlier_fuzzy_fallback(
    hass, monkeypatch
) -> None:
    fuzzy = {
        "word_forms": False,
        "wording_alternatives": False,
        "fuzzy": True,
        "fuzzy_threshold": 90,
    }
    fallback = local_rule(
        "Fallback",
        phrases=["turn on kitchen light"],
        order=0,
        behavior="custom",
        matching=fuzzy,
    )
    exact = local_rule(
        "Exact",
        phrases=["turn on the kitchen light"],
        order=1,
    )
    exact["conditions"] = [
        {"condition": "state", "entity_id": "input_boolean.blocked", "state": "on"}
    ]

    async def validate(_hass, config):
        return config

    async def build(_hass, _config):
        return SimpleNamespace(async_check=lambda **_kwargs: False)

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.ha_condition.async_validate_condition_config",
        validate,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.ha_condition.async_from_config",
        build,
    )

    rules = await manager(fallback, exact)
    match, skipped = await rules.async_match_with_skipped(
        hass, "turn on the kitchen light"
    )
    assert match is not None and match.rule["name"] == "Fallback"
    assert skipped == [
        {"id": "exact", "name": "Exact", "reason": "conditions_false"}
    ]


async def test_condition_failed_best_fuzzy_tries_next_eligible_candidate(
    hass, monkeypatch
) -> None:
    fuzzy = {
        "word_forms": False,
        "wording_alternatives": False,
        "fuzzy": True,
        "fuzzy_threshold": 70,
    }
    best = local_rule("Best", phrases=["ligh"], order=0, behavior="custom", matching=fuzzy)
    best["conditions"] = [
        {"condition": "state", "entity_id": "input_boolean.no", "state": "on"}
    ]
    fallback = local_rule(
        "Fallback",
        phrases=["ligth"],
        order=1,
        behavior="custom",
        matching=fuzzy,
    )
    fallback["conditions"] = [
        {"condition": "state", "entity_id": "input_boolean.yes", "state": "on"}
    ]

    async def validate(_hass, config):
        return config

    async def build(_hass, config):
        return SimpleNamespace(
            async_check=lambda **_kwargs: config["entity_id"][0].endswith("yes")
        )

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.ha_condition.async_validate_condition_config",
        validate,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.ha_condition.async_from_config",
        build,
    )

    rules = await manager(best, fallback)
    match, skipped = await rules.async_match_with_skipped(hass, "light")
    assert match is not None and match.rule["name"] == "Fallback"
    assert skipped == [
        {"id": "best", "name": "Best", "reason": "conditions_false"}
    ]


async def test_fuzzy_chain_never_returns_to_earlier_priority() -> None:
    fuzzy = {
        "word_forms": False,
        "wording_alternatives": False,
        "fuzzy": True,
        "fuzzy_threshold": 70,
    }
    rules = await manager(
        local_rule("First", phrases=["liagt"], order=0),
        local_rule("Second", phrases=["ligh"], order=1),
        local_rule("Third", phrases=["ligth"], order=2),
        defaults=fuzzy,
    )
    cursor = _MatchCursor(rules._matching_snapshot, "light")
    first = cursor.next_match()
    assert first is not None and first.rule["name"] == "Second"
    cursor.accept(first)
    second = cursor.next_match()
    assert second is not None and second.rule["name"] == "Third"
    assert cursor.next_match() is None


async def test_continue_matching_handoff_stops_later_rules(hass) -> None:
    first = local_rule("First", phrases=["hello"])
    first["continue_matching"] = True
    first["action"]["continue_to_ai"] = True
    second = local_rule("Second", phrases=["hello"], order=1)
    rules = await manager(first, second)
    services = FakeServices()
    hass.services = services
    outcome = await async_evaluate_rule(
        hass, rules, RequestRuleRuntime(), "hello", "session"
    )
    assert outcome is not None and not outcome.consume
    assert outcome.match.rule["name"] == "First"
    assert len(services.calls) == 1


async def test_continue_matching_failure_stops_later_actions(hass) -> None:
    first = local_rule("First", phrases=["hello"])
    first["continue_matching"] = True
    second = local_rule("Second", phrases=["hello"], order=1)
    rules = await manager(first, second)
    services = FakeServices(fail=True)
    hass.services = services
    outcome = await async_evaluate_rule(
        hass, rules, RequestRuleRuntime(), "hello", "session"
    )
    assert outcome is not None and outcome.match.rule["name"] == "First"
    assert outcome.consume and not outcome.successful
    assert outcome.response == "Failed safely"
    assert len(services.calls) == 1


async def test_guest_denial_after_passing_condition_never_continues_to_ai(
    hass, monkeypatch
) -> None:
    rule = local_rule()
    rule["action"]["continue_to_ai"] = True
    rule["action"]["actions"][0]["target"] = {}
    rule["conditions"] = [
        {"condition": "state", "entity_id": "input_boolean.ready", "state": "on"}
    ]

    async def validate(_hass, config):
        return config

    async def build(_hass, _config):
        return SimpleNamespace(async_check=lambda **_kwargs: True)

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.ha_condition.async_validate_condition_config",
        validate,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.ha_condition.async_from_config",
        build,
    )
    hass.services = FakeServices()
    outcome = await async_evaluate_rule(
        hass,
        await manager(rule),
        RequestRuleRuntime(),
        "good night",
        "session",
        guest_policy=GuestCapabilityPolicy(True),
    )
    assert outcome is not None and outcome.consume and not outcome.successful
    assert outcome.response == GUEST_MODE_UNAVAILABLE
    assert hass.services.calls == []


async def test_result_alias_validation_and_substitution() -> None:
    rule = local_rule(phrases=["Battery of {device}"], match_type="sentence_pattern")
    rule["action"]["actions"] = [
        {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "data": {
                "function": "battery",
                "arguments": {"device": "{{ device }}"},
                "result_alias": "battery",
            },
        },
        {
            "action": "notify.send_message",
            "data": {"message": "{battery.level} for {device}"},
        },
    ]
    rule["action"]["success_response"] = "{battery.name} is at {battery.level}%"
    validated = validate_rule(rule)
    assert "step_id" not in validated["action"]["actions"][0]["data"]
    assert validate_rule(rule) == validated
    saved = await (await manager()).async_create(rule)
    assert saved["action"]["actions"][0]["data"]["step_id"]
    assert (
        resolve_result_values(
            validated["action"]["success_response"],
            {"device": "tablet"},
            {"battery": {"name": "Kitchen tablet", "level": 62}},
        )
        == "Kitchen tablet is at 62%"
    )
    assert (
        resolve_result_values(
            "{device} / {battery.device}",
            {"device": "request tablet"},
            {"battery": {"device": "returned tablet"}},
        )
        == "request tablet / returned tablet"
    )
    assert resolve_result_values("{battery}", {}, {"battery": False}) is False
    assert resolve_result_values("{battery}", {}, {"battery": 0}) == 0
    assert resolve_result_values(
        {"message": "Battery: {battery.level}", "items": ["{device}", 7]},
        {"device": "tablet"},
        {"battery": {"level": 62}},
    ) == {"message": "Battery: 62", "items": ["tablet", 7]}
    assert (
        resolve_result_values(
            "{battery.items.0.name}", {}, {"battery": {"items": [{"name": "tablet"}]}}
        )
        == "tablet"
    )
    with pytest.raises(ValueError, match="unavailable"):
        resolve_result_values("{battery.missing}", {}, {"battery": {}})
    rule["action"]["actions"][0]["data"]["result_alias"] = "request"
    with pytest.raises(ValueError, match="alias"):
        validate_rule(rule)
    rule["action"]["actions"][0]["data"]["result_alias"] = "bad-name"
    with pytest.raises(ValueError, match="alias"):
        validate_rule(rule)


async def test_function_result_capture_requires_active_result_context() -> None:
    executor = AsyncMock(return_value={"result": "completed"})
    token = _ACTIVE_FUNCTION_EXECUTOR.set(executor)
    try:
        with pytest.raises(HomeAssistantError, match="require an active Request Rule"):
            await async_call_active_function("lookup", {}, result_alias="lookup_result")
    finally:
        _ACTIVE_FUNCTION_EXECUTOR.reset(token)

    executor.assert_awaited_once_with("lookup", {})


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"plain": "mapping"}, {"plain": "mapping"}),
        ({"result": "not valid JSON"}, "not valid JSON"),
    ],
)
async def test_function_capture_preserves_unwrapped_and_non_json_results(
    payload, expected
) -> None:
    results = {}

    async def execute(_function, _arguments):
        return SimpleNamespace(tool_result=payload)

    executor_token = _ACTIVE_FUNCTION_EXECUTOR.set(execute)
    results_token = _ACTIVE_FUNCTION_RESULTS.set(results)
    try:
        captured = await async_call_active_function("lookup", {}, "value")
    finally:
        _ACTIVE_FUNCTION_RESULTS.reset(results_token)
        _ACTIVE_FUNCTION_EXECUTOR.reset(executor_token)

    assert captured == expected
    assert results["value"] == expected


def test_result_resolution_rejects_out_of_range_list_paths() -> None:
    with pytest.raises(ValueError, match="path .* is unavailable"):
        resolve_result_values(
            "{reading.items.1}", {}, {"reading": {"items": ["only item"]}}
        )
    with pytest.raises(ValueError, match="Function result missing is unavailable"):
        resolve_result_values("{missing.value}", {}, {})


async def test_result_dependencies_and_bounds() -> None:
    rule = local_rule()
    rule["action"]["actions"] = [
        {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "data": {"function": "same", "arguments": {}, "result_alias": "one"},
        },
        {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "data": {
                "function": "same",
                "arguments": {"previous": "{one.value}"},
                "result_alias": "two",
            },
        },
    ]
    rule["action"]["success_response"] = "{two.value}"
    validated = await (await manager()).async_create(rule)
    assert len(validated["action"]["actions"]) == 2
    assert (
        validated["action"]["actions"][0]["data"]["step_id"]
        != validated["action"]["actions"][1]["data"]["step_id"]
    )
    assert (
        validate_rule(validated)["action"]["actions"][0]["data"]["step_id"]
        == validated["action"]["actions"][0]["data"]["step_id"]
    )
    duplicate = deepcopy(rule)
    duplicate["action"]["actions"][1]["data"]["result_alias"] = "one"
    with pytest.raises(ValueError, match="unique"):
        validate_rule(duplicate)
    reordered = deepcopy(rule)
    reordered["action"]["actions"].reverse()
    with pytest.raises(ValueError, match="earlier step"):
        validate_rule(reordered)
    deleted = deepcopy(rule)
    deleted["action"]["actions"].pop(0)
    with pytest.raises(ValueError, match="earlier step"):
        validate_rule(deleted)
    assert _bounded_function_result(False) is False
    assert _bounded_function_result(0) == 0
    with pytest.raises(HomeAssistantError, match="too large"):
        _bounded_function_result("x" * 20000)
    with pytest.raises(HomeAssistantError, match="deeply nested"):
        _bounded_function_result([[[[[[[[[0]]]]]]]]])
    with pytest.raises(HomeAssistantError, match="keys must be text"):
        _bounded_function_result({1: "invalid key"})
    with pytest.raises(HomeAssistantError, match="must contain JSON values"):
        _bounded_function_result(object())


async def test_match_cursor_deduplicates_matching_phrases_for_one_rule() -> None:
    rules = await manager(
        local_rule(phrases=["turn on", "turn on lights"], match_type="starts_with")
    )
    cursor = _MatchCursor(rules._committed_matching_snapshot, "turn on lights")

    match = cursor.next_match()

    assert match is not None and match.rule["id"] == "good-night"
    assert match.phrase == "turn on"
    assert cursor.next_match() is None


@pytest.mark.parametrize(
    "phrases",
    [
        ["open kitchen lights", "turn off music", "open kitchen light"],
        ["open kitchen light", "open kitchen lights", "turn off music"],
    ],
)
async def test_match_cursor_keeps_best_fuzzy_phrase_per_rule(phrases) -> None:
    matching = {
        "word_forms": False,
        "wording_alternatives": False,
        "fuzzy": True,
        "fuzzy_threshold": 70,
    }
    rules = await manager(
        local_rule(
            phrases=phrases,
            match_type="equals",
            behavior="custom",
            matching=matching,
        )
    )
    cursor = _MatchCursor(
        rules._committed_matching_snapshot, "open kitchen lightz"
    )

    match = cursor.next_match()

    assert match is not None and match.fuzzy
    assert match.phrase == "open kitchen light"
    assert cursor.next_match() is None


async def test_empty_snapshot_has_no_eligible_matches() -> None:
    rules = await manager()
    skipped: list[dict[str, str]] = []

    matches = [
        match
        async for match in rules.async_eligible_matches(
            SimpleNamespace(), "anything", skipped
        )
    ]

    assert matches == []
    assert skipped == []


@pytest.mark.parametrize(
    ("actions", "slots", "success", "failure", "message"),
    [
        (
            [
                {
                    "choose": [
                        {
                            "sequence": [
                                {
                                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                                    "data": {"result_alias": "reading"},
                                }
                            ]
                        }
                    ]
                }
            ],
            set(),
            "",
            "",
            "only by top-level steps",
        ),
        (
            [
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {"result_alias": "request"},
                }
            ],
            set(),
            "",
            "",
            "distinct simple identifier",
        ),
        (
            [
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {"result_alias": "bad-name"},
                }
            ],
            set(),
            "",
            "",
            "distinct simple identifier",
        ),
        (
            [
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {"result_alias": "device"},
                }
            ],
            {"device"},
            "",
            "",
            "distinct simple identifier",
        ),
        (
            [
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {"result_alias": "reading", "step_id": "bad"},
                }
            ],
            set(),
            "",
            "",
            "step IDs must be unique",
        ),
        (
            [
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {"result_alias": "reading", "step_id": "a" * 32},
                },
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {"result_alias": "reading", "step_id": "b" * 32},
                },
            ],
            set(),
            "",
            "",
            "aliases must be unique",
        ),
        (
            [
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {"result_alias": "reading", "step_id": "a" * 32},
                },
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {"result_alias": "other", "step_id": "a" * 32},
                },
            ],
            set(),
            "",
            "",
            "step IDs must be unique",
        ),
        (
            [
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {"result_alias": "reading"},
                }
            ],
            set(),
            "{missing.value}",
            "",
            "missing Function result",
        ),
        (
            [
                {
                    "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
                    "data": {"result_alias": "reading"},
                }
            ],
            set(),
            "",
            "{reading.value}",
            "Failure response cannot reference Function results",
        ),
    ],
)
def test_result_dependency_validation_rejects_unsafe_alias_contracts(
    actions, slots, success, failure, message
) -> None:
    with pytest.raises(ValueError, match=message):
        _validate_result_dependencies(
            {
                "actions": actions,
                "success_response": success,
                "failure_response": failure,
            },
            slots,
        )


async def test_groups_preserve_global_order_and_revision() -> None:
    rules = await manager(local_rule("One"), local_rule("Two", order=1))
    first_revision = rules.revision()
    updated = await rules.async_set_groups(
        [{"id": "g1", "name": "Kitchen"}], expected_revision=first_revision
    )
    assert updated["rules"] == rules.snapshot()["rules"]
    assert rules.match("good night").rule["name"] == "One"
    with pytest.raises(ValueError, match="another tab"):
        await rules.async_set_groups([], expected_revision=first_revision)
    first = rules.snapshot()["rules"][0]
    await rules.async_update(
        first["id"], {**first, "group_id": "g1"}, expected_revision=rules.revision()
    )
    before = [item["id"] for item in rules.snapshot()["rules"]]
    await rules.async_set_groups([], expected_revision=rules.revision())
    assert [item["id"] for item in rules.snapshot()["rules"]] == before
    assert all(item["group_id"] is None for item in rules.snapshot()["rules"])
    moved = await rules.async_move("two", "top", expected_revision=rules.revision())
    assert moved["order"] == 0
    assert rules.match("good night").rule["name"] == "Two"


async def test_group_updates_reject_non_list_and_unknown_rule_group() -> None:
    rules = await manager(local_rule("Grouped"))
    with pytest.raises(ValueError, match="groups must be a list"):
        await rules.async_set_groups(None)

    changed = rules.snapshot()["rules"][0]
    changed["group_id"] = "missing"
    with pytest.raises(ValueError, match="group does not exist"):
        await rules.async_update(changed["id"], changed)


async def test_group_creation_assigns_stable_backend_id() -> None:
    rules = await manager(local_rule())
    created = await rules.async_set_groups(
        [{"name": "Kitchen"}], expected_revision=rules.revision()
    )
    group = created["groups"][0]
    assert len(group["id"]) == 32
    assert rules.snapshot()["groups"] == [group]
    repeated = await rules.async_set_groups(
        [group], expected_revision=created["revision"]
    )
    assert repeated["groups"] == [group]


async def test_group_and_reorder_mutations_keep_compiled_sentence_patterns(
    monkeypatch,
) -> None:
    rules = await manager(
        local_rule("One", phrases=["Set {room} light"], match_type="sentence_pattern"),
        local_rule(
            "Two", phrases=["Set {room} light"], match_type="sentence_pattern", order=1
        ),
    )

    def unexpected_compile(_pattern):
        raise AssertionError(
            "metadata and order mutations must retain compiled patterns"
        )

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules._compile_sentence_pattern",
        unexpected_compile,
    )
    await rules.async_set_groups(
        [{"id": "home", "name": "Home"}], expected_revision=rules.revision()
    )
    await rules.async_move("two", "top", expected_revision=rules.revision())
    assert rules.match("Set kitchen light").rule["name"] == "Two"


async def test_groups_survive_restart_and_backup_restore() -> None:
    store = MemoryStore({"defaults": dict(DEFAULT_MATCHING), "rules": [local_rule()]})
    rules = RequestRules(store)
    await rules.async_initialize()
    assert rules.snapshot()["groups"] == []
    await rules.async_set_groups(
        [{"id": "home", "name": "Home"}], expected_revision=rules.revision()
    )
    old = rules.snapshot()["rules"][0]
    await rules.async_update(
        old["id"], {**old, "group_id": "home"}, expected_revision=rules.revision()
    )
    restored = RequestRules(MemoryStore())
    await restored.async_initialize()
    await restored.async_replace_backup(await rules.async_backup_data())
    assert restored.snapshot()["groups"] == [{"id": "home", "name": "Home"}]
    assert restored.snapshot()["rules"][0]["group_id"] == "home"
    restarted = RequestRules(store)
    await restarted.async_initialize()
    assert restarted.snapshot()["groups"] == restored.snapshot()["groups"]


async def test_function_result_capture_feeds_later_step_and_response(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        request_rules as module,
    )

    calls = []

    class CaptureScript:
        def __init__(self, _hass, sequence, *_args, **_kwargs):
            self.sequence = sequence

        async def async_run(self, _variables, _context=None):
            variables = dict(_variables)
            for step in self.sequence:
                if "variables" in step:
                    values = step["variables"]
                    raw = values.variables if hasattr(values, "variables") else values
                    if all(name.startswith(("__eoai_completed_", "__eoai_stopped_", "__eoai_wait_completed_")) for name in raw):
                        variables.update(values.async_simple_render(variables) if hasattr(values, "async_simple_render") else values)
                elif step.get("action") == f"{DOMAIN}.{SERVICE_CALL_FUNCTION}":
                    await async_call_active_function(
                        step["data"]["function"],
                        step["data"]["arguments"],
                        step["data"].get("result_alias"),
                    )
                    variables.update(module._ACTIVE_FUNCTION_RESULTS.get())
                elif "data" in step and "message" in step["data"]:
                    message = step["data"]["message"]
                    calls.append(
                        message.async_render(variables, parse_result=False)
                        if hasattr(message, "async_render")
                        else message
                    )
            return SimpleNamespace(variables=variables, conversation_response=UNDEFINED)

        async def async_unload(self):
            pass

    monkeypatch.setattr(module, "Script", CaptureScript)
    rule = local_rule(phrases=["Battery {device}"], match_type="sentence_pattern")
    rule["action"]["actions"] = [
        {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "data": {
                "function": "get_battery",
                "arguments": {},
                "result_alias": "battery",
            },
        },
        {
            "action": "notify.send_message",
            "data": {"message": "{battery.level} for {device}"},
        },
    ]
    rule["action"]["success_response"] = "{battery.name} is at {battery.level}%"

    async def execute(_name, _arguments):
        return SimpleNamespace(
            tool_result={"result": json.dumps({"name": "Kitchen tablet", "level": 62})}
        )

    outcome = await async_evaluate_rule(
        hass,
        await manager(rule),
        RequestRuleRuntime(),
        "Battery tablet",
        "session",
        function_executor=execute,
    )
    assert outcome is not None and outcome.response == "Kitchen tablet is at 62%"
    assert calls == ["62 for tablet"]


async def test_missing_function_result_path_stops_later_steps(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        request_rules as module,
    )

    calls = []

    class CaptureScript:
        def __init__(self, _hass, sequence, *_args, **_kwargs):
            self.sequence = sequence

        async def async_run(self, _variables, _context=None):
            step = next(step for step in self.sequence if step.get("action"))
            if step["action"] == f"{DOMAIN}.{SERVICE_CALL_FUNCTION}":
                await async_call_active_function(
                    step["data"]["function"], {}, step["data"].get("result_alias")
                )
            else:
                calls.append(step)

        async def async_unload(self):
            pass

    monkeypatch.setattr(module, "Script", CaptureScript)
    rule = local_rule()
    rule["action"]["actions"] = [
        {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "data": {
                "function": "get_battery",
                "arguments": {},
                "result_alias": "battery",
            },
        },
        {"action": "notify.send_message", "data": {"message": "{battery.missing}"}},
    ]

    async def execute(_name, _arguments):
        return SimpleNamespace(tool_result={"result": json.dumps({"level": 0})})

    outcome = await async_evaluate_rule(
        hass,
        await manager(rule),
        RequestRuleRuntime(),
        "good night",
        "session",
        function_executor=execute,
    )
    assert (
        outcome is not None
        and outcome.response == "Failed safely"
        and not outcome.successful
    )
    assert calls == []


@pytest.mark.parametrize("value", [False, 0, "", None])
async def test_function_capture_accepts_false_zero_empty_and_null(value) -> None:
    from custom_components.extended_openai_conversation_responses import (
        request_rules as module,
    )

    results = {}

    async def execute(_name, _arguments):
        return SimpleNamespace(tool_result={"result": json.dumps(value)})

    executor_token = module._ACTIVE_FUNCTION_EXECUTOR.set(execute)
    result_token = module._ACTIVE_FUNCTION_RESULTS.set(results)
    try:
        await async_call_active_function("value", {}, "captured")
        assert "captured" in results and results["captured"] == value
    finally:
        module._ACTIVE_FUNCTION_RESULTS.reset(result_token)
        module._ACTIVE_FUNCTION_EXECUTOR.reset(executor_token)


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


@pytest.mark.parametrize(
    ("stored_phrase", "spoken_text"),
    [
        ("café lights", "cafe\u0301 lights"),
        ("straße", "STRASSE"),
        ("ΟΣ", "ος"),
        ("İSTANBUL", "i\u0307stanbul"),
    ],
)
async def test_request_rule_matching_has_explicit_unicode_equivalence_contract(
    stored_phrase: str,
    spoken_text: str,
) -> None:
    """Canonical Unicode and locale-neutral case folding match predictably."""
    settings = {**DEFAULT_MATCHING, "word_forms": False, "wording_alternatives": False}
    rules = await manager(
        local_rule(
            phrases=[stored_phrase],
            matching=settings,
            behavior="custom",
        )
    )

    match = rules.match(spoken_text)

    assert match is not None
    assert match.phrase == stored_phrase
    assert normalize_text(stored_phrase, settings) == normalize_text(
        spoken_text, settings
    )


def test_unicode_normalization_distinguishes_dotless_i_from_ascii_i() -> None:
    """Case folding is Unicode-aware but deliberately not locale-sensitive."""
    settings = {
        **DEFAULT_MATCHING,
        "word_forms": False,
        "wording_alternatives": False,
    }

    assert normalize_text("I", settings) == "i"
    assert normalize_text("ı", settings) == "ı"
    assert normalize_text("I", settings) != normalize_text("ı", settings)
    # Punctuation filtering turns an internal combining dot into a boundary.
    assert normalize_text("\u0130STANBUL", settings) == "i stanbul"
    assert normalize_text("\u0130STANBUL", settings) != normalize_text("istanbul", settings)


def test_session_identity_uses_continuity_or_actual_chat_log_id() -> None:
    assert request_rule_session_id("device:kitchen", "core-id") == (
        "continuity:device:kitchen:conversation:core-id"
    )
    assert request_rule_session_id(None, "core-created-id") == (
        "conversation:core-created-id"
    )


async def test_multiple_phrases_and_curated_wording_alternatives() -> None:
    rules = await manager(local_rule(phrases=["turn on the tv", "power up the tv"]))
    match = rules.match("Switch on the television")
    assert match is not None
    assert match.phrase == "turn on the tv"


async def test_persisted_wording_groups_can_be_replaced_and_backed_up() -> None:
    store = MemoryStore()
    rules = RequestRules(store)
    await rules.async_initialize()
    groups = [{"canonical": "activate", "alternatives": ["power up"]}]
    assert await rules.async_set_wording_groups(groups) == groups
    created = local_rule(phrases=["activate kitchen"])
    await rules.async_create(created)
    assert rules.match("power up kitchen") is not None
    backup = await rules.async_backup_data()
    assert backup["wording_groups"] == groups


def test_wording_group_validation_rejects_ambiguous_phrases() -> None:
    with pytest.raises(ValueError, match="ambiguous duplicate"):
        validate_wording_groups(
            [
                {"canonical": "turn on", "alternatives": ["switch on"]},
                {"canonical": "activate", "alternatives": ["switch on"]},
            ]
        )


async def test_missing_wording_groups_seed_existing_defaults() -> None:
    rules = await manager(local_rule(phrases=["turn on the tv"]))
    assert rules.snapshot()["wording_groups"] == list(DEFAULT_WORDING_GROUPS)
    assert rules.match("switch on the television") is not None


async def test_storage_v1_migration_seeds_wording_groups() -> None:
    store = object.__new__(RequestRuleStore)
    migrated = await store._async_migrate_func(
        1, 0, {"defaults": dict(DEFAULT_MATCHING), "rules": []}
    )
    assert migrated["wording_groups"] == list(DEFAULT_WORDING_GROUPS)


async def test_storage_v2_migration_is_additive() -> None:
    store = object.__new__(RequestRuleStore)
    old = {"defaults": dict(DEFAULT_MATCHING), "rules": [local_rule()]}
    assert await store._async_migrate_func(2, 0, old) == old


async def test_hassil_pattern_supports_optional_alternatives_and_slots() -> None:
    rules = await manager(
        local_rule(
            phrases=["[please ](set|change) {room} lights"],
            match_type="sentence_pattern",
        )
    )
    match = rules.match("please change kitchen lights")
    assert match is not None
    assert match.fuzzy is False
    assert match.slots == {"room": "kitchen"}


async def test_multiword_multiple_slots_and_sentence_variants() -> None:
    rules = await manager(
        local_rule(
            phrases=["Add {item} to {list_name}", "Put {item} on {list_name}"],
            match_type="sentence_pattern",
        )
    )
    first = rules.match("Add semi skimmed milk to weekly shopping")
    second = rules.match("Put oat milk on weekend list")
    assert first is not None and first.slots == {
        "item": "semi skimmed milk",
        "list_name": "weekly shopping",
    }
    assert second is not None and second.slots == {
        "item": "oat milk",
        "list_name": "weekend list",
    }


@pytest.mark.parametrize(
    ("pattern", "matches", "near_miss"),
    [
        (
            "[please ]remember {fact}",
            [
                (
                    "please remember the blue recycling bin is Tuesday",
                    {"fact": "the blue recycling bin is Tuesday"},
                ),
                ("remember buy oat milk", {"fact": "buy oat milk"}),
            ],
            "please recall the blue recycling bin is Tuesday",
        ),
        (
            "(turn|switch) {room} lights on",
            [
                (
                    "switch upstairs guest room lights on",
                    {"room": "upstairs guest room"},
                ),
                ("turn kitchen lights on", {"room": "kitchen"}),
            ],
            "toggle upstairs guest room lights on",
        ),
        (
            "[please ](add|put) {item} (to|on) {list_name}",
            [
                (
                    "please put semi skimmed milk on weekly shopping list",
                    {
                        "item": "semi skimmed milk",
                        "list_name": "weekly shopping list",
                    },
                ),
                (
                    "add oat milk to groceries",
                    {"item": "oat milk", "list_name": "groceries"},
                ),
            ],
            "please place semi skimmed milk on weekly shopping list",
        ),
    ],
)
async def test_supported_sentence_pattern_combinations_with_slots(
    pattern, matches, near_miss
) -> None:
    rules = await manager(local_rule(phrases=[pattern], match_type="sentence_pattern"))
    for text, expected_slots in matches:
        match = rules.match(text)
        assert match is not None
        assert match.slots == expected_slots
    assert rules.match(near_miss) is None


def test_unknown_slot_reference_and_mismatched_variants_are_rejected() -> None:
    rule = local_rule(phrases=["Remember {fact}"], match_type="sentence_pattern")
    rule["action"]["success_response"] = "Saved {missing}"
    with pytest.raises(ValueError, match="unknown captured value: missing"):
        validate_rule(rule)
    rule["action"]["success_response"] = "Saved"
    rule["phrases"] = ["Remember {fact}", "Save {item}"]
    with pytest.raises(ValueError, match="same slots"):
        validate_rule(rule)


async def test_hassil_sentence_pattern_bypasses_text_normalization() -> None:
    rules = await manager(
        local_rule(phrases=["turn on light"], match_type="sentence_pattern")
    )
    assert rules.match("switch on light") is None
    assert rules.match("turn on lights") is None


def test_hassil_sentence_pattern_rejects_named_expansions() -> None:
    with pytest.raises(ValueError, match="named expansion rules"):
        validate_rule(
            local_rule(phrases=["turn <device> on"], match_type="sentence_pattern")
        )


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


async def test_first_deterministic_rule_wins_over_fuzzy_and_specificity() -> None:
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
    assert match.rule["name"] == "Contains"
    assert match.fuzzy is False


async def test_moving_rule_changes_deterministic_priority() -> None:
    rules = await manager(
        local_rule(
            name="Contains", phrases=["good night"], match_type="contains", order=0
        ),
        local_rule(name="Exact", phrases=["good night"], match_type="equals", order=1),
    )
    match = rules.match("good night")
    assert match is not None and match.rule["name"] == "Contains"

    await rules.async_move("exact", "up")

    match = rules.match("good night")
    assert match is not None and match.rule["name"] == "Exact"


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


async def test_slot_function_rule_persistence_round_trip() -> None:
    store = MemoryStore()
    rules = RequestRules(store)
    await rules.async_initialize()
    rule = local_rule(phrases=["Remember {fact}"], match_type="sentence_pattern")
    rule["action"]["actions"] = [
        {
            "type": "function",
            "function": "remember",
            "arguments": {"fact": {"source": "slot", "slot": "fact"}},
        }
    ]
    created = await rules.async_create(rule)
    assert created["slots"] == [{"name": "fact"}]
    assert created["action"]["actions"] == [
        {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "data": {
                "function": "remember",
                "arguments": {"fact": "{{ fact }}"},
            },
        }
    ]
    reloaded = RequestRules(store)
    await reloaded.async_initialize()
    assert reloaded.snapshot()["rules"][0] == created


async def test_legacy_flat_action_is_persisted_as_native_action() -> None:
    legacy = local_rule()
    store = MemoryStore({"defaults": dict(DEFAULT_MATCHING), "rules": [legacy]})
    rules = RequestRules(store)
    await rules.async_initialize()
    assert store.saves == 1
    assert store.data["rules"][0]["action"]["actions"][0] == {
        "action": "script.turn_on",
        "target": {"entity_id": ["script.goodnight"]},
        "data": {},
    }


def test_native_script_constructs_and_templates_validate() -> None:
    rule = local_rule(phrases=["Set lights to {level}"], match_type="sentence_pattern")
    rule["action"]["actions"] = [
        {
            "choose": [
                {
                    "conditions": [
                        {
                            "condition": "state",
                            "entity_id": "binary_sensor.home",
                            "state": "on",
                        }
                    ],
                    "sequence": [
                        {
                            "action": "light.turn_on",
                            "target": {"entity_id": "light.lamp"},
                            "data": {"brightness_pct": "{{ level }}"},
                        }
                    ],
                }
            ],
            "default": [{"delay": {"seconds": 1}}],
        }
    ]
    validated = validate_rule(rule)
    assert validated["action"]["actions"] == rule["action"]["actions"]
    assert validated["slots"] == [{"name": "level"}]


def test_native_dynamic_duration_and_iteration_templates_validate() -> None:
    rule = local_rule()
    rule["action"]["actions"] = [
        {"delay": {"seconds": "{{ 2 }}"}},
        {
            "wait_template": "{{ is_state('binary_sensor.ready', 'on') }}",
            "timeout": "{{ 5 }}",
            "continue_on_timeout": True,
        },
        {
            "repeat": {
                "for_each": "{{ ['light.kitchen', 'light.hall'] }}",
                "sequence": [
                    {
                        "action": "light.turn_off",
                        "target": {"entity_id": "{{ repeat.item }}"},
                    }
                ],
            }
        },
    ]

    validated = validate_rule(rule)

    assert validated["action"]["actions"] == rule["action"]["actions"]


def test_native_malformed_dynamic_duration_template_is_rejected() -> None:
    rule = local_rule()
    rule["action"]["actions"] = [{"delay": {"seconds": "{{ broken"}}]

    with pytest.raises(ValueError, match="invalid Home Assistant action sequence"):
        validate_rule(rule)


def test_native_script_variable_is_not_a_captured_request_slot() -> None:
    rule = local_rule()
    rule["action"]["actions"] = [
        {"variables": {"level": 50}},
        {
            "action": "light.turn_on",
            "data": {"brightness_pct": "{{ level }}"},
        },
    ]
    validated = validate_rule(rule)
    assert validated["slots"] == []
    assert validated["action"]["actions"] == rule["action"]["actions"]


def test_native_compact_jinja_round_trips_without_legacy_rewriting() -> None:
    rule = local_rule()
    rule["action"]["actions"] = [
        {
            "action": "notify.send_message",
            "data": {"message": "{{item}}"},
        }
    ]
    validated = validate_rule(rule)
    assert validated["action"]["actions"][0]["data"]["message"] == "{{item}}"


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

    def async_services_for_domain(self, domain):
        from tests.entity_service_fixtures import registered_entity_service
        return {"turn_on": registered_entity_service(), "turn_off": registered_entity_service()}

    async def async_call(self, domain, service, **kwargs):
        self.calls.append((domain, service, kwargs))
        if self.fail:
            raise HomeAssistantError("boom")


@pytest.fixture(autouse=True)
def fake_ha_script(monkeypatch):
    """Keep these unit tests focused while preserving the production Script seam."""

    async def render(value, variables):
        if hasattr(value, "async_render"):
            return value.async_render(variables, parse_result=False)
        if isinstance(value, str):
            for name, item in variables.items():
                value = value.replace(f"{{{{ {name} }}}}", str(item))
            return value
        if isinstance(value, dict):
            return {key: await render(item, variables) for key, item in value.items()}
        if isinstance(value, list):
            return [await render(item, variables) for item in value]
        return value

    class FakeScript:
        def __init__(self, hass, sequence, *_args, **_kwargs):
            self.hass = hass
            self.sequence = sequence

        async def async_run(self, variables, _context=None):
            for action in self.sequence:
                if "variables" in action:
                    script_variables = action["variables"]
                    if hasattr(script_variables, "async_simple_render"):
                        variables.update(
                            script_variables.async_simple_render(variables)
                        )
                    else:
                        variables.update(await render(script_variables, variables))
                    continue
                service_name = action["action"]
                data = await render(action.get("data", {}), variables)
                if service_name == f"{DOMAIN}.{SERVICE_CALL_FUNCTION}":
                    await async_call_active_function(
                        data["function"], data["arguments"]
                    )
                    continue
                domain, service = service_name.split(".", 1)
                await self.hass.services.async_call(
                    domain,
                    service,
                    target=await render(action.get("target", {}), variables),
                    service_data=data,
                    blocking=True,
                )
            return SimpleNamespace(variables=variables, conversation_response=UNDEFINED)

        async def async_unload(self):
            return None

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request_rules.Script",
        FakeScript,
    )


@pytest.mark.parametrize("fail", [False, True])
async def test_multiple_local_actions_and_failure_response(fail, hass) -> None:
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
    hass.services = services
    result = await async_evaluate_rule(
        hass, rules, RequestRuleRuntime(), "good night", "conversation:one"
    )
    assert result is not None and result.consume
    assert result.response == ("Failed safely" if fail else "Done")
    assert len(services.calls) == (1 if fail else 2)


async def test_slots_resolve_in_action_data_response_and_multiple_actions(hass) -> None:
    rule = local_rule(phrases=["Remember {fact}"], match_type="sentence_pattern")
    rule["action"]["actions"] = [
        {
            "domain": "input_text",
            "service": "set_value",
            "target": {"entity_id": "input_text.memory"},
            "data": {"value": {"value_from": "slot", "slot": "fact"}},
        },
        {
            "domain": "notify",
            "service": "send_message",
            "target": {},
            "data": {"message": "Remembered {fact}"},
        },
    ]
    rule["action"]["success_response"] = "I will remember {fact}."
    services = FakeServices()
    hass.services = services
    result = await async_evaluate_rule(
        hass,
        await manager(rule),
        RequestRuleRuntime(),
        "Remember buy oat milk tomorrow",
        "conversation:slots",
    )
    assert result is not None
    assert result.response == "I will remember buy oat milk tomorrow."
    assert services.calls[0][2]["service_data"]["value"] == "buy oat milk tomorrow"
    assert services.calls[1][2]["service_data"]["message"] == (
        "Remembered buy oat milk tomorrow"
    )


async def test_native_template_uses_captured_slot_alongside_script_variable(
    hass,
) -> None:
    rule = local_rule(
        phrases=["Set brightness to {level}"], match_type="sentence_pattern"
    )
    rule["action"]["actions"] = [
        {"variables": {"transition": 1}},
        {
            "action": "light.turn_on",
            "target": {"entity_id": "light.lamp"},
            "data": {
                "brightness_pct": "{{ level }}",
                "transition": "{{ transition }}",
            },
        },
    ]
    services = FakeServices()
    hass.services = services
    result = await async_evaluate_rule(
        hass,
        await manager(rule),
        RequestRuleRuntime(),
        "Set brightness to 60",
        "conversation:native-slot",
    )
    assert result is not None and result.response == "Done"
    assert services.calls == [
        (
            "light",
            "turn_on",
            {
                "target": {"entity_id": ["light.lamp"]},
                "service_data": {"brightness_pct": "60", "transition": "1"},
                "blocking": True,
            },
        )
    ]


async def test_direct_function_action_fixed_and_slot_arguments_without_provider(
    hass,
) -> None:
    rule = local_rule(
        phrases=["Add {item} to {list_name}"], match_type="sentence_pattern"
    )
    rule["action"]["actions"] = [
        {
            "type": "function",
            "function": "add_list_item",
            "arguments": {
                "item": {"source": "slot", "slot": "item"},
                "list": {"source": "fixed", "value": "Shopping"},
                "spoken_list": {"source": "slot", "slot": "list_name"},
            },
        }
    ]
    calls = []

    async def execute(name, arguments):
        calls.append((name, arguments))
        return "ok"

    result = await async_evaluate_rule(
        hass,
        await manager(rule),
        RequestRuleRuntime(),
        "Add semi skimmed milk to weekly groceries",
        "conversation:function",
        function_executor=execute,
    )
    assert result is not None and result.response == "Done"
    assert calls == [
        (
            "add_list_item",
            {
                "item": "semi skimmed milk",
                "list": "Shopping",
                "spoken_list": "weekly groceries",
            },
        )
    ]


async def test_direct_function_error_uses_local_failure_response(hass) -> None:
    rule = local_rule(phrases=["Remember {fact}"], match_type="sentence_pattern")
    rule["action"]["actions"] = [
        {
            "type": "function",
            "function": "remember",
            "arguments": {"fact": {"source": "slot", "slot": "fact"}},
        }
    ]

    async def fail(_name, _arguments):
        raise HomeAssistantError("function failed")

    result = await async_evaluate_rule(
        hass,
        await manager(rule),
        RequestRuleRuntime(),
        "Remember the blue bin is Tuesday",
        "conversation:function-error",
        function_executor=fail,
    )
    assert result is not None and result.response == "Failed safely"


def test_shared_function_argument_validation_common_schema_types() -> None:
    spec = {
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "count": {"type": "integer"},
                "ratio": {"type": "number"},
                "enabled": {"type": "boolean"},
                "mode": {"type": "string", "enum": ["one", "two"]},
                "items": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["text", "count"],
            "additionalProperties": False,
        }
    }
    assert validate_function_arguments(
        spec,
        {
            "text": "hello",
            "count": "2",
            "ratio": "1.5",
            "enabled": "true",
            "mode": "one",
            "items": "milk, bread",
        },
    ) == {
        "text": "hello",
        "count": 2,
        "ratio": 1.5,
        "enabled": True,
        "mode": "one",
        "items": ["milk", "bread"],
    }
    with pytest.raises(HomeAssistantError, match="Missing required"):
        validate_function_arguments(spec, {"text": "hello"})
    with pytest.raises(HomeAssistantError, match="one of its choices"):
        validate_function_arguments(spec, {"text": "hello", "count": 1, "mode": "bad"})


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


async def test_guest_mode_allows_permitted_local_action_without_ai(hass) -> None:
    rules = await manager(local_rule())
    services = FakeServices()
    hass.services = services
    policy = GuestCapabilityPolicy(
        True,
        readable_entity_ids=frozenset({"script.goodnight"}),
        controllable_entity_ids=frozenset({"script.goodnight"}),
    )
    result = await async_evaluate_rule(
        hass,
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


def test_routing_rule_validates_required_captured_provider_input() -> None:
    rule = routing_rule(match_type="sentence_pattern")
    rule["phrases"] = ["ask {query}", "please ask {query}"]
    rule["action"]["continue_to_ai"] = True
    rule["ai_input_mode"] = "capture"
    rule["ai_input_capture"] = "query"

    validated = validate_rule(rule)

    assert validated["ai_input_mode"] == "capture"
    assert validated["ai_input_capture"] == "query"
    assert validated["slots"] == [{"name": "query"}]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"ai_input_mode": "invalid"}, "AI input must be Original request"),
        (
            {"match_type": "equals", "ai_input_mode": "capture"},
            "Captured AI input requires a Sentence Pattern capture",
        ),
        (
            {
                "phrases": ["ask {query}"],
                "ai_input_mode": "capture",
                "ai_input_capture": "missing",
            },
            "Captured AI input must exist in every trigger",
        ),
        (
            {
                "phrases": ["[{query}] ask"],
                "ai_input_mode": "capture",
                "ai_input_capture": "query",
            },
            "Captured AI input must be present on every match",
        ),
        (
            {
                "phrases": ["ask {query}"],
                "ai_input_mode": "capture",
                "ai_input_capture": "query",
                "action_continue_to_ai": False,
            },
            "Captured AI input requires Continue to AI",
        ),
        (
            {"ai_input_mode": "original", "ai_input_capture": "query"},
            "Original AI input cannot select a capture",
        ),
    ],
)
def test_routing_rule_rejects_invalid_captured_provider_input(
    change, message
) -> None:
    rule = routing_rule()
    rule["match_type"] = "sentence_pattern"
    rule["phrases"] = ["ask {query}"]
    rule["action"]["continue_to_ai"] = True
    settings = deepcopy(change)
    if "action_continue_to_ai" in settings:
        rule["action"]["continue_to_ai"] = settings.pop("action_continue_to_ai")
    if "match_type" in settings:
        rule["match_type"] = settings.pop("match_type")
        if rule["match_type"] == "equals":
            rule["phrases"] = ["ask"]
    rule.update(settings)

    with pytest.raises(ValueError, match=message):
        validate_rule(rule)


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

    reset_rules = await manager(
        routing_rule(scope="conversation", reset=True, match_type="equals")
    )
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


async def test_conversation_reset_clears_prior_request_reset_during_rule_cascade() -> None:
    request_reset = routing_rule(scope="request", reset=True)
    request_reset["order"] = 0
    request_reset["continue_matching"] = True
    request_reset["action"]["continue_to_ai"] = False
    conversation_reset = routing_rule(scope="conversation", reset=True)
    conversation_reset["order"] = 1
    conversation_reset["action"]["continue_to_ai"] = False
    rules = await manager(request_reset, conversation_reset)

    result = await async_evaluate_rule(
        SimpleNamespace(),
        rules,
        RequestRuleRuntime(),
        "think carefully about this",
        "session",
    )

    assert result is not None
    assert result.match.rule["action"]["scope"] == "conversation"
    assert result.request_override is None


async def test_conversation_route_reapplies_new_values_after_request_reset() -> None:
    request_reset = routing_rule(scope="request", reset=True)
    request_reset["order"] = 0
    request_reset["continue_matching"] = True
    request_reset["action"]["continue_to_ai"] = False
    conversation_route = routing_rule(scope="conversation")
    conversation_route["order"] = 1
    conversation_route["action"]["continue_to_ai"] = False
    rules = await manager(request_reset, conversation_route)

    result = await async_evaluate_rule(
        SimpleNamespace(),
        rules,
        RequestRuleRuntime(),
        "think carefully about this",
        "session",
    )

    assert result is not None
    assert result.request_override == {
        "__request_rule_reset__": "1",
        CONF_CHAT_MODEL: "gpt-5",
        CONF_REASONING_EFFORT: "high",
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
    subentry = SimpleNamespace(
        subentry_id="agent", subentry_type="conversation", data={}
    )
    entry = SimpleNamespace(
        domain="extended_openai_conversation_responses",
        subentries={"agent": subentry},
    )
    config_entries = SimpleNamespace(async_get_entry=lambda entry_id: entry)
    hass = SimpleNamespace(data={}, config_entries=config_entries)

    async def get_rules(*args):
        return rules

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.async_get_request_rules",
        get_rules,
    )

    service_catalog_calls = 0

    async def get_service_descriptions(*args):
        nonlocal service_catalog_calls
        service_catalog_calls += 1
        return {"light": {"turn_on": {"name": "Turn on", "fields": {}}}}

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.service_helper.async_get_all_descriptions",
        get_service_descriptions,
    )
    base = {
        "section": "request_rules",
        "entry_id": "entry",
        "subentry_id": "agent",
    }
    with pytest.raises(HomeAssistantError, match="Administrator"):
        await async_management_command(hass, "user", False, {**base, "action": "list"})
    listed = await async_management_command(
        hass, "admin", True, {**base, "action": "list"}
    )
    assert "service_catalog" not in listed
    assert service_catalog_calls == 0

    with pytest.raises(HomeAssistantError, match="Administrator"):
        await async_management_command(
            hass,
            "user",
            False,
            {**base, "section": "service_catalog", "action": "get"},
        )
    service_catalog = await async_management_command(
        hass,
        "admin",
        True,
        {**base, "section": "service_catalog", "action": "get"},
    )
    assert "light" in service_catalog["services"]
    assert service_catalog_calls == 1
    groups = [{"canonical": "activate", "alternatives": ["power up"]}]
    updated_groups = await async_management_command(
        hass,
        "admin",
        True,
        {**base, "action": "wording_groups", "wording_groups": groups},
    )
    assert updated_groups["wording_groups"] == groups
    assert isinstance(updated_groups["revision"], str)
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
    assert deleted["deleted"] is True
    assert isinstance(deleted["revision"], str)


@pytest.mark.parametrize(
    ("match_type", "expected"),
    [
        ("equals", False),
        ("sentence_pattern", False),
        ("starts_with", True),
        ("ends_with", True),
        ("contains", True),
    ],
)
def test_legacy_routing_flow_is_normalized_once(match_type, expected) -> None:
    rule = routing_rule(
        scope="conversation"
        if match_type in {"equals", "sentence_pattern"}
        else "request",
        match_type=match_type,
    )
    validated = validate_rule(rule)
    assert validated["action"]["continue_to_ai"] is expected


def test_explicit_continue_to_ai_allows_equals_request_scope() -> None:
    rule = routing_rule(scope="request", match_type="equals")
    rule["action"]["continue_to_ai"] = True
    validated = validate_rule(rule)
    assert validated["action"]["scope"] == "request"
    assert validated["action"]["continue_to_ai"] is True


def test_explicit_continue_to_ai_survives_backup_normalization() -> None:
    rule = routing_rule(scope="request", match_type="equals")
    rule["action"]["continue_to_ai"] = True
    prepared = RequestRules.validate_backup_data(
        {"defaults": dict(DEFAULT_MATCHING), "rules": [rule]}
    )
    restored = prepared["rules"][0]
    assert restored["action"]["scope"] == "request"
    assert restored["action"]["continue_to_ai"] is True


async def test_explicit_continue_to_ai_decouples_equals_from_consumption() -> None:
    rule = routing_rule(scope="request", match_type="equals")
    rule["action"]["continue_to_ai"] = True
    result = await async_evaluate_rule(
        SimpleNamespace(),
        await manager(rule),
        RequestRuleRuntime(),
        "think carefully",
        "session",
    )
    assert result is not None and result.consume is False
    assert result.response is None
    assert result.request_override == {
        CONF_CHAT_MODEL: "gpt-5",
        CONF_REASONING_EFFORT: "high",
    }


async def test_explicit_standalone_routing_decouples_contains_from_consumption() -> (
    None
):
    rule = routing_rule(scope="conversation", match_type="contains")
    rule["action"]["continue_to_ai"] = False
    runtime = RequestRuleRuntime()
    result = await async_evaluate_rule(
        SimpleNamespace(),
        await manager(rule),
        runtime,
        "please think carefully about this",
        "session",
    )
    assert result is not None and result.consume is True
    assert result.response == "Updated"
    assert result.request_override is None
    assert runtime.get("session") == {
        CONF_CHAT_MODEL: "gpt-5",
        CONF_REASONING_EFFORT: "high",
    }


def test_explicit_standalone_request_scope_is_rejected() -> None:
    rule = routing_rule(scope="request", match_type="contains")
    rule["action"]["continue_to_ai"] = False
    with pytest.raises(ValueError, match="Continue to AI"):
        validate_rule(rule)


def test_continue_to_ai_requires_boolean() -> None:
    rule = routing_rule(scope="request", match_type="starts_with")
    rule["action"]["continue_to_ai"] = "yes"
    with pytest.raises(ValueError, match="continue_to_ai must be true or false"):
        validate_rule(rule)


async def test_storage_v4_migration_is_additive() -> None:
    store = object.__new__(RequestRuleStore)
    old = {"defaults": dict(DEFAULT_MATCHING), "rules": [local_rule()]}
    assert await store._async_migrate_func(4, 0, old) == old
