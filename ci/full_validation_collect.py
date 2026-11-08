"""Collect one immutable cohort on completion events, without polling idle jobs."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import sys

from ci import full_validation as driver
from ci.release_certification import GitHubActions


def selected_workflows(mode):
    if mode not in {"full", "quick"}:
        raise ValueError("Unknown validation mode")
    return {
        name
        for name, _ in driver.TEST_WORKFLOWS
        if mode == "full"
        or name not in {"ios-companion-app.yml", "generator-proofs.yml"}
    }


def validate_state(state, parent):
    """Only a genuine dispatch manifest from this exact parent may drive writes."""
    if (
        state.get("schema") != 2
        or state.get("state") != "dispatched"
        or state.get("passed") is not False
        or state.get("candidate_sha") != parent["head_sha"]
        or str(state.get("run_id")) != str(parent["id"])
        or str(state.get("run_attempt")) != str(parent["run_attempt"])
        or state.get("validation_ref")
        != f"full-validation-{parent['id']}-{parent['run_attempt']}"
        or not re.fullmatch(r"[0-9a-f]{40}", state.get("ha_dev_sha", ""))
        or not re.fullmatch(r"[0-9]+", str(state.get("stress_seed", "")))
        or type(state.get("check_id")) is not int
    ):
        raise ValueError("Cohort does not belong to the completed parent attempt")
    names = [row["workflow"] for row in state["runs"]]
    errors = [row[0] for row in state.get("launch_errors", [])]
    if (
        len(names) != len(set(names))
        or len({row["run_id"] for row in state["runs"]}) != len(names)
        or set(names) & set(errors)
        or set(names) | set(errors) != selected_workflows(state["mode"])
    ):
        raise ValueError("Cohort launch inventory differs")


def verified_run(item, state, actions):
    run = actions.get(f"actions/runs/{item['run_id']}")
    workflow = actions.get(f"actions/workflows/{item['workflow']}")
    if (
        run.get("head_sha") != state["candidate_sha"]
        or run.get("head_branch") != state["validation_ref"]
        or run.get("event") != "workflow_dispatch"
        or run.get("workflow_id") != workflow["id"]
        or run.get("run_attempt") != item.get("run_attempt", 1)
    ):
        raise ValueError("Child source, workflow, branch or attempt differs")
    return run


def collect(state, parent, actions):
    validate_state(state, parent)
    # Rebind the existing dispatch helpers to the persisted immutable cohort.
    driver.TARGET_SHA = state["candidate_sha"]
    driver.REF_NAME = state["validation_ref"]
    driver.STRESS_SEED = state["stress_seed"]
    driver.MODE = state["mode"]
    results = [(item, verified_run(item, state, actions)) for item in state["runs"]]
    if any(run["status"] != "completed" for _, run in results):
        return None
    failed = bool(state.get("launch_errors")) or any(
        run.get("conclusion") != "success" for _, run in results
    )
    if state["mode"] == "full" and not failed:
        query = f"actions/workflows/{driver.CERTIFICATION_WORKFLOW}/runs"
        candidates = actions.get(
            query,
            branch=state["validation_ref"],
            event="workflow_dispatch",
            per_page=100,
        )
        runs = candidates.get("workflow_runs", [])
        if len(runs) > 1:
            raise ValueError("Multiple programme certificates for one cohort")
        if not runs:
            driver.dispatch(
                driver.CERTIFICATION_WORKFLOW,
                driver.programme_inputs(results, state["ha_dev_sha"]),
            )
            return None
        item = {
            "workflow": driver.CERTIFICATION_WORKFLOW,
            "run_id": runs[0]["id"],
            "run_attempt": 1,
        }
        run = verified_run(item, state, actions)
        if run["status"] != "completed":
            return None
        failed = run.get("conclusion") != "success"
        results.append((item, run))
    proof = deepcopy(state)
    proof.update(
        state="completed",
        passed=not failed,
        collector_run_id=os.environ["GITHUB_RUN_ID"],
    )
    proof["runs"] = [
        {
            "workflow": item["workflow"],
            "run_id": run["id"],
            "run_attempt": run["run_attempt"],
        }
        for item, run in results
    ]
    return proof


def main():
    actions = GitHubActions(os.environ["GH_REPOSITORY"], os.environ["GH_TOKEN"])
    parent = actions.get(f"actions/runs/{os.environ['COHORT_PARENT_ID']}")
    artifacts = [
        a
        for a in actions.run_artifacts(parent["id"])
        if a["name"] == "full-validation-cohort"
    ]
    if len(artifacts) != 1:
        raise ValueError("Missing unique parent dispatch manifest")
    state = actions.artifact_json(artifacts[0], "full-validation-cohort.json")
    output = Path(os.environ["GITHUB_OUTPUT"])
    with output.open("a", encoding="utf-8") as stream:
        stream.write(
            f"artifact=full-validation-result-{parent['id']}-{parent['run_attempt']}\n"
        )
    # Duplicate completion events must not dispatch certification twice or
    # replace a terminal result. The workflow serializes events per cohort.
    existing = actions.get(
        "actions/artifacts",
        name=f"full-validation-result-{parent['id']}-{parent['run_attempt']}",
        per_page=100,
    )
    validate_state(state, parent)
    if existing.get("artifacts"):
        artifacts = existing["artifacts"]
        if len(artifacts) != 1:
            raise ValueError("Multiple terminal cohort proofs")
        artifact = artifacts[0]
        previous = actions.get(f"actions/runs/{artifact['workflow_run']['id']}")
        workflow = actions.get("actions/workflows/full-validation-collect.yml")
        proof = actions.artifact_json(artifact, "full-validation-cohort.json")
        if (
            previous.get("workflow_id") != workflow["id"]
            or previous.get("head_repository", {}).get("full_name")
            != os.environ["GH_REPOSITORY"]
            or any(
                proof.get(key) != state.get(key)
                for key in (
                    "candidate_sha",
                    "run_id",
                    "run_attempt",
                    "validation_ref",
                    "check_id",
                )
            )
        ):
            raise ValueError("Existing result is not from this cohort's collector")
        if (
            previous.get("status") != "completed"
            or previous.get("conclusion") == "success"
        ):
            return 0
        # An upload can succeed before the result/cleanup step fails. A manual
        # recovery replaces only that unsuccessful collector's bound proof.
        driver.api("DELETE", f"actions/artifacts/{artifact['id']}")
    check = actions.get(f"check-runs/{state['check_id']}")
    if (
        check.get("head_sha") != state["candidate_sha"]
        or check.get("external_id") != f"{parent['id']}-{parent['run_attempt']}"
    ):
        raise ValueError("Result check does not belong to this cohort")
    try:
        proof = collect(state, parent, actions)
    except Exception as error:
        driver.api(
            "PATCH",
            f"check-runs/{state['check_id']}",
            {
                "status": "completed",
                "conclusion": "failure",
                "output": {
                    "title": "Cohort verification failed",
                    "summary": str(error)[:2000],
                },
            },
        )
        raise
    if proof is None:
        Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text(
            "Cohort is still running. The next completion event will collect it.\n",
            encoding="utf-8",
        )
        return 0
    Path("full-validation-cohort.json").write_text(
        json.dumps(proof, indent=2) + "\n", encoding="utf-8"
    )
    with output.open("a", encoding="utf-8") as stream:
        stream.write("complete=true\n")
    summary = f"**{proof['mode']}** validation {'passed' if proof['passed'] else 'failed'} for `{proof['candidate_sha']}`."
    Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text(summary + "\n", encoding="utf-8")
    return 0


def finish():
    """Publish a terminal check only after Actions persisted the result proof."""
    proof = json.loads(Path("full-validation-cohort.json").read_text(encoding="utf-8"))
    summary = f"**{proof['mode']}** validation {'passed' if proof['passed'] else 'failed'} for `{proof['candidate_sha']}`."
    driver.api(
        "PATCH",
        f"check-runs/{proof['check_id']}",
        {
            "status": "completed",
            "conclusion": "success" if proof["passed"] else "failure",
            "details_url": f"https://github.com/{os.environ['GH_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
            "output": {"title": "Full validation results", "summary": summary},
        },
    )
    try:
        driver.api("DELETE", f"git/refs/heads/{proof['validation_ref']}")
    except RuntimeError as error:
        if "returned 404:" not in str(error):
            raise
    return 0


if __name__ == "__main__":
    raise SystemExit(finish() if sys.argv[1:] == ["finish"] else main())
