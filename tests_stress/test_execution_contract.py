"""Certification rejects missing, skipped and unexercised mandatory coverage."""

import json
from pathlib import Path

import pytest
import yaml

from ci.execution_contract import (
    CONTRACT,
    check_execution,
    expected_cases,
    python_selections,
    selected,
)

NODE = "tests_stress/test_runtime_soak.py::test_probe"
BROWSER = "tests_browser/probe.spec.mjs::chromium::probe"


def contract():
    return {
        "pytest": [NODE],
        "browser": {"runtime": [BROWSER]},
        "minimums": {"runtime": {"actual_tool_executions": 1}},
    }


def evidence():
    return {
        "campaign": "runtime",
        "execution_cases": [
            {"nodeid": node, "collected": True, "executed": True, "outcome": "passed"}
            for node in [NODE, BROWSER]
        ],
        "measured_totals": {"actual_tool_executions": 1},
    }


def test_complete_reviewed_execution_passes():
    assert check_execution(evidence(), contract()) == []


@pytest.mark.parametrize(
    "outcome", ["skipped", "xfailed", "xpassed", "failed", "deselected", "incomplete"]
)
def test_unexpected_pytest_outcomes_reject_successful_job(outcome):
    item = evidence()
    item["execution_cases"][0]["outcome"] = outcome
    assert check_execution(item, contract())


def test_missing_node_rejects_successful_job():
    item = evidence()
    item["execution_cases"].pop(0)
    assert any("absent" in error for error in check_execution(item, contract()))


def test_browser_prerequisite_missing_rejects_successful_job():
    item = evidence()
    item["execution_cases"][1].update(executed=False, outcome="skipped")
    assert any("did not pass" in error for error in check_execution(item, contract()))


def test_zero_semantic_work_rejects_successful_job():
    item = evidence()
    item["measured_totals"] = {}
    assert any(
        "required minimum" in error for error in check_execution(item, contract())
    )


def test_conditional_copy_skip_requires_a_genuine_execution():
    policy = contract()
    policy["allowances"] = {
        BROWSER: {
            "outcomes": ["skipped"],
            "requires_pass": True,
            "reason": "Fixture copy is skipped; genuine HA copy must execute",
            "review": "test review",
            "expires": "2099-01-01",
        }
    }
    item = evidence()
    item["execution_cases"].append(
        {"nodeid": BROWSER, "collected": True, "executed": False, "outcome": "skipped"}
    )
    assert check_execution(item, policy) == []
    item["execution_cases"].pop(1)
    assert check_execution(item, policy)


def test_selected_cases_and_new_test_functions_require_review():
    import ast

    policy = json.loads(CONTRACT.read_text())
    known = {node.split("[", 1)[0] for node in policy["pytest"]}
    root = Path(__file__).resolve().parents[1]
    for campaign, paths in python_selections().items():
        assert expected_cases(policy, campaign), campaign
        for selector in paths:
            file = root / selector.split("::")[0]
            files = list(file.rglob("test_*.py")) if file.is_dir() else [file]
            for path in files:
                prefix = path.relative_to(root).as_posix()

                def visit(body, parents=(), prefix=prefix):
                    for node in body:
                        if isinstance(node, ast.ClassDef):
                            yield from visit(node.body, (*parents, node.name))
                        elif isinstance(
                            node, (ast.FunctionDef, ast.AsyncFunctionDef)
                        ) and node.name.startswith("test_"):
                            yield "::".join((prefix, *parents, node.name))

                for identity in visit(ast.parse(path.read_text()).body):
                    if selected(identity, selector):
                        assert identity in known, (
                            f"New selected test needs execution-contract review: {identity}"
                        )


def test_real_pytest_ledger_records_collection_skips_xfail_and_teardown(tmp_path):
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    (tmp_path / "test_probe.py").write_text("""import pytest
@pytest.mark.skipif(True, reason='missing browser prerequisite')
def test_browser(): pass
@pytest.mark.xfail(reason='unexpected mandatory xfail')
def test_expected(): assert False
@pytest.fixture
def broken_teardown():
    yield
    assert False
def test_teardown(broken_teardown): pass
def test_ok(): pass
""")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "ci.pytest_execution",
            "-q",
            "-o",
            "addopts=",
            "test_probe.py",
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(root),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "ENHANCED_EXECUTION_EVIDENCE": "1",
            "STRESS_ARTIFACT_DIR": str(tmp_path / "evidence"),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1, result.stderr
    ledger = json.loads(
        next((tmp_path / "evidence").glob("execution-*.json")).read_text()
    )
    cases = {case["nodeid"]: case for case in ledger["cases"]}
    assert {node: case["outcome"] for node, case in cases.items()} == {
        "test_probe.py::test_browser": "skipped",
        "test_probe.py::test_expected": "xfailed",
        "test_probe.py::test_teardown": "failed",
        "test_probe.py::test_ok": "passed",
    }
    assert all(case["collected"] for case in cases.values())
    assert cases["test_probe.py::test_teardown"]["phases"]["teardown"] == "failed"


