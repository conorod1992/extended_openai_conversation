"""Final genuine Request Rules coverage gap from the stable CI report."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.extended_openai_conversation_responses import request_rules as rr


class MemoryStore:
    """Minimal store for constructing a Request Rules manager."""

    async def async_load(self):
        return None

    async def async_save(self, data):
        return None


def _pattern_rule(rule_id: str, order: int, phrase: str) -> dict:
    return {
        "id": rule_id,
        "name": rule_id.replace("-", " ").title(),
        "enabled": True,
        "phrases": [phrase],
        "match_type": "sentence_pattern",
        "action_type": "local_action",
        "action": {
            "actions": [],
            "success_response": "Done",
            "failure_response": "Failed",
        },
        "matching_behavior": "defaults",
        "matching": dict(rr.DEFAULT_MATCHING),
        "order": order,
    }


def test_sort_and_compile_deactivates_pattern_that_exceeds_aggregate_state_limit() -> None:
    """The per-agent state ceiling applies across enabled patterns, not just per rule."""
    manager = rr.RequestRules(MemoryStore())
    first = _pattern_rule("first-pattern", 0, "first")
    overflow = _pattern_rule("overflow-pattern", 1, "overflow")
    manager._rules = [deepcopy(first), deepcopy(overflow)]

    compiled = {
        "first": rr.CompiledPhrase(
            "first",
            sentence_pattern=SimpleNamespace(
                capture_names=set(), state_count=rr.MAX_AGENT_PATTERN_STATES
            ),
        ),
        "overflow": rr.CompiledPhrase(
            "overflow",
            sentence_pattern=SimpleNamespace(capture_names=set(), state_count=1),
        ),
    }

    with patch.object(rr, "_compile_sentence_pattern", side_effect=compiled.__getitem__):
        changed = manager._sort_and_compile()

    assert changed is False
    assert manager._diagnostics == {
        "overflow-pattern": (
            "Sentence pattern is inactive: enabled sentence patterns exceed the "
            f"per-agent compiled state limit of {rr.MAX_AGENT_PATTERN_STATES}"
        )
    }
    assert [
        rule["id"] for rule, _settings, _phrase in manager._matching_snapshot.phrases
    ] == ["first-pattern"]
