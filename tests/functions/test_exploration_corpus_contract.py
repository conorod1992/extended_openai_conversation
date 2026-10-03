"""Contracts for durable enhanced-nightly exploration reproductions."""

from __future__ import annotations

import json
from pathlib import Path


CORPUS = (
    Path(__file__).resolve().parents[2]
    / "tests_stress"
    / "fixtures"
    / "exploration_corpus.json"
)


def test_exploration_corpus_has_unique_replayable_cases():
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    cases = payload["cases"]
    assert len(cases) >= 4
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert case["failure_signature"].strip()
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