def test_reviewed_skip_exception_is_explicit_and_scoped():
    policy = contract()
    policy["allowances"] = {
        NODE: {
            "outcomes": ["skipped"],
            "requires_pass": False,
            "reason": "Explicit temporary exception",
            "review": "https://example.test/review/1",
            "expires": "2099-01-01",
        }
    }
    item = evidence()
    item["execution_cases"][0].update(executed=False, outcome="skipped")
    assert check_execution(item, policy) == []
    item["execution_cases"][1].update(executed=False, outcome="skipped")
    assert check_execution(item, policy)  # No global browser exception.


def test_expired_reviewed_allowance_fails_closed():
    policy = contract()
    policy["allowances"] = {
        NODE: {
            "outcomes": ["skipped"],
            "requires_pass": False,
            "reason": "Expired exception",
            "review": "https://example.test/review/1",
            "expires": "2000-01-01",
        }
    }
    item = evidence()
    item["execution_cases"][0].update(executed=False, outcome="skipped")
    assert any("expired" in error for error in check_execution(item, policy))


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "campaign",
    [
        "minimum-ha-features",
        "public-version-journeys",
        "native-browser-firefox",
        "native-browser-webkit",
        "native-browser-webkit-mobile",
    ],
)
def test_compatibility_requires_each_reviewed_case(campaign):
    policy = json.loads(CONTRACT.read_text(encoding="utf-8"))
    nodes = expected_cases(policy, campaign)
    assert nodes
    item = {
        "campaign": campaign,
        "execution_cases": [
            {"nodeid": node, "collected": True, "executed": True, "outcome": "passed"}
            for node in sorted(nodes)
        ],
    }
    assert not check_execution(item, policy)
    for index in range(len(item["execution_cases"])):
        altered = json.loads(json.dumps(item))
        altered["execution_cases"][index].update(executed=False, outcome="skipped")
        assert check_execution(altered, policy)
        altered["execution_cases"].pop(index)
        assert check_execution(altered, policy)


def test_only_stable_adds_native_browser_engines():
    jobs = yaml.safe_load(
        (ROOT / ".github/workflows/ha-browser-compatibility.yml").read_text(
            encoding="utf-8"
        )
    )["jobs"]
    matrix = jobs["native-browser"]["strategy"]["matrix"]
    assert matrix["ha-version"] == ["oldest", "stable", "dev"]
    assert matrix["profile"] == ["chromium"]
    assert (
        matrix["include"] == "${{ fromJson(needs.prepare.outputs.nightly-browsers) }}"
    )
    preparation = jobs["prepare"]["steps"][0]["run"]
    assert '"${{ github.event_name }}" = schedule' in preparation
    assert '"${{ github.event_name }}" = workflow_dispatch' in preparation
    steps = jobs["native-browser"]["steps"]
    assert any(
        "compatibility_evidence.py check" in step.get("run", "") for step in steps
    )

    real_ha = yaml.safe_load(
        (ROOT / ".github/workflows/real-ha.yml").read_text(encoding="utf-8")
    )["jobs"]
    public_steps = real_ha["public-journeys"]["steps"]
    run = next(
        step
        for step in public_steps
        if step["name"] == "Run reviewed HA feature compatibility contract"
    )
    for key in ["STRESS_CAMPAIGN", "ENHANCED_EXECUTION_EVIDENCE", "PYTEST_PLUGINS"]:
        assert "github.event_name == 'schedule'" in run["env"][key]
        assert "github.event_name == 'workflow_dispatch'" in run["env"][key]
    for step in public_steps:
        if step["name"] in {
            "Require every compatibility case to pass",
            "Upload feature execution evidence",
        }:
            assert "github.event_name == 'schedule'" in step["if"]
            assert "github.event_name == 'workflow_dispatch'" in step["if"]
            if step["name"] == "Require every compatibility case to pass":
                assert step["env"]["HA_POINT"] == "${{ matrix.ha-version }}"
                assert (
                    step["env"]["HA_STABLE_VERSION"]
                    == "${{ needs.prepare.outputs.stable-ha-version }}"
                )


