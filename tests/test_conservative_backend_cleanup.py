"""Regression tests for low-risk request-path efficiency cleanups."""

from typing import Any, cast

from custom_components.extended_openai_conversation_responses import request_rules
from custom_components.extended_openai_conversation_responses.entity import (
    _index_function_tools,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)


def test_function_tool_index_preserves_first_definition() -> None:
    """Indexing must retain the old linear lookup's first-match behavior."""
    first = {"spec": {"name": "same"}, "marker": "first"}
    second = {"spec": {"name": "same"}, "marker": "second"}
    other = {"spec": {"name": "other"}, "marker": "other"}

    indexed = _index_function_tools([first, second, other])

    assert indexed["same"] is first
    assert indexed["other"] is other


def test_request_rule_match_reuses_normalization_by_profile(monkeypatch: Any) -> None:
    """Rules sharing normalization settings should reuse the same candidate text."""
    manager = RequestRules(cast(Any, object()))
    shared_settings = {
        "word_forms": True,
        "wording_alternatives": True,
        "fuzzy": False,
        "fuzzy_threshold": 90,
    }
    alternate_settings = {
        **shared_settings,
        "word_forms": False,
    }
    manager._rules = [
        {
            "id": str(order),
            "name": phrase,
            "enabled": True,
            "match_type": "contains",
            "order": order,
            "matching_behavior": "custom",
            "matching": settings,
            "phrases": [phrase],
        }
        for order, (phrase, settings) in enumerate(
            [
                ("alpha", shared_settings),
                ("beta", dict(shared_settings)),
                ("gamma", alternate_settings),
            ]
        )
    ]
    manager._sort_and_compile()
    calls: list[tuple[bool, bool]] = []

    def fake_normalize(text: str, settings: Any, wording_groups: Any) -> str:
        calls.append(
            (
                bool(settings.get("word_forms")),
                bool(settings.get("wording_alternatives")),
            )
        )
        return "unmatched input"

    monkeypatch.setattr(request_rules, "normalize_text", fake_normalize)

    assert manager.match("utterance") is None
    assert sorted(calls) == [(False, True), (True, True)]
