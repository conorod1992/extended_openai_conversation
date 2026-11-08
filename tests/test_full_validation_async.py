"""Dispatch success must never substitute for completed, source-bound results."""

from copy import deepcopy
import importlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.test_full_validation_contract import DEV_SHA, _driver


def setup_cohort(monkeypatch, tmp_path, mode="full"):
    monkeypatch.setenv("FULL_VALIDATION_MODE", mode)
    driver = _driver(monkeypatch, tmp_path)
    module = importlib.import_module("ci.full_validation_collect")
    monkeypatch.setattr(module, "driver", driver)
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    state = {
        "schema": 2,
        "state": "dispatched",
        "passed": False,
        "mode": mode,
        "candidate_sha": driver.TARGET_SHA,
        "ha_dev_sha": DEV_SHA,
        "run_id": "123",
        "run_attempt": "1",
        "stress_seed": "42",
        "validation_ref": "full-validation-123-1",
        "check_id": 900,
        "runs": [
            {"workflow": name, "run_id": i, "run_attempt": 1}
            for i, name in enumerate(sorted(module.selected_workflows(mode)), 1)
        ],
        "launch_errors": [],
    }
    parent = {"id": 123, "run_attempt": 1, "head_sha": driver.TARGET_SHA}
    by_id = {row["run_id"]: row for row in state["runs"]}

    def get(path, **query):
        if path.endswith("/runs"):
            return {"workflow_runs": []}
        if path.startswith("actions/runs/"):
            row = by_id[int(path.rsplit("/", 1)[1])]
            return {
                "id": row["run_id"],
                "run_attempt": 1,
                "workflow_id": row["workflow"],
                "head_sha": state["candidate_sha"],
                "head_branch": state["validation_ref"],
                "status": "completed",
                "conclusion": "success",
                "event": "workflow_dispatch",
            }
        return {"id": path.rsplit("/", 1)[1]}

    actions = Mock(get=Mock(side_effect=get))
    return module, state, parent, actions


def test_async_launcher_persists_pending_manifest_and_never_waits(
    monkeypatch, tmp_path
):
    driver = _driver(monkeypatch, tmp_path)
    monkeypatch.setenv("FULL_VALIDATION_ASYNC", "1")
    monkeypatch.setattr(driver, "api", Mock(return_value={"id": 900}))
    monkeypatch.setattr(
        driver,
        "dispatch",
        Mock(
            side_effect=lambda name, inputs: {
                "workflow": name,
                "run_id": len(name),
                "url": "https://example.test/run",
            }
        ),
    )
    wait = Mock(side_effect=AssertionError("An asynchronous dispatcher cannot wait"))
    monkeypatch.setattr(driver, "wait_for_workflows", wait)
    monkeypatch.chdir(tmp_path)
    assert driver.main() == 0
    wait.assert_not_called()
    state = json.loads(Path("full-validation-cohort.json").read_text())
    assert state["schema"] == 2 and state["state"] == "dispatched"
    assert state["passed"] is False and state["check_id"] == 900


@pytest.mark.parametrize("completed", [False, True])
def test_duplicate_detection_follows_async_result_after_dispatch_exits(
    monkeypatch, tmp_path, completed
):
    from ci.release_certification import GitHubActions

    driver = _driver(monkeypatch, tmp_path)
    run = {"id": 100, "run_attempt": 1, "status": "completed"}
    state = {
        "schema": 2,
        "state": "dispatched",
        "candidate_sha": driver.TARGET_SHA,
        "run_id": "100",
        "run_attempt": "1",
        "check_id": 900,
    }
    monkeypatch.setattr(
        GitHubActions, "run_artifacts", lambda *_: [{"name": "full-validation-cohort"}]
    )
    monkeypatch.setattr(GitHubActions, "artifact_json", lambda *_: state)
    monkeypatch.setattr(
        GitHubActions,
        "get",
        lambda *_: {
            "status": "completed" if completed else "in_progress",
            "head_sha": driver.TARGET_SHA,
            "external_id": "100-1",
        },
    )
    assert driver.cohort_is_running(run) is (not completed)


def test_incomplete_cohort_returns_immediately(monkeypatch, tmp_path):
    module, state, parent, actions = setup_cohort(monkeypatch, tmp_path)
    original = actions.get.side_effect

    def pending(path, **query):
        result = original(path, **query)
        if path.startswith("actions/runs/"):
            result.update(status="in_progress", conclusion=None)
        return result

    actions.get.side_effect = pending
    dispatch = Mock()
    monkeypatch.setattr(module.driver, "dispatch", dispatch)
    assert module.collect(state, parent, actions) is None
    dispatch.assert_not_called()


def test_complete_quick_cohort_has_no_release_certificate(monkeypatch, tmp_path):
    module, state, parent, actions = setup_cohort(monkeypatch, tmp_path, "quick")
    proof = module.collect(state, parent, actions)
    assert proof["passed"] is True and proof["state"] == "completed"
    assert proof["collector_run_id"] == "999"
    assert not {
        "ios-companion-app.yml",
        "generator-proofs.yml",
        "nightly-programme.yml",
    } & {row["workflow"] for row in proof["runs"]}


def test_full_cohort_dispatches_certificate_only_after_all_children_pass(
    monkeypatch, tmp_path
):
    module, state, parent, actions = setup_cohort(monkeypatch, tmp_path)
    inputs = {"candidate_sha": state["candidate_sha"]}
    programme = Mock(return_value=inputs)
    dispatch = Mock()
    monkeypatch.setattr(module.driver, "programme_inputs", programme)
    monkeypatch.setattr(module.driver, "dispatch", dispatch)
    assert module.collect(state, parent, actions) is None
    programme.assert_called_once()
    dispatch.assert_called_once_with("nightly-programme.yml", inputs)