def test_minimum_feature_contract_covers_distinct_native_boundaries():
    policy = json.loads(CONTRACT.read_text(encoding="utf-8"))
    nodes = expected_cases(policy, "minimum-ha-features")
    for file in [
        "test_public_version_journeys",
        "test_ha_llm_tool_acceptance",
        "test_native_indirect_target_resolution",
        "test_user_permission_acceptance",
        "test_local_intent_exclusions",
        "test_assist_voice_identity_precedence",
        "test_request_rules_script_semantics",
        "test_entity_registry_customization",
        "test_knowledge_provider_wire_e2e",
        "test_memory_provider_wire_e2e",
        "test_quiet_hours_acceptance",
        "test_intercom_voice_acceptance",
    ]:
        assert any(node.startswith(f"tests_real_ha/{file}.py::") for node in nodes), (
            file
        )


@pytest.mark.parametrize(
    "tested,accepted",
    [("2026.9.4", True), ("2026.10.0b0", False), ("2026.9.3", False)],
    ids=["final", "beta", "older"],
)
def test_stable_compatibility_point_cannot_follow_fixture_prerelease(tested, accepted):
    from ci.compatibility_evidence import check_frontend_point, check_stable_point

    item = {"environment": {"packages": {"homeassistant": tested}}}
    assert bool(check_stable_point(item, "stable", "2026.9.4")) != accepted
    assert check_stable_point(item, "stable", "2026.10.0b0")
    assert not check_stable_point(item, "oldest", None)
    item["environment"]["packages"]["home-assistant-frontend"] = "20260826.7"
    assert not check_frontend_point(item, "20260826.7")
    assert check_frontend_point(item, "20260930.1")
    assert check_frontend_point(item, None)


@pytest.mark.parametrize(
    "failure",
    [
        "missing-route",
        "missing-backend",
        "missing-sample",
        "wrong-sha",
        "wrong-environment",
        "nan",
    ],
)
def test_overnight_latency_evidence_rejects_incomplete_or_wrong_candidate(failure):
    from copy import deepcopy

    from ci.enhanced_evidence import environment_fingerprint
    from ci.frontend_latency.review import backend_operations, check_latency, routes

    sha = "a" * 40
    environment = {"packages": {"homeassistant": "fixture"}}
    item = {
        "eoai_sha": sha,
        "harness_sha": sha,
        "environment": environment,
        "environment_sha256": environment_fingerprint(environment),
        "runs": 3,
        "backend": {
            name: {"samples_ms": [10, 10, 10], "median_ms": 10}
            for name in backend_operations()
        },
        "browser": {
            "samples": [
                {
                    "route": route,
                    "iteration": iteration,
                    "supported": True,
                    "wall_ready_ms": 500,
                    "failures": [],
                }
                for route in routes()
                for iteration in range(1, 4)
            ]
        },
    }
    assert not check_latency(item, sha, sha)
    altered = deepcopy(item)
    if failure == "missing-route":
        altered["browser"]["samples"] = [
            sample
            for sample in altered["browser"]["samples"]
            if sample["route"] != routes()[0]
        ]
    elif failure == "missing-backend":
        altered["backend"].pop(next(iter(altered["backend"])))
    elif failure == "missing-sample":
        altered["browser"]["samples"].pop()
    elif failure == "wrong-sha":
        altered["eoai_sha"] = "b" * 40
    elif failure == "wrong-environment":
        altered["environment"]["packages"]["homeassistant"] = "changed"
    else:
        altered["browser"]["samples"][0]["wall_ready_ms"] = float("nan")
    assert check_latency(altered, sha, sha)


