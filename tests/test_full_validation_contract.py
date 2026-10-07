"""Keep required validation lanes complete and isolated from optional diagnostics."""

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
        ("android-companion-app.yml", "full-validation-123-1"),
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


def test_run_discovery_rejects_an_unexpected_candidate(monkeypatch, tmp_path):
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
        driver.wait_for_run("ci.yml", datetime.now(UTC))


def test_full_validation_dispatches_required_lanes_and_excludes_optional_ios():
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
    assert "ios-companion-app.yml" not in workflows
    ios = (root / ".github/workflows/ios-companion-app.yml").read_text()
    assert "workflow_dispatch:" in ios
    assert 'cron: "17 4 * * 1"' in ios
    assert {
        "android-companion-app.yml",
        "frontend-latency-diagnostics.yml",
        "haos-supervisor-vm-acceptance.yml",
        "ha-version-upgrade-acceptance.yml",
        "enhanced-stress.yml",
    } <= set(workflows)
    assert all((root / ".github" / "workflows" / name).is_file() for name in workflows)
