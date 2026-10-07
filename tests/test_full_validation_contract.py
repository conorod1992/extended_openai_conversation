"""Keep expensive native lanes in the immutable full-validation campaign."""

import ast
from datetime import UTC, datetime
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def _driver(monkeypatch, tmp_path):
    for key, value in {
        "GH_API_URL": "https://api.github.com",
        "GH_REPOSITORY": "owner/repository",
        "GH_TOKEN": "test-token",
        "GH_SHA": "candidate-sha",
        "GH_REF": "candidate-branch",
        "GH_RUN_ID": "123",
        "GH_RUN_ATTEMPT": "1",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    }.items():
        monkeypatch.setenv(key, value)
    path = Path(__file__).resolve().parents[1] / "ci/full_validation.py"
    spec = importlib.util.spec_from_file_location("full_validation_driver", path)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    return driver


@pytest.mark.parametrize(
    ("workflow", "expected_ref"),
    [
        ("ios-companion-app.yml", "candidate-branch"),
        ("ci.yml", "full-validation-123-1"),
    ],
)
def test_dispatch_and_discovery_use_same_ref_and_exact_candidate(
    monkeypatch, tmp_path, workflow, expected_ref
):
    driver = _driver(monkeypatch, tmp_path)
    run = {
        "id": 42,
        "html_url": "https://github.com/owner/repository/actions/runs/42",
        "head_sha": "candidate-sha",
        "created_at": datetime.now(UTC).isoformat(),
    }
    api = Mock(side_effect=[{}, {"workflow_runs": [run]}])
    monkeypatch.setattr(driver, "api", api)
    assert driver.dispatch(workflow, {})["run_id"] == 42
    assert api.call_args_list[0].args[2]["ref"] == expected_ref
    assert f"branch={expected_ref}" in api.call_args_list[1].args[1]


def test_ios_discovery_rejects_an_advancing_source_branch(monkeypatch, tmp_path):
    driver = _driver(monkeypatch, tmp_path)
    api = Mock(
        return_value={
            "workflow_runs": [
                {
                    "head_sha": "different-sha",
                    "created_at": datetime.now(UTC).isoformat(),
                }
            ]
        }
    )
    monkeypatch.setattr(driver, "api", api)
    clock = iter([0, 0, 181])
    monkeypatch.setattr(
        driver,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda _: None),
    )
    with pytest.raises(RuntimeError, match="did not create the dispatched run"):
        driver.wait_for_run("ios-companion-app.yml", datetime.now(UTC))


def test_full_validation_dispatches_both_companion_apps_and_appliance_lanes():
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "ci/full_validation.py").read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "TEST_WORKFLOWS"
            for target in node.targets
        )
    )
    workflows = [name for name, _ in ast.literal_eval(assignment.value)]
    assert len(workflows) == len(set(workflows))
    assert {
        "android-companion-app.yml",
        "ios-companion-app.yml",
        "frontend-latency-diagnostics.yml",
        "haos-supervisor-vm-acceptance.yml",
        "ha-version-upgrade-acceptance.yml",
        "enhanced-stress.yml",
    } <= set(workflows)
    assert all((root / ".github" / "workflows" / name).is_file() for name in workflows)