@pytest.mark.parametrize(
    "failure",
    [
        "missing-scan",
        "duplicate-scan",
        "empty-scanner",
        "wrong-theme",
        "serious",
        "critical",
        "expired-allowance",
    ],
)
def test_overnight_accessibility_semantics_fail_closed(failure):
    from copy import deepcopy

    from ci.enhanced_evidence import environment_fingerprint
    from ci.frontend_latency.review import check_accessibility, expected_scans

    sha = "a" * 40
    environment = {"packages": {"homeassistant": "fixture"}}
    item = {
        "eoai_sha": sha,
        "environment": environment,
        "environment_sha256": environment_fingerprint(environment),
        "scans": [
            {
                "route": route,
                "state": state,
                "theme": theme,
                "theme_colour": {
                    "background": "#111111" if theme == "dark" else "#fafafa",
                    "brightness": 17 if theme == "dark" else 250,
                },
                "width": width,
                "axe_version": "4.11.0",
                "passes": 15,
                "violations": [],
            }
            for route, state, theme, width in sorted(expected_scans())
        ],
    }
    policy = {"accessibility_allowances": []}
    assert not check_accessibility(item, sha, policy)
    altered = deepcopy(item)
    if failure == "missing-scan":
        altered["scans"].pop()
    elif failure == "duplicate-scan":
        altered["scans"].append(deepcopy(altered["scans"][0]))
    elif failure == "empty-scanner":
        altered["scans"][0]["passes"] = 0
    elif failure == "wrong-theme":
        altered["scans"][0]["theme_colour"]["brightness"] = (
            250 if altered["scans"][0]["theme"] == "dark" else 17
        )
    elif failure in {"serious", "critical"}:
        altered["scans"][0]["violations"] = [
            {"id": "label", "impact": failure, "nodes": [{"target": ["#new-control"]}]}
        ]
    else:
        policy["accessibility_allowances"] = [
            {
                "case": ["overview", "route", "light", 1280],
                "rule": "label",
                "target": ["#new-control"],
                "impact": "serious",
                "reason": "reviewed fixture",
                "reviewed_by": "repository review",
                "review_until": "2020-01-01",
            }
        ]
    assert check_accessibility(altered, sha, policy)


def test_overnight_performance_alerts_require_absolute_and_relative_growth():
    from ci.frontend_latency.compare import regression_alerts

    policy = {
        "ready_regression": {"absolute_ms": 1000, "relative": 0.5},
        "backend_regression": {"absolute_ms": 100, "relative": 1.0},
    }
    healthy = {
        "browser": {
            "route": {"baseline": {"ready_ms": 10000}, "current": {"ready_ms": 11500}}
        },
        "backend": {"read": {"baseline_ms": 1, "current_ms": 2}},
    }
    assert not regression_alerts(healthy, policy)
    healthy["browser"]["route"]["current"]["ready_ms"] = 30000
    assert len(regression_alerts(healthy, policy)) == 1


def test_frontend_quality_workflow_has_no_pr_or_push_execution():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/frontend-latency-diagnostics.yml").read_text(
            encoding="utf-8"
        )
    )
    events = workflow.get("on", workflow.get(True))
    assert set(events) == {"schedule", "workflow_dispatch"}
    steps = workflow["jobs"]["diagnose"]["steps"]
    checkout = next(
        step for step in steps if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["ref"] == "${{ github.sha }}"
    assert any(
        step.get("if") == "always()"
        and "frontend_latency.review" in step.get("run", "")
        for step in steps
    )


def test_reviewed_accessibility_allowance_cannot_hide_node_growth():
    from ci.enhanced_evidence import environment_fingerprint
    from ci.frontend_latency.review import check_accessibility, expected_scans

    environment = {"packages": {"homeassistant": "fixture"}}
    sha = "a" * 40
    scans = [
        {
            "route": r,
            "state": s,
            "theme": t,
            "theme_colour": {
                "background": "#111111" if t == "dark" else "#fafafa",
                "brightness": 17 if t == "dark" else 250,
            },
            "width": w,
            "axe_version": "4.11.0",
            "passes": 10,
            "violations": [],
        }
        for r, s, t, w in sorted(expected_scans())
    ]
    first = scans[0]
    node = {"target": ["#existing"]}
    first["violations"] = [
        {"id": "color-contrast", "impact": "serious", "nodes": [node]}
    ]
    item = {
        "eoai_sha": sha,
        "environment": environment,
        "environment_sha256": environment_fingerprint(environment),
        "scans": scans,
    }
    policy = {
        "accessibility_allowances": [
            {
                "case": [
                    first["route"],
                    first["state"],
                    first["theme"],
                    first["width"],
                ],
                "rule": "color-contrast",
                "impact": "serious",
                "target": node["target"],
                "max_nodes": 1,
                "reason": "Existing measured contrast",
                "reviewed_by": "Repository audit",
                "review_until": "2099-01-01",
            }
        ]
    }
    assert not check_accessibility(item, sha, policy)
    first["violations"][0]["nodes"].append(node)
    assert any(
        "count grew" in error for error in check_accessibility(item, sha, policy)
    )
    first["violations"] = []
    assert any("obsolete" in error for error in check_accessibility(item, sha, policy))
