"""Keep required validation lanes complete and isolated from optional diagnostics."""

import ast
from copy import deepcopy
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


def test_workflow_wait_does_not_finish_on_an_in_progress_run(monkeypatch, tmp_path):
    driver = _driver(monkeypatch, tmp_path)
    item = {"workflow": "ci.yml", "run_id": 42, "url": "https://example.test/run"}
    final = {"status": "completed", "conclusion": "success"}
    statuses = Mock(side_effect=[(item, {"status": "in_progress"}), (item, final)])
    sleep = Mock()
    monkeypatch.setattr(driver, "run_status", statuses)
    monkeypatch.setattr(driver.time, "sleep", sleep)
    assert driver.wait_for_workflows([item]) == [(item, final)]
    assert statuses.call_count == 2
    sleep.assert_called_once_with(30)


def test_full_validation_includes_specialists_and_accounts_for_all_workflows():
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
    assert "ios-companion-app.yml" in workflows
    assert dict(ast.literal_eval(assignment.value))["mutation.yml"] == {
        "campaign": "all"
    }
    ios = (root / ".github/workflows/ios-companion-app.yml").read_text()
    assert "workflow_dispatch:" in ios
    assert 'cron: "17 4 * * 1"' in ios
    assert {
        "android-companion-app.yml",
        "frontend-latency-diagnostics.yml",
        "haos-supervisor-vm-acceptance.yml",
        "ha-version-upgrade-acceptance.yml",
        "enhanced-stress.yml",
        "hacs.yaml",
    } <= set(workflows)
    assert all((root / ".github" / "workflows" / name).is_file() for name in workflows)
    excluded = {
        "full-validation.yml",
        "release.yml",
        "ci-image-stable.yml",
        "ci-image-dev.yml",
        "cleanup-orphan-actions-history.yml",
        "race-amplification.yml",
        "live-openai-acceptance.yml",
        "live-openai-catalog-conformance.yml",
        "live-openai-canary.yml",
        "live-openai-full-e2e.yml",
    }
    assert set(workflows) | {"nightly-programme.yml"} | excluded == {
        path.name for path in (root / ".github/workflows").iterdir()
    }


@pytest.mark.parametrize(
    "failure", [None, "test", "dispatch", "certification", "evidence"]
)
def test_final_certification_waits_for_successful_prerequisites(
    monkeypatch, tmp_path, failure
):
    driver = _driver(monkeypatch, tmp_path)
    monkeypatch.setattr(
        driver, "TEST_WORKFLOWS", (("ci.yml", {}), ("enhanced-stress.yml", {}))
    )
    monkeypatch.setattr(driver, "api", Mock())
    completed = []
    launched = []

    def dispatch(workflow, inputs):
        if workflow == driver.CERTIFICATION_WORKFLOW:
            assert completed == ["ci.yml", "enhanced-stress.yml"]
            assert inputs["candidate_sha"] == driver.TARGET_SHA
        if failure == "dispatch" and workflow == "ci.yml":
            raise RuntimeError("cannot dispatch")
        launched.append(workflow)
        return {
            "workflow": workflow,
            "run_id": len(launched),
            "url": "https://example.test/run",
        }

    def wait(items):
        completed.extend(item["workflow"] for item in items)
        return [
            (
                item,
                {
                    "conclusion": "failure"
                    if (
                        (failure == "test" and item["workflow"] == "ci.yml")
                        or (
                            failure == "certification"
                            and item["workflow"] == driver.CERTIFICATION_WORKFLOW
                        )
                    )
                    else "success"
                },
            )
            for item in items
        ]

    monkeypatch.setattr(driver, "dispatch", dispatch)
    monkeypatch.setattr(driver, "wait_for_workflows", wait)

    def inputs(_):
        if failure == "evidence":
            raise RuntimeError("invalid evidence")
        return {"candidate_sha": driver.TARGET_SHA}

    monkeypatch.setattr(driver, "programme_inputs", inputs)
    assert driver.main() == int(failure is not None)
    assert (driver.CERTIFICATION_WORKFLOW in launched) == (
        failure not in {"test", "dispatch", "evidence"}
    )
    assert "nightly-programme.yml" in driver.SUMMARY_PATH.read_text()


@pytest.mark.parametrize(
    "defect",
    [None, "candidate", "failed", "partial", "empty", "expired", "version", "errors"],
)
def test_programme_uses_this_runs_verified_environment(monkeypatch, tmp_path, defect):
    from ci.release_certification import GitHubActions

    driver = _driver(monkeypatch, tmp_path)
    artifact = {
        "name": "certification-index-123",
        "size_in_bytes": 100,
        "expired": False,
    }
    index = {
        "passed": True,
        "candidate_sha": driver.TARGET_SHA,
        "selected": "all",
        "expected_stable_ha_version": "2026.10.0",
        "identity_errors": [],
        "execution_errors": [],
    }
    changes = {
        "candidate": {"candidate_sha": "other"},
        "failed": {"passed": False},
        "partial": {"selected": "runtime"},
        "version": {"expected_stable_ha_version": None},
        "errors": {"execution_errors": ["missing mandatory case"]},
    }
    index.update(changes.get(defect, {}))
    if defect == "empty":
        artifact["size_in_bytes"] = 0
    if defect == "expired":
        artifact["expired"] = True
    artifacts = Mock(return_value=[artifact])
    monkeypatch.setattr(GitHubActions, "run_artifacts", artifacts)
    monkeypatch.setattr(GitHubActions, "artifact_json", lambda *_: deepcopy(index))
    results = [
        ({"workflow": "enhanced-stress.yml", "run_id": 42}, {"conclusion": "success"})
    ]
    if defect:
        with pytest.raises(RuntimeError):
            driver.programme_inputs(results)
    else:
        assert driver.programme_inputs(results) == {
            "candidate_sha": driver.TARGET_SHA,
            "ha_version": "2026.10.0",
            "full_architecture": "true",
        }
    artifacts.assert_called_once_with(42)
