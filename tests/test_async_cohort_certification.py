"""A successful dispatch is insufficient to authorize release publication."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from ci.release_certification import completed_async_cohort


@pytest.mark.parametrize("complete", [False, True])
def test_release_gate_reads_completed_async_cohort_instead_of_dispatch_success(
    complete,
):
    from ci.release_certification import REQUIRED_WORKFLOWS, certify
    from tests.test_release_certification import SOURCE, CohortActions

    actions = CohortActions()
    actions.repository = "owner/repo"
    actions.proof.update(
        schema=2,
        state="dispatched",
        passed=False,
        mode="full",
        ha_dev_sha="d" * 40,
        stress_seed="42",
        check_id=900,
    )
    completed = deepcopy(actions.proof)
    completed.update(state="completed", passed=True, collector_run_id="999")
    actions.artifacts[999] = []
    actions._artifact(999, "full-validation-result-100-1", completed)
    actions.artifacts[999][0]["workflow_run"] = {"id": 999}

    def get(path, **query):
        if path == "actions/artifacts":
            return {"artifacts": actions.artifacts[999] if complete else []}
        if path == "actions/workflows/full-validation-collect.yml":
            return {"id": 500}
        assert path == "actions/runs/999"
        return {
            "id": 999,
            "workflow_id": 500,
            "status": "completed",
            "conclusion": "success",
            "event": "workflow_run",
            "head_repository": {"full_name": "owner/repo"},
        }

    actions.get = get
    if complete:
        assert set(certify(actions, SOURCE)) == set(REQUIRED_WORKFLOWS)
    else:
        with pytest.raises(RuntimeError, match="no unique live result"):
            certify(actions, SOURCE)


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "absent",
        "expired",
        "workflow",
        "failed",
        "repository",
        "candidate",
        "attempt",
        "pending",
    ],
)
def test_async_release_requires_trusted_completed_parent_bound_proof(damage):
    parent = {"id": 123, "run_attempt": 1}
    launch = {
        "candidate_sha": "a" * 40,
        "run_id": "123",
        "run_attempt": "1",
        "validation_ref": "full-validation-123-1",
        "ha_dev_sha": "b" * 40,
        "stress_seed": "42",
        "mode": "full",
        "check_id": 100,
    }
    artifact = {"expired": False, "workflow_run": {"id": 999}}
    run = {
        "id": 999,
        "workflow_id": 50,
        "status": "completed",
        "conclusion": "success",
        "event": "workflow_run",
        "head_repository": {"full_name": "owner/repo"},
    }
    proof = {
        **deepcopy(launch),
        "schema": 2,
        "state": "completed",
        "passed": True,
        "collector_run_id": "999",
    }
    artifacts = [artifact]
    if damage == "absent":
        artifacts = []
    elif damage == "expired":
        artifact["expired"] = True
    elif damage == "workflow":
        run["workflow_id"] = 51
    elif damage == "failed":
        run["conclusion"] = "failure"
    elif damage == "repository":
        run["head_repository"]["full_name"] = "attacker/fork"
    elif damage == "candidate":
        proof["candidate_sha"] = "c" * 40
    elif damage == "attempt":
        proof["run_attempt"] = "2"
    elif damage == "pending":
        proof["state"] = "dispatched"
    actions = Mock(repository="owner/repo")
    actions.get.side_effect = lambda path, **kw: (
        {"artifacts": artifacts}
        if path == "actions/artifacts"
        else run
        if path.startswith("actions/runs/")
        else {"id": 50}
    )
    actions.artifact_json.return_value = proof
    if damage:
        with pytest.raises(RuntimeError):
            completed_async_cohort(actions, parent, launch)
    else:
        assert completed_async_cohort(actions, parent, launch) == proof
