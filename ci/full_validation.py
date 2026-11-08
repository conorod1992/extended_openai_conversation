"""Dispatch the repository's required non-live acceptance workflow set."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from ci.ha_dev_snapshot import resolve_ha_dev_sha
    from ci.validation_inventory import (
        CERTIFICATION_WORKFLOW,
        HA_DEV_WORKFLOWS,
        TEST_WORKFLOWS,
    )
except ImportError:
    from ha_dev_snapshot import resolve_ha_dev_sha
    from validation_inventory import (
        CERTIFICATION_WORKFLOW,
        HA_DEV_WORKFLOWS,
        TEST_WORKFLOWS,
    )


API_URL = os.environ["GH_API_URL"].rstrip("/")
REPOSITORY = os.environ["GH_REPOSITORY"]
TOKEN = os.environ["GH_TOKEN"]
TARGET_SHA = os.environ["GH_SHA"]
TARGET_REF = os.environ["GH_REF"]
RUN_ID = os.environ["GH_RUN_ID"]
RUN_ATTEMPT = os.environ["GH_RUN_ATTEMPT"]
REF_NAME = f"full-validation-{RUN_ID}-{RUN_ATTEMPT}"
SUMMARY_PATH = Path(os.environ["GITHUB_STEP_SUMMARY"])
STRESS_SEED = os.environ.get("FULL_VALIDATION_SEED", "") or str(secrets.randbits(32))
MODE = os.environ.get("FULL_VALIDATION_MODE", "") or "full"
if MODE not in {"full", "quick"}:
    raise SystemExit("The validation mode must be full or quick")
if not re.fullmatch(r"[0-9]+", STRESS_SEED):
    raise SystemExit("The stress seed must be an unsigned integer")


def api(method: str, endpoint: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        f"{API_URL}/repos/{REPOSITORY}/{endpoint.lstrip('/')}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(
            f"GitHub API {method} {endpoint} returned {error.code}: {detail}"
        ) from error
    except URLError as error:
        raise RuntimeError(f"GitHub API request failed: {error.reason}") from error


def wait_for_run(workflow: str, dispatched_at: datetime) -> dict:
    query = urlencode(
        {"branch": REF_NAME, "event": "workflow_dispatch", "per_page": 100}
    )
    endpoint = f"actions/workflows/{workflow}/runs?{query}"
    cutoff = dispatched_at - timedelta(seconds=30)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        runs = api("GET", endpoint).get("workflow_runs", [])
        matches = [
            run
            for run in runs
            if run.get("head_sha") == TARGET_SHA
            and datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
            >= cutoff
        ]
        if matches:
            return min(matches, key=lambda run: run["created_at"])
        time.sleep(5)
    raise RuntimeError(f"GitHub did not create the dispatched run for {workflow}")


def dispatch(workflow: str, inputs: dict[str, str]) -> dict:
    payload = {"ref": REF_NAME, "inputs": inputs}
    if workflow == "enhanced-stress.yml":
        payload["inputs"]["seed"] = STRESS_SEED
    dispatched_at = datetime.now(UTC)
    api("POST", f"actions/workflows/{workflow}/dispatches", payload)
    run = wait_for_run(workflow, dispatched_at)
    print(f"Dispatched {workflow}: {run['html_url']}", flush=True)
    return {"workflow": workflow, "run_id": run["id"], "url": run["html_url"]}


def run_status(item: dict) -> tuple[dict, dict]:
    run = api("GET", f"actions/runs/{item['run_id']}")
    return item, run


def wait_for_workflows(launched: list[dict]) -> list[tuple[dict, dict]]:
    """Wait for a cohort before dispatching any dependent certification."""
    pending = {item["run_id"]: item for item in launched}
    results: list[tuple[dict, dict]] = []
    with ThreadPoolExecutor(max_workers=min(16, max(1, len(pending)))) as pool:
        status_errors: dict[int, int] = {}
        while pending:
            completed = []
            futures = {
                run_id: pool.submit(run_status, item)
                for run_id, item in pending.items()
            }
            for run_id, future in futures.items():
                try:
                    item, run = future.result(timeout=45)
                except Exception as error:
                    print(
                        f"Status refresh failed: {error}", file=sys.stderr, flush=True
                    )
                    item = pending[run_id]
                    status_errors[run_id] = status_errors.get(run_id, 0) + 1
                    if status_errors[run_id] >= 5:
                        results.append(
                            (
                                item,
                                {
                                    "conclusion": "failure",
                                    "status": "status_unavailable",
                                },
                            )
                        )
                        completed.append(run_id)
                    continue
                if run.get("status") == "completed":
                    results.append((item, run))
                    completed.append(item["run_id"])
                    print(
                        f"Completed {item['workflow']}: {run.get('conclusion')} "
                        f"{item['url']}",
                        flush=True,
                    )
            for run_id in completed:
                pending.pop(run_id, None)
            if pending:
                time.sleep(30)
    return results


def programme_inputs(
    results: list[tuple[dict, dict]], ha_dev_sha: str
) -> dict[str, str]:
    """Use the HA version actually certified by this cohort, never a new lookup."""
    try:
        from ci.release_certification import GitHubActions
    except ModuleNotFoundError:
        from release_certification import GitHubActions

    item = next(
        item for item, _ in results if item["workflow"] == "enhanced-stress.yml"
    )
    actions = GitHubActions(REPOSITORY, TOKEN)
    indexes = [
        artifact
        for artifact in actions.run_artifacts(item["run_id"])
        if artifact["name"].startswith("certification-index-")
    ]
    if (
        len(indexes) != 1
        or indexes[0].get("expired")
        or indexes[0].get("size_in_bytes", 0) <= 0
    ):
        raise RuntimeError(
            "Exactly one nonempty Enhanced certification index is required"
        )
    index = actions.artifact_json(indexes[0], "certification-final.json")
    dev_sources = {
        row.get("environment", {}).get("homeassistant_source_commit")
        for row in index.get("jobs", [])
        if row.get("ha_point") == "dev"
    }
    if (
        index.get("passed") is not True
        or index.get("candidate_sha") != TARGET_SHA
        or index.get("selected") != "all"
        or index.get("identity_errors")
        or index.get("execution_errors")
        or dev_sources != {ha_dev_sha}
    ):
        raise RuntimeError(
            "Enhanced certification does not prove this complete candidate"
        )
    version = index.get("expected_stable_ha_version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError("Enhanced certification did not record a stable HA version")
    return {
        "candidate_sha": TARGET_SHA,
        "ha_version": version,
        "full_architecture": "true",
        "validation_ref": REF_NAME,
        "ha_dev_sha": ha_dev_sha,
    }


def cohort_is_running(run: dict) -> bool:
    if run.get("status") in {
        "queued",
        "pending",
        "in_progress",
        "waiting",
        "requested",
    }:
        return True
    if run.get("status") != "completed":
        return False
    # A completed dispatcher can still own an unfinished asynchronous cohort.
    try:
        from ci.release_certification import GitHubActions
    except ModuleNotFoundError:
        from release_certification import GitHubActions

    actions = GitHubActions(REPOSITORY, TOKEN)
    artifacts = [
        a
        for a in actions.run_artifacts(run["id"])
        if a["name"] == "full-validation-cohort" and not a.get("expired")
    ]
    if len(artifacts) != 1:
        return False
    state = actions.artifact_json(artifacts[0], "full-validation-cohort.json")
    if (
        state.get("schema") != 2
        or state.get("state") != "dispatched"
        or state.get("candidate_sha") != TARGET_SHA
        or str(state.get("run_id")) != str(run["id"])
        or str(state.get("run_attempt")) != str(run["run_attempt"])
        or type(state.get("check_id")) is not int
    ):
        return False
    check = actions.get(f"check-runs/{state['check_id']}")
    return (
        check.get("status") != "completed"
        and check.get("head_sha") == TARGET_SHA
        and check.get("external_id") == f"{run['id']}-{run['run_attempt']}"
    )


def duplicate_cohort() -> dict | None:
    """Do not dispatch the same explicitly pinned stress cohort twice.

    Unseeded cohorts intentionally sample new races, and an unresolved dev ref
    can move: neither is eligible for deduplication.
    """
    seed = os.environ.get("FULL_VALIDATION_EXPLICIT_SEED", "")
    dev = os.environ.get("FULL_VALIDATION_HA_DEV_SHA", "")
    if not seed or not dev:
        return None
    title = f"Full validation · {MODE} · seed={seed} · dev={dev}"
    query = urlencode(
        {"head_sha": TARGET_SHA, "event": "workflow_dispatch", "per_page": 100}
    )
    runs = api("GET", f"actions/workflows/full-validation.yml/runs?{query}")[
        "workflow_runs"
    ]
    return next(
        (
            run
            for run in runs
            if int(run["id"]) < int(RUN_ID)
            and run.get("head_sha") == TARGET_SHA
            and run.get("display_title") == title
            and cohort_is_running(run)
        ),
        None,
    )


def main() -> int:
    duplicate = duplicate_cohort()
    if duplicate:
        url = duplicate["html_url"]
        SUMMARY_PATH.write_text(
            f"An identical pinned cohort is already running: [follow it here]({url}).\nNo duplicate test workflows were dispatched.\n",
            encoding="utf-8",
        )
        Path("full-validation-cohort.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "passed": False,
                    "candidate_sha": TARGET_SHA,
                    "run_id": RUN_ID,
                    "duplicate_of": duplicate["id"],
                    "runs": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        # This run provides no independent certification evidence.
        print(f"Identical cohort already running: {url}", flush=True)
        return 1
    workflows = tuple(
        (workflow, dict(inputs))
        for workflow, inputs in TEST_WORKFLOWS
        if MODE != "quick"
        or workflow not in {"ios-companion-app.yml", "generator-proofs.yml"}
    )
    if MODE == "quick":
        for workflow, inputs in workflows:
            if workflow == "enhanced-stress.yml":
                inputs["campaign"] = "all-except-long-lifetime"
    ha_dev_sha = resolve_ha_dev_sha(os.environ.get("FULL_VALIDATION_HA_DEV_SHA", ""))
    print(
        f"Running {len(workflows)} test workflows ({MODE}) for {TARGET_SHA} "
        f"on isolated ref {REF_NAME}; HA dev={ha_dev_sha}; enhanced stress seed={STRESS_SEED}",
        flush=True,
    )
    api("POST", "git/refs", {"ref": f"refs/heads/{REF_NAME}", "sha": TARGET_SHA})
    launched: list[dict] = []
    launch_errors: list[tuple[str, str]] = []
    asynchronous = os.environ.get("FULL_VALIDATION_ASYNC") == "1"
    check = (
        api(
            "POST",
            "check-runs",
            {
                "name": "Full validation results",
                "head_sha": TARGET_SHA,
                "status": "in_progress",
                "external_id": f"{RUN_ID}-{RUN_ATTEMPT}",
            },
        )
        if asynchronous
        else {}
    )
    for workflow, inputs in workflows:
        try:
            pinned_inputs = dict(inputs)
            if workflow in HA_DEV_WORKFLOWS:
                pinned_inputs["ha_dev_sha"] = ha_dev_sha
            launched.append(dispatch(workflow, pinned_inputs))
        except Exception as error:
            launch_errors.append((workflow, str(error)))
            print(
                f"Could not dispatch {workflow}: {error}", file=sys.stderr, flush=True
            )
    if asynchronous:
        Path("full-validation-cohort.json").write_text(
            json.dumps(
                {
                    "schema": 2,
                    "state": "dispatched",
                    "passed": False,
                    "mode": MODE,
                    "candidate_sha": TARGET_SHA,
                    "ha_dev_sha": ha_dev_sha,
                    "stress_seed": STRESS_SEED,
                    "validation_ref": REF_NAME,
                    "run_id": RUN_ID,
                    "run_attempt": RUN_ATTEMPT,
                    "check_id": check["id"],
                    "runs": launched,
                    "launch_errors": launch_errors,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        SUMMARY_PATH.write_text(
            f"Dispatched **{MODE}** validation for `{TARGET_SHA}`.\n\n"
            "The **Full validation results** check reports the final outcome. "
            "This dispatch result is not release certification. "
            "Completion events collect results without an idle runner.\n",
            encoding="utf-8",
        )
        return 1 if launch_errors else 0
    results = wait_for_workflows(launched)
    if (
        MODE == "full"
        and not launch_errors
        and all(run.get("conclusion") == "success" for _, run in results)
    ):
        try:
            certification = dispatch(
                CERTIFICATION_WORKFLOW, programme_inputs(results, ha_dev_sha)
            )
            results.extend(wait_for_workflows([certification]))
        except Exception as error:
            launch_errors.append((CERTIFICATION_WORKFLOW, str(error)))
            print(f"Could not certify programme: {error}", file=sys.stderr, flush=True)

    failures = [
        (item["workflow"], run.get("conclusion", "unknown"), item["url"])
        for item, run in results
        if run.get("conclusion") != "success"
    ]
    failures.extend((workflow, error, "") for workflow, error in launch_errors)

    lines = [
        "## Full validation",
        "",
        f"Mode: **{MODE}**",
        f"Commit: `{TARGET_SHA}`",
        f"Home Assistant dev source: `{ha_dev_sha}`",
        f"Enhanced stress seed: `{STRESS_SEED}` (heavy)",
        f"Test workflows: {len(workflows)}",
        (
            "Quick mode skips Enhanced long-lifetime, iOS, and nightly programme "
            "release certification. This run cannot certify a release candidate."
            if MODE == "quick"
            else "Nightly programme certification runs after all test workflows succeed."
        ),
        "Live OpenAI acceptance is intentionally separate because it calls paid external APIs.",
        "",
        "| Workflow | Result | Run |",
        "|---|---|---|",
    ]
    result_by_name = {item["workflow"]: (run, item) for item, run in results}
    for workflow, _ in (*TEST_WORKFLOWS, (CERTIFICATION_WORKFLOW, {})):
        if MODE == "quick" and workflow in {
            "ios-companion-app.yml",
            "generator-proofs.yml",
            CERTIFICATION_WORKFLOW,
        }:
            lines.append(f"| `{workflow}` | skipped | quick mode |")
        elif workflow in result_by_name:
            run, item = result_by_name[workflow]
            lines.append(
                f"| `{workflow}` | {run.get('conclusion')} | [view run]({item['url']}) |"
            )
        else:
            error = next(
                (message for name, message in launch_errors if name == workflow),
                "prerequisites did not all succeed",
            )
            lines.append(f"| `{workflow}` | not dispatched | {error} |")
    lines.extend(["", f"Temporary ref `{REF_NAME}` is removed by the cleanup job."])
    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Persist the exact cohort even after its temporary branch is cleaned up.
    Path("full-validation-cohort.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "mode": MODE,
                "candidate_sha": TARGET_SHA,
                "ha_dev_sha": ha_dev_sha,
                "stress_seed": STRESS_SEED,
                "validation_ref": REF_NAME,
                "run_id": RUN_ID,
                "run_attempt": RUN_ATTEMPT,
                "passed": not failures,
                "runs": [
                    {
                        "workflow": item["workflow"],
                        "run_id": item["run_id"],
                        "run_attempt": run.get("run_attempt", 1),
                    }
                    for item, run in results
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
