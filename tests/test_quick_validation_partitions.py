"""Parallel execution must preserve reviewed obligations and fail closed."""

from copy import deepcopy
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from ci import enhanced_certification as gate
from ci.execution_contract import CONTRACT, check_execution, expected_cases
from tests.test_candidate_certification import SOURCE, lifecycle_item

ROOT = Path(__file__).resolve().parents[1]


def browser_rows():
    policy = json.loads(CONTRACT.read_text(encoding="utf-8"))
    expected = expected_cases(policy, "browser")
    fixture = next(node for node in expected if node in policy["allowances"])
    inventory = json.loads(
        CONTRACT.with_name("browser_partition_contract.json").read_text(
            encoding="utf-8"
        )
    )["partitions"]
    rows = []
    for part in ("static", "native"):
        row = lifecycle_item("stable", "2026.9.4")
        row.pop("ha_point")
        row.update(campaign="browser", intensity="heavy", browser_partition=part)
        row["execution_runs"][0]["execution_id"] = part
        row["execution_cases"] = [
            {
                "nodeid": node,
                "execution_id": part,
                "collected": True,
                "executed": part != "static" or node != fixture,
                "outcome": "skipped"
                if part == "static" and node == fixture
                else "passed",
            }
            for node in inventory[part]
        ]
        row["measured_totals"] = (
            policy["minimums"]["browser"] if part == "native" else {}
        )
        rows.append(row)
    return rows, policy, fixture


def test_browser_union_retains_fixture_skip_and_requires_its_genuine_pass():
    rows, policy, fixture = browser_rows()
    merged = gate.merge_browser_partitions(rows)
    assert not check_execution(merged, policy)
    assert {case["nodeid"] for case in merged["execution_cases"]} == expected_cases(
        policy, "browser"
    )
    broken = deepcopy(rows)
    broken[1]["execution_cases"] = [
        case for case in broken[1]["execution_cases"] if case["nodeid"] != fixture
    ]
    with pytest.raises(AssertionError, match="inventory mismatch"):
        gate.merge_browser_partitions(broken)


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "duplicate",
        "overlap",
        "sha",
        "seed",
        "environment",
        "source_version",
        "target_version",
        "fixture_failure",
        "native_skip",
        "missing_shared_copy",
        "missing_repeat",
        "metric",
    ],
)
def test_browser_merge_rejects_invalid_or_duplicated_evidence(fault):
    rows, _, fixture = browser_rows()
    if fault == "missing":
        rows.pop()
    elif fault == "duplicate":
        rows[1]["browser_partition"] = "static"
    elif fault == "overlap":
        rows[1]["execution_cases"].append(
            deepcopy(
                next(
                    case
                    for case in rows[0]["execution_cases"]
                    if case["nodeid"] != fixture
                )
            )
        )
    elif fault == "fixture_failure":
        next(case for case in rows[0]["execution_cases"] if case["nodeid"] == fixture)[
            "outcome"
        ] = "failed"
    elif fault == "native_skip":
        rows[1]["execution_cases"][0]["outcome"] = "skipped"
    elif fault == "missing_repeat":
        counts = Counter(case["nodeid"] for case in rows[1]["execution_cases"])
        repeated = next(node for node, count in counts.items() if count > 1)
        index = next(
            i for i, case in enumerate(rows[1]["execution_cases"])
            if case["nodeid"] == repeated
        )
        rows[1]["execution_cases"].pop(index)
    elif fault == "missing_shared_copy":
        shared = {c["nodeid"] for c in rows[0]["execution_cases"]} & {
            c["nodeid"] for c in rows[1]["execution_cases"]
        }
        node = next(node for node in shared if node != fixture)
        rows[0]["execution_cases"] = [
            c for c in rows[0]["execution_cases"] if c["nodeid"] != node
        ]
    elif fault == "metric":
        rows[0]["measured_totals"] = {"browser_creates": True}
    else:
        rows[1][{"sha": "eoai_sha"}.get(fault, fault)] = "wrong"
    with pytest.raises(AssertionError):
        gate.merge_browser_partitions(rows)


@pytest.mark.parametrize(
    "native_result,expected", [("success", 0), ("failure", 1), (None, 1)]
)
def test_browser_certificate_requires_both_actual_job_results(
    tmp_path, monkeypatch, native_result, expected
):
    rows, _, _ = browser_rows()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    for key, value in {
        "ENHANCED_CANDIDATE_SHA": SOURCE,
        "ENHANCED_EXPECTED_STABLE_HA_VERSION": "2026.9.4",
        "ENHANCED_CAMPAIGNS": '["browser"]',
        "ENHANCED_INTENSITIES": '["heavy"]',
        "ENHANCED_SELECTED": "runtime",
        "STRESS_SEED": "123",
        "ENHANCED_NEEDS": "{}",
    }.items():
        monkeypatch.setenv(key, value)
    for row in rows:
        folder = tmp_path / row["browser_partition"]
        folder.mkdir()
        (folder / "certification.json").write_text(json.dumps(row), encoding="utf-8")
    jobs = {"browser / heavy / static": "success"}
    if native_result is not None:
        jobs["browser / heavy / native"] = native_result
    monkeypatch.setattr(gate, "actual_jobs", lambda: jobs)
    assert gate.main(tmp_path) == expected


def test_python_matrices_are_disjoint_and_preserve_all_campaigns():
    result = subprocess.run(
        [
            sys.executable,
            "ci/actions_schedule.py",
            "--event",
            "workflow_dispatch",
            "--campaign",
            "all",
            "--intensity",
            "all",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    plan = {
        key: json.loads(value)
        for key, value in (line.split("=", 1) for line in result.stdout.splitlines())
    }
    main, persistence = plan["python_matrix"], plan["persistence_matrix"]
    assert all(row["campaign"] != "persistence" for row in main)
    assert {(row["campaign"], row["intensity"]) for row in persistence} == {
        ("persistence", "normal"),
        ("persistence", "heavy"),
    }
    assert {(row["campaign"], row["intensity"]) for row in main + persistence} == {
        (name, intensity)
        for name in plan["campaigns"]
        for intensity in plan["intensities"]
    }


def test_python_steps_remain_consistent_and_only_persistence_waits():
    jobs = yaml.safe_load(
        (ROOT / ".github/workflows/enhanced-stress.yml").read_text(encoding="utf-8")
    )["jobs"]
    main, persistence = jobs["python-campaigns"], jobs["persistence-campaign"]
    assert main["needs"] == "prepare"
    assert persistence["needs"] == ["prepare", "persistence_runtime"]
    persisted_steps = {step["name"]: step for step in persistence["steps"]}
    assert all(step == persisted_steps[step["name"]] for step in main["steps"])
    assert "persistence-campaign" in jobs["certify"]["needs"]
    browser = jobs["browser"]
    assert '"static","native"' in browser["strategy"]["matrix"]["partition"]
    assert "EOAI_BROWSER_PARTITION" in browser["env"]
