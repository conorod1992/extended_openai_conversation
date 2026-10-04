"""Replay every saved exploration reproduction in Enhanced campaigns."""

from __future__ import annotations

import json

from tests.functions.exploration_corpus_runner import CORPUS, replay_corpus_case
from tests_stress.conftest import record


def test_saved_exploration_corpus_cases_replay_to_reviewed_outcomes(
    stress_trace: list[dict],
) -> None:
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    cases = payload["cases"]
    expected_ids = [case["id"] for case in cases]
    replayed_ids: list[str] = []

    for case in cases:
        result = replay_corpus_case(case)
        replayed_ids.append(case["id"])
        record(
            stress_trace,
            "exploration_corpus_replay",
            corpus_case_id=case["id"],
            observed_outcome=result["outcome"],
            observed_failure_signature=result["failure_signature"],
            replay_evidence=result["evidence"],
        )
        assert result["outcome"] == case["expected_outcome"], case["id"]
        assert result["failure_signature"] == case["failure_signature"], case["id"]
        assert result["evidence"], case["id"]

    record(
        stress_trace,
        "summary",
        exploration_corpus_expected_case_ids=expected_ids,
        exploration_corpus_replayed_case_ids=replayed_ids,
        exploration_corpus_cases_replayed=len(replayed_ids),
    )
    assert replayed_ids == expected_ids
