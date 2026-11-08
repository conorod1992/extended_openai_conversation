"""Full generator proofs; run weekly, for generator changes and release candidates."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses import agent_config
from tests_stress.generated_valid_states import (
    TRIPLE_GROUPS,
    generate,
    normalized_state,
    obligations,
)

CONTRACT = (
    Path(__file__).resolve().parents[1] / "tests_stress/agent_field_contract.json"
)


def test_covering_generator_is_valid_complete_bounded_and_reproducible() -> None:
    first = generate(2983703646, heavy=True)
    second = generate(2983703646, heavy=True)
    assert first == second
    assert first.cases
    reviewed = json.loads(
        CONTRACT.with_name("reviewed_valid_coverage.json").read_text(encoding="utf-8")
    )
    first.assert_reviewed_inventory(reviewed)
    with pytest.raises(AssertionError, match="exclusion reasons changed"):
        replace(
            first, obligations=frozenset(list(first.obligations)[1:])
        ).assert_reviewed_inventory(reviewed)
    with pytest.raises(AssertionError, match="exclusion reasons changed"):
        replace(
            first, excluded={**first.excluded, "unexpected rejection": 1}
        ).assert_reviewed_inventory(reviewed)
    assert len(first.cases) < 500
    assert first.exploratory_count <= 15
    actual = set()
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    participating = {
        getattr(agent_config, name): set()
        for name, item in contract["fields"].items()
        if item["coverage"]["kind"] == "combinatorial_dimension"
    }
    for case in first.cases:
        normalized, api = normalized_state(case)
        assert normalized["chat_model"] == case["chat_model"]
        assert api in {"responses", "chat_completions"}
        actual.update(obligations(case))
        for field, seen in participating.items():
            seen.add(json.dumps(normalized.get(field), sort_keys=True))
    assert first.obligations <= actual
    assert all(len(seen) > 1 for seen in participating.values()), {
        field: len(seen) for field, seen in participating.items() if len(seen) <= 1
    }
    assert all(len(item) == 2 for item in first.obligations if len(item) == 2)
    assert {
        tuple(key for key, _ in item) for item in first.obligations if len(item) == 3
    } == set(TRIPLE_GROUPS)
    with pytest.raises(AssertionError, match="budget"):
        generate(2983703646, heavy=False, budget=1)


def test_seed_changes_equivalent_covering_choice() -> None:
    one = generate(2983703646, heavy=False)
    other = generate(2983703647, heavy=False)
    # Compare compact inventories: pytest's diff of thousands of obligations can
    # itself exceed the campaign timeout when the coverage boundary regresses.
    assert one.reviewed_inventory() == other.reviewed_inventory()
    assert one.cases != other.cases
