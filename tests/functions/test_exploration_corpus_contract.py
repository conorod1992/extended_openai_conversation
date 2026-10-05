"""Contracts for durable enhanced-nightly exploration reproductions."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from tests.functions.exploration_expected_state import ADAPTERS, expected_corpus_case

CORPUS = (
    Path(__file__).resolve().parents[2]
    / "tests_stress"
    / "fixtures"
    / "exploration_corpus.json"
)


def test_exploration_corpus_has_unique_replayable_cases():
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    cases = payload["cases"]
    assert len(cases) >= 4
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert case["failure_signature"].strip()
        assert case["expected_outcome"] == "protected"
        assert case["id"] in ADAPTERS
        assert case["environment"]["layer"].strip()
        assert len(case["operations"]) >= 3
        assert all(
            isinstance(operation, dict) and operation.get("op")
            for operation in case["operations"]
        )
        # A durable reproduction must describe the actual operation sequence.
        # Seeds are optional metadata, never the only way to reconstruct a case.
        assert not (
            set(case) <= {"id", "source", "failure_signature", "environment", "seed"}
        )


def test_exploration_corpus_keeps_distinct_failure_signatures():
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    signatures = [case["failure_signature"] for case in payload["cases"]]
    assert len(signatures) == len(set(signatures))


def test_exploration_corpus_round_trip_is_semantically_stable(tmp_path):
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    destination = tmp_path / "corpus.json"
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    assert json.loads(destination.read_text(encoding="utf-8")) == payload


def test_unknown_or_invalid_corpus_operations_fail_closed():
    cases = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]
    case = deepcopy(
        next(item for item in cases if item["id"] == "storage-lost-ack-retry")
    )

    unknown = deepcopy(case)
    unknown["operations"][0]["op"] = "invented_operation"
    with pytest.raises(ValueError, match="unknown corpus operation"):
        expected_corpus_case(unknown)

    invalid = deepcopy(case)
    invalid["operations"][0]["generation"] = "not-generation-A"
    with pytest.raises(ValueError):
        expected_corpus_case(invalid)

    extra_argument = deepcopy(case)
    extra_argument["operations"][0]["unreviewed"] = True
    with pytest.raises(ValueError, match="invalid arguments"):
        expected_corpus_case(extra_argument)

    unregistered = deepcopy(case)
    unregistered["id"] = "unregistered-case"
    with pytest.raises(ValueError, match="unregistered exploration corpus case"):
        expected_corpus_case(unregistered)
