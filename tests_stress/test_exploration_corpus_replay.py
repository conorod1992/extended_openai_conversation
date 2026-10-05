"""Replay every saved exploration reproduction in Enhanced campaigns."""

from __future__ import annotations

import json

import pytest

from custom_components.extended_openai_conversation_responses.functions import (
    file as file_module,
)
from tests.functions.exploration_corpus_runner import CORPUS, replay_corpus_case
from tests_stress.conftest import record
from tests_stress.test_os_storage_faults import real_store_io as real_store_io


async def test_saved_exploration_corpus_cases_replay_to_reviewed_outcomes(
    hass, tmp_path, monkeypatch, real_store_io,
    stress_trace: list[dict],
) -> None:
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    cases = payload["cases"]
    expected_ids = [case["id"] for case in cases]
    replayed_ids: list[str] = []

    for case in cases:
        result = await replay_corpus_case(case, hass, tmp_path, monkeypatch)
        replayed_ids.append(case["id"])
        record(
            stress_trace,
            "exploration_corpus_replay",
            corpus_case_id=case["id"],
            observed_outcome=result["outcome"],
            observed_failure_signature=result["failure_signature"],
            replay_evidence=result["evidence"],
            realised_schedule=result["operations"],
            api_schedules=result["api_schedules"],
            production_backed=result["production_backed"],
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

    # A deliberately broken native boundary must be detected by the real replay.
    file_case = next(case for case in cases if case["id"] == "native-file-replacement-conflict")
    def overwrite_external_edit(path, text, _fingerprint):
        return file_module._atomic_replace_text(path, text)

    with monkeypatch.context() as fault:
        fault.setattr(file_module, "_atomic_replace_text_if_unchanged", overwrite_external_edit)
        with pytest.raises(AssertionError):
            await replay_corpus_case(file_case, hass, tmp_path, fault)
