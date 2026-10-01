"""Certification rejects missing, skipped and unexercised mandatory coverage."""

import json
from pathlib import Path

import pytest

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
