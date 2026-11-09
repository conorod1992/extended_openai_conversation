"""Validate independently authored Memory refactor fixtures without using EOAI heuristics.

These checks protect the *test data*, not the current Memory implementation:
the gold labels remain independent of stemmers, embedding models, and LLM output.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
KEY_RE = re.compile(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)*\Z")
DECISIONS = {"create", "coexist", "update", "unchanged", "clarify", "abstain", "reject"}
FAMILIES = {"explicit", "inferred"}


def load(name: str) -> dict:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert type(data) is dict
    assert data.get("schema_version") == 1
    return data


def assert_record(record: dict, *, semantic: bool) -> None:
    assert type(record) is dict
    assert isinstance(record.get("id"), str) and record["id"]
    assert isinstance(record.get("content"), str) and record["content"].strip()
    scope = record.get("scope")
    assert isinstance(scope, str) and scope
    if semantic:
        assert scope.startswith("user:") or scope == "shared:household"
        assert record.get("source") in {"explicit", "implicit"}
    else:
        assert scope == "shared:household" or scope in {"alice", "bob"}
    assert isinstance(record.get("category"), str) and record["category"].strip()
    if "key" in record:
        assert isinstance(record["key"], str) and KEY_RE.fullmatch(record["key"])
    if "subject" in record:
        assert isinstance(record["subject"], str) and record["subject"].strip()
    if "valid_from" in record:
        assert isinstance(record["valid_from"], str) and record["valid_from"].strip()


def test_semantic_gold_labels_are_self_consistent() -> None:
    data = load("memory_refactor_semantics.json")
    cases = data["cases"]
    assert isinstance(cases, list) and len(cases) >= 30
    ids: set[str] = set()
    families: set[str] = set()
    decisions: set[str] = set()
    for case in cases:
        assert type(case) is dict
        case_id = case["id"]
        assert isinstance(case_id, str) and case_id not in ids
        ids.add(case_id)
        families.add(case["family"])
        decision = case["semantic_decision"]
        assert decision in DECISIONS
        decisions.add(decision)
        assert case["intent"] in FAMILIES
        assert isinstance(case["user_turn"], str) and case["user_turn"].strip()
        assert isinstance(case["rationale"], str) and case["rationale"].strip()
        records = case["initial"]
        assert isinstance(records, list)
        record_ids: set[str] = set()
        for record in records:
            assert_record(record, semantic=True)
            assert record["id"] not in record_ids
            record_ids.add(record["id"])
        target_ids = case["target_ids"]
        forbidden = case["forbidden_target_ids"]
        assert isinstance(target_ids, list) and isinstance(forbidden, list)
        assert all(isinstance(value, str) for value in (*target_ids, *forbidden))
        assert len(set(target_ids)) == len(target_ids)
        assert len(set(forbidden)) == len(forbidden)
        assert set(target_ids) <= record_ids
        assert set(forbidden) <= record_ids
        assert not (set(target_ids) & set(forbidden))
        if decision in {"update", "unchanged"}:
            assert len(target_ids) == 1
        else:
            assert not target_ids
        if decision == "create":
            assert not records
        if "must_preserve" in case:
            assert decision == "update"
            assert isinstance(case["must_preserve"], list)
            assert all(
                isinstance(value, str) and value.strip()
                for value in case["must_preserve"]
            )
    assert len(families) >= 8
    assert decisions == DECISIONS
    assert {case["intent"] for case in cases} == FAMILIES


def test_retrieval_fixture_has_valid_independent_gold_ids() -> None:
    data = load("memory_refactor_retrieval.json")
    records, queries = data["records"], data["queries"]
    assert isinstance(records, list) and len(records) >= 30
    assert isinstance(queries, list) and len(queries) >= 30
    by_id: dict[str, dict] = {}
    for record in records:
        assert_record(record, semantic=False)
        assert record["id"] not in by_id
        by_id[record["id"]] = record
    query_ids: set[str] = set()
    families: set[str] = set()
    for query in queries:
        assert type(query) is dict
        query_id = query["id"]
        assert isinstance(query_id, str) and query_id not in query_ids
        query_ids.add(query_id)
        assert isinstance(query["query"], str) and query["query"].strip()
        assert isinstance(query["rationale"], str) and query["rationale"].strip()
        scope = query["scope"]
        assert isinstance(scope, str) and scope in {
            "alice", "bob", "shared:household"
        }
        family = query["family"]
        assert isinstance(family, str) and family
        families.add(family)
        relevant = query["relevant_ids"]
        excluded = query["excluded_ids"]
        assert isinstance(relevant, list) and isinstance(excluded, list)
        assert all(isinstance(value, str) for value in (*relevant, *excluded))
        assert len(relevant) == len(set(relevant))
        assert len(excluded) == len(set(excluded))
        assert not (set(relevant) & set(excluded))
        assert set(relevant) | set(excluded) <= by_id.keys()
        # Gold positives must be reachable in the explicitly requested
        # owner scope; excluded cross-owner canaries intentionally may not be.
        assert all(by_id[id_]["scope"] == scope for id_ in relevant)
    assert {"scope-personal", "scope-foreign", "scope-shared"} <= families
    assert {"relation-order", "symbolic", "morphology-verb", "cjk"} <= families


@pytest.mark.parametrize(
    "name",
    ["memory_refactor_semantics.json", "memory_refactor_retrieval.json"],
)
def test_gold_fixture_has_final_newline(name: str) -> None:
    assert (FIXTURES / name).read_bytes().endswith(b"\n")
