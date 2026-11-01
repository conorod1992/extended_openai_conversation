"""Performance changes must preserve complete, source-bound lifetime coverage."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from ci.actions_schedule import enhanced_plan, python_matrix
from ci.enhanced_certification import merge_lifetime_partitions
from ci.execution_contract import CONTRACT, check_execution, expected_cases
from ci.pytest_execution import ExecutionLedger


def test_lifetime_partitions_preserve_every_reviewed_node(monkeypatch):
    policy = json.loads(CONTRACT.read_text())
    expected = expected_cases(policy, "long-lifetime")
    monkeypatch.setenv("STRESS_CAMPAIGN", "long-lifetime")
    rows = []
    for partition in ("reload", "uninterrupted"):
        monkeypatch.setenv("EOAI_LIFETIME_PARTITION", partition)
        items = [SimpleNamespace(nodeid=node) for node in sorted(expected)]
        ledger = ExecutionLedger()
        for item in items:
            ledger.pytest_itemcollected(item)
        ledger.pytest_collection_modifyitems(items)
        assert ledger.collected == {item.nodeid for item in items}
        rows.append(
            {
                "campaign": "long-lifetime",
                "status": "success",
                "lifetime_partition": partition,
                "execution_cases": [
                    {
                        "nodeid": item.nodeid,
                        "collected": True,
                        "executed": True,
                        "outcome": "passed",
                    }
                    for item in items
                ],
                "measured_totals": policy["minimums"]["long-lifetime"]
                if partition == "reload"
                else {},
            }
        )
    merged = merge_lifetime_partitions(rows)
    assert {case["nodeid"] for case in merged["execution_cases"]} == expected
    assert not check_execution(merged, policy)
    broken = deepcopy(rows)
    broken[1]["execution_cases"].clear()
    assert check_execution(merge_lifetime_partitions(broken), policy)


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "overlap", "sha", "environment", "seed", "metric"]
)
def test_lifetime_merge_rejects_incomplete_or_inconsistent_evidence(fault):
    rows = [
        {
            "campaign": "long-lifetime",
            "status": "success",
            "lifetime_partition": part,
            "execution_cases": [{"nodeid": part}],
            "eoai_sha": "a" * 40,
            "environment": {"python": "3.14"},
            "seed": "42",
        }
        for part in ("reload", "uninterrupted")
    ]
    if fault == "missing":
        rows.pop()
    elif fault == "duplicate":
        rows[1]["lifetime_partition"] = "reload"
    elif fault == "overlap":
        rows[1]["execution_cases"] = rows[0]["execution_cases"]
    elif fault == "metric":
        rows[1]["measured_totals"] = {"pre_gc_resource_samples": True}
    else:
        rows[1][{"sha": "eoai_sha"}.get(fault, fault)] = "wrong"
    with pytest.raises(AssertionError):
        merge_lifetime_partitions(rows)


def test_scheduled_matrix_preserves_original_intensities():
    plan = {
        "campaigns": ["runtime", "long-lifetime"],
        "intensities": ["normal", "heavy"],
    }
    rows = python_matrix(plan, scheduled=True)
    assert len(rows) == 4
    assert {r["partition"] for r in rows if r["campaign"] == "long-lifetime"} == {
        "reload",
        "uninterrupted",
    }
    assert all(
        r["intensity"] == "normal" for r in rows if r["campaign"] == "long-lifetime"
    )
    manual = python_matrix(
        enhanced_plan("workflow_dispatch", campaign="long-lifetime", intensity="all"),
        scheduled=False,
    )
    assert len(manual) == 4


def test_ios_cache_warms_default_branch_and_uses_exact_build_identity():
    from pathlib import Path

    import yaml

    workflow = yaml.safe_load(
        (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/ios-companion-app.yml"
        ).read_text(encoding="utf-8")
    )
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["push"]["branches"] == ["develop"]
    assert triggers["schedule"]
    steps = workflow["jobs"]["companion"]["steps"]
    identity = next(
        step["run"]
        for step in steps
        if step.get("name") == "Resolve exact native build cache identity"
    )
    for required in (
        "xcodebuild",
        "iphonesimulator",
        "platform.machine()",
        "IOS_SOURCE_SHA",
        "patch_ios_companion_e2e.py",
        "Package.resolved",
    ):
        assert required in identity
    caches = [
        step["with"]
        for step in steps
        if step.get("uses", "").startswith("actions/cache/")
    ]
    assert len(caches) == 4
    assert all(
        "IOS_BUILD_CACHE_ID" in cache["key"] and "restore-keys" not in cache
        for cache in caches
    )
    assert sorted(cache["key"] for cache in caches[:2]) == sorted(
        cache["key"] for cache in caches[2:]
    )


@pytest.mark.parametrize(
    "companion_result,expected", [("success", 0), ("failure", 1), (None, 1)]
)
def test_final_gate_requires_both_isolated_jobs(
    tmp_path, monkeypatch, companion_result, expected
):
    from ci import enhanced_certification as gate
    from tests.test_candidate_certification import SOURCE, lifecycle_item

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    for key, value in {
        "ENHANCED_CANDIDATE_SHA": SOURCE,
        "ENHANCED_EXPECTED_STABLE_HA_VERSION": "2026.9.4",
        "ENHANCED_CAMPAIGNS": '["long-lifetime"]',
        "ENHANCED_INTENSITIES": '["normal"]',
        "ENHANCED_SELECTED": "long-lifetime",
        "STRESS_SEED": "123",
        "ENHANCED_NEEDS": "{}",
    }.items():
        monkeypatch.setenv(key, value)
    policy = json.loads(CONTRACT.read_text())
    for partition in ("reload", "uninterrupted"):
        row = lifecycle_item("stable", "2026.9.4")
        row.pop("ha_point")
        row.update(
            campaign="long-lifetime", intensity="normal", lifetime_partition=partition
        )
        row["execution_runs"][0]["execution_id"] = partition
        row["execution_cases"] = [
            {
                "nodeid": node,
                "execution_id": partition,
                "collected": True,
                "executed": True,
                "outcome": "passed",
            }
            for node in sorted(expected_cases(policy, "long-lifetime"))
            if (
                "::test_booted_process_uninterrupted_runtime_survives_populated_lifetime"
                in node
            )
            == (partition == "uninterrupted")
        ]
        row["measured_totals"] = (
            policy["minimums"]["long-lifetime"] if partition == "reload" else {}
        )
        folder = tmp_path / partition
        folder.mkdir()
        (folder / "certification.json").write_text(json.dumps(row), encoding="utf-8")
    jobs = {"long-lifetime / normal / reload": "success"}
    if companion_result is not None:
        jobs["long-lifetime / normal / uninterrupted"] = companion_result
    monkeypatch.setattr(gate, "actual_jobs", lambda: jobs)
    assert gate.main(tmp_path) == expected


@pytest.mark.parametrize("partition", ["reload", "uninterrupted"])
def test_real_pytest_partition_records_only_assigned_cases(tmp_path, partition):
    import os
    from pathlib import Path
    import subprocess
    import sys

    suite = tmp_path / "tests_stress"
    suite.mkdir()
    companion = "test_booted_process_uninterrupted_runtime_survives_populated_lifetime"
    (suite / "test_process_resource_soak.py").write_text(
        f"def test_reload(): pass\ndef {companion}(): pass\n", encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "ci.pytest_execution",
            "--rootdir=.",
            "-o",
            "addopts=",
            "-q",
            "tests_stress",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "ENHANCED_EXECUTION_EVIDENCE": "1",
            "STRESS_CAMPAIGN": "long-lifetime",
            "EOAI_LIFETIME_PARTITION": partition,
            "STRESS_ARTIFACT_DIR": str(tmp_path / "evidence"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    ledger = json.loads(
        next((tmp_path / "evidence").glob("execution-*.json")).read_text()
    )
    assert len(ledger["cases"]) == 1
    case = ledger["cases"][0]
    assert case["outcome"] == "passed"
    assert case["nodeid"].endswith(
        companion if partition == "uninterrupted" else "test_reload"
    )
