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
        "error-state-serious",
        "error-state-critical",
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
    elif failure in {
        "serious",
        "critical",
        "error-state-serious",
        "error-state-critical",
    }:
        target = altered["scans"][0]
        if failure.startswith("error-state-"):
            target = next(
                scan for scan in altered["scans"] if scan["state"] == "tool-save-error"
            )
        target["violations"] = [
            {
                "id": "label",
                "impact": failure.rsplit("-", 1)[-1],
                "nodes": [{"target": ["#new-control"]}],
            }
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


@pytest.mark.parametrize("state", ["route", "tool-save-error", "destructive-error"])
def test_reviewed_accessibility_allowance_cannot_hide_node_growth(state):
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
    from ci.frontend_latency.review import reviewed_state_key

    first = next(
        scan
        for scan in scans
        if scan["route"] == "capabilities-functions" and scan["state"] == state
    )
    key = reviewed_state_key(
        (first["route"], first["state"], first["theme"], first["width"])
    )
    original = next(
        scan
        for scan in scans
        if (scan["route"], scan["state"], scan["theme"], scan["width"]) == key
    )
    node = {"target": ["#existing"]}
    first["violations"] = [
        {"id": "color-contrast", "impact": "serious", "nodes": [node]}
    ]
    if original is not first:
        from copy import deepcopy

        original["violations"] = deepcopy(first["violations"])
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
                    *key,
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
    original["violations"] = []
    assert any("obsolete" in error for error in check_accessibility(item, sha, policy))


@pytest.mark.parametrize("metric", ["heap_used", "nodes", "documents", "listeners"])
def test_browser_retention_gate_rejects_sustained_growth_but_accepts_plateau(metric):
    import subprocess

    source = """
import {retentionGrowth} from './tests_browser/browser-retention-metrics.mjs';
const metric = process.argv[1];
const base = {heap_used:1000000, nodes:500, documents:1, listeners:20};
const steps = {heap_used:4000000, nodes:1000, documents:4, listeners:100};
const stable = Array.from({length:8}, (_, i) => ({...base, heap_used:1000000 + i % 2 * 100000}));
if (retentionGrowth(stable).length) throw new Error('Plateau rejected');
const growth = Array.from({length:8}, (_, i) => ({...base, [metric]:base[metric] + i * steps[metric]}));
if (!retentionGrowth(growth).some(item => item.metric === metric)) throw new Error('Leak accepted');
const blip = stable.map(item => ({...item})); blip[4][metric] *= 100;
if (retentionGrowth(blip).length) throw new Error('Transient blip rejected');
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source, metric],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_critical_campaign_counts_reach_certification_summary(tmp_path, monkeypatch):
    """Critical traces must use the collector's reviewed summary convention."""
    import ast
    import subprocess
    import sys

    policy = json.loads(CONTRACT.read_text())
    expected = {}
    for campaign in ("functions", "large-installation", "process-chaos"):
        expected.update(policy["minimums"][campaign])
    for name in (
        "test_function_provider_wire_remaining.py",
        "test_extreme_context_matrix.py",
        "test_delayed_backlog.py",
    ):
        tree = ast.parse((ROOT / "tests_stress" / name).read_text())
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
                continue
            if call.func.id != "record" or not expected.keys() & {
                keyword.arg for keyword in call.keywords
            }:
                continue
            assert isinstance(call.args[1], ast.Constant)
            assert call.args[1].value == "summary", (
                f"{name}:{call.lineno}: critical metric discarded"
            )
    for outcome, multiplier in (("passed", 1), ("failed", 100)):
        (tmp_path / f"{outcome}.json").write_text(
            json.dumps(
                {
                    "test": f"tests_stress/test_probe.py::{outcome}",
                    "outcome": outcome,
                    "operations": [
                        {
                            "operation": "summary",
                            **{
                                key: value * multiplier
                                for key, value in expected.items()
                            },
                        }
                    ],
                }
            )
        )
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "step-summary.md"))
    result = subprocess.run(
        [sys.executable, str(ROOT / "ci" / "enhanced_summary.py"), str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads((tmp_path / "certification.json").read_text())
    assert actual["measured_totals"] == expected
    assert actual["trace_outcomes"] == {"passed": 1, "failed": 1}


@pytest.mark.parametrize("nested", ["False", "True"])
@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
def test_optional_ai_task_wire_round_trips_are_mandatory(nested, mode):
    policy = json.loads(CONTRACT.read_text())
    node = (
        "tests_real_ha/test_ai_task_provider_wire.py::"
        f"test_optional_structured_output_round_trips_provider_contract[{nested}-{mode}]"
    )
    assert node in expected_cases(policy, "ai-task")
    assert node not in policy.get("allowances", {})



def test_native_request_rule_semantics_are_mandatory():
    policy = json.loads(CONTRACT.read_text())
    cases = expected_cases(policy, "request-rules")
    prefix = "tests_real_ha/test_request_rules_script_semantics.py::"
    for name, count in {
        "test_nested_function_results_agree_in_native_action_and_speech": 5,
        "test_native_stop_enabled_decision_does_not_mask_later_abort": 15,
        "test_instrumented_stops_preserve_native_nested_scope": 8,
    }.items():
        nodes = [node for node in cases if node.startswith(prefix + name + "[")]
        assert len(nodes) == count
        assert not set(nodes) & policy.get("allowances", {}).keys()



def test_frontend_acknowledged_saves_are_mandatory():
    policy = json.loads(CONTRACT.read_text())
    cases = expected_cases(policy, "browser")
    prefix = "tests_browser/real-ha-nightly-management.spec.mjs::chromium::real-ha-nightly-management.spec.mjs::"
    for title in (
        "Guest save acknowledgement survives a failed refresh and edits in flight through genuine HA",
        "combined Request Rules settings remain current across warm and expired navigation through genuine HA",
    ):
        node = prefix + title
        assert node in cases
        assert node not in policy.get("allowances", {})
