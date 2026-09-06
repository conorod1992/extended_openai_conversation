"""Focused regressions for roadmap PR2 Request Rule management integrity."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    RequestRules,
    validate_rule,
)


def _routing_rule(
    rule_id: str,
    name: str,
    *,
    phrase: str = "use the careful model",
    match_type: str = "contains",
    order: int = 0,
) -> dict:
    return validate_rule(
        {
            "id": rule_id,
            "name": name,
            "enabled": True,
            "phrases": [phrase],
            "match_type": match_type,
            "action_type": "model_routing",
            "action": {
                "model": "gpt-5-mini",
                "reasoning_effort": None,
                "scope": "request" if match_type not in {"equals", "sentence_pattern"} else "conversation",
                "reset": False,
                "success_response": "Updated",
            },
            "matching_behavior": "defaults",
            "matching": DEFAULT_MATCHING,
            "order": order,
        }
    )


def _function_rule(function_name: str) -> dict:
    return validate_rule(
        {
            "id": "function-rule",
            "name": "Run configured function",
            "enabled": True,
            "phrases": ["do the thing"],
            "match_type": "equals",
            "action_type": "local_action",
            "action": {
                "actions": [
                    {
                        "action": f"{DOMAIN}.call_function",
                        "data": {"function": function_name, "arguments": {}},
                    }
                ],
                "success_response": "Done",
                "failure_response": "Failed",
            },
            "matching_behavior": "defaults",
            "matching": DEFAULT_MATCHING,
            "order": 0,
        }
    )


def _manager(rules: list[dict]) -> tuple[RequestRules, SimpleNamespace]:
    store = SimpleNamespace(async_save=AsyncMock())
    manager = RequestRules(store)
    manager._initialized = True
    manager._rules = rules
    manager._sort_and_compile()
    return manager, store


async def test_move_changes_only_final_order_tiebreaker() -> None:
    first = _routing_rule("first", "First", order=0)
    second = _routing_rule("second", "Second", order=1)
    manager, store = _manager([first, second])

    assert manager.match("please use the careful model now").rule["id"] == "first"
    revision = manager.revision()

    moved = await manager.async_move("second", "up", expected_revision=revision)

    assert moved["id"] == "second"
    assert [rule["id"] for rule in manager.snapshot()["rules"]] == ["second", "first"]
    assert manager.match("please use the careful model now").rule["id"] == "second"
    store.async_save.assert_awaited_once()


async def test_move_does_not_override_match_type_or_specificity() -> None:
    broad = _routing_rule(
        "broad",
        "Broad",
        phrase="careful model",
        match_type="contains",
        order=0,
    )
    exact = _routing_rule(
        "exact",
        "Exact",
        phrase="use the careful model",
        match_type="equals",
        order=1,
    )
    manager, _store = _manager([broad, exact])

    assert manager.match("use the careful model").rule["id"] == "exact"

    await manager.async_move("exact", "up", expected_revision=manager.revision())

    assert manager.match("use the careful model").rule["id"] == "exact"


async def test_move_rejects_stale_revision_without_changing_rules() -> None:
    manager, store = _manager(
        [
            _routing_rule("first", "First", order=0),
            _routing_rule("second", "Second", order=1),
        ]
    )
    stale_revision = manager.revision()
    await manager.async_update(
        "first",
        _routing_rule("first", "Updated first", order=0),
        expected_revision=stale_revision,
    )
    store.async_save.reset_mock()

    with pytest.raises(ValueError, match="changed in another tab"):
        await manager.async_move("second", "up", expected_revision=stale_revision)

    assert [rule["id"] for rule in manager.snapshot()["rules"]] == ["first", "second"]
    store.async_save.assert_not_awaited()


async def test_function_reference_rename_rejects_stale_revision() -> None:
    manager, store = _manager([_function_rule("old_tool")])
    stale_revision = manager.revision()
    await manager.async_set_defaults(
        {**DEFAULT_MATCHING, "fuzzy_threshold": 91},
        expected_revision=stale_revision,
    )
    store.async_save.reset_mock()

    with pytest.raises(ValueError, match="changed in another tab"):
        await manager.async_rename_function_reference(
            "old_tool",
            "new_tool",
            expected_revision=stale_revision,
        )

    assert manager.function_references("old_tool") == [
        {"id": "function-rule", "name": "Run configured function"}
    ]
    assert manager.function_references("new_tool") == []
    store.async_save.assert_not_awaited()