@pytest.mark.parametrize(
    "status,conclusion",
    [("in_progress", None), ("completed", "success"), ("completed", "failure")],
)
def test_certificate_completion_is_collected_without_redispatch(
    monkeypatch, tmp_path, status, conclusion
):
    module, state, parent, actions = setup_cohort(monkeypatch, tmp_path)
    original = actions.get.side_effect

    def get(path, **query):
        if path.endswith("/runs"):
            return {"workflow_runs": [{"id": 999}]}
        if path == "actions/runs/999":
            return {
                "id": 999,
                "run_attempt": 1,
                "workflow_id": "nightly-programme.yml",
                "head_sha": state["candidate_sha"],
                "head_branch": state["validation_ref"],
                "status": status,
                "conclusion": conclusion,
                "event": "workflow_dispatch",
            }
        return original(path, **query)

    actions.get.side_effect = get
    dispatch = Mock()
    monkeypatch.setattr(module.driver, "dispatch", dispatch)
    proof = module.collect(state, parent, actions)
    dispatch.assert_not_called()
    if status == "in_progress":
        assert proof is None
    else:
        assert proof["passed"] is (conclusion == "success")
        assert proof["runs"][-1]["run_id"] == 999


def test_failed_child_produces_failed_result_without_certification(
    monkeypatch, tmp_path
):
    module, state, parent, actions = setup_cohort(monkeypatch, tmp_path)
    original = actions.get.side_effect

    def get(path, **query):
        run = original(path, **query)
        if path.startswith("actions/runs/"):
            run["conclusion"] = "cancelled"
        return run

    actions.get.side_effect = get
    dispatch = Mock()
    monkeypatch.setattr(module.driver, "dispatch", dispatch)
    proof = module.collect(state, parent, actions)
    assert proof["passed"] is False
    dispatch.assert_not_called()


@pytest.mark.parametrize(
    "damage", ["candidate", "attempt", "inventory", "duplicate", "dev", "seed"]
)
def test_collector_rejects_unbound_launch_manifests(monkeypatch, tmp_path, damage):
    module, state, parent, actions = setup_cohort(monkeypatch, tmp_path)
    if damage == "candidate":
        state["candidate_sha"] = "other"
    elif damage == "attempt":
        state["run_attempt"] = "2"
    elif damage == "inventory":
        state["runs"].pop()
    elif damage == "duplicate":
        state["runs"].append(deepcopy(state["runs"][0]))
    elif damage == "dev":
        state["ha_dev_sha"] = "dev"
    elif damage == "seed":
        state["stress_seed"] = "not-a-seed"
    with pytest.raises(ValueError):
        module.collect(state, parent, actions)
    actions.get.assert_not_called()


@pytest.mark.parametrize("damage", ["source", "branch", "attempt", "workflow", "event"])
def test_collector_rejects_replaced_child_runs(monkeypatch, tmp_path, damage):
    module, state, parent, actions = setup_cohort(monkeypatch, tmp_path)
    original = actions.get.side_effect

    def damaged(path, **query):
        run = original(path, **query)
        if path.startswith("actions/runs/"):
            key, value = {
                "source": ("head_sha", "other"),
                "branch": ("head_branch", "develop"),
                "attempt": ("run_attempt", 2),
                "workflow": ("workflow_id", "other"),
                "event": ("event", "pull_request"),
            }[damage]
            run[key] = value
        return run

    actions.get.side_effect = damaged
    with pytest.raises(ValueError, match="Child"):
        module.collect(state, parent, actions)


@pytest.mark.parametrize("previous_conclusion", ["success", "failure"])
def test_completion_callbacks_are_idempotent_and_can_recover_failed_publication(
    monkeypatch, tmp_path, previous_conclusion
):
    module, state, parent, actions = setup_cohort(monkeypatch, tmp_path, "quick")
    artifact = {"id": 1, "workflow_run": {"id": 999}}
    old_proof = {**deepcopy(state), "state": "completed", "passed": True}
    original = actions.get.side_effect

    def get(path, **query):
        if path == "actions/runs/123":
            return parent
        if path == "actions/artifacts":
            return {"artifacts": [artifact]}
        if path == "actions/runs/999":
            return {
                "workflow_id": 50,
                "status": "completed",
                "conclusion": previous_conclusion,
                "head_repository": {"full_name": "owner/repository"},
            }
        if path == "actions/workflows/full-validation-collect.yml":
            return {"id": 50}
        if path == "check-runs/900":
            return {"head_sha": state["candidate_sha"], "external_id": "123-1"}
        return original(path, **query)

    actions.get.side_effect = get
    actions.run_artifacts.return_value = [{"name": "full-validation-cohort"}]
    actions.artifact_json.side_effect = lambda art, _: (
        old_proof if art is artifact else state
    )
    monkeypatch.setattr(module, "GitHubActions", lambda *_: actions)
    monkeypatch.setenv("COHORT_PARENT_ID", "123")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output.txt"))
    monkeypatch.setattr(module.driver, "api", Mock())
    collect = Mock(return_value=old_proof)
    monkeypatch.setattr(module, "collect", collect)
    monkeypatch.chdir(tmp_path)
    assert module.main() == 0
    if previous_conclusion == "success":
        collect.assert_not_called()
        module.driver.api.assert_not_called()
    else:
        module.driver.api.assert_called_once_with("DELETE", "actions/artifacts/1")
        collect.assert_called_once()
        assert "complete=true" in (tmp_path / "output.txt").read_text()
