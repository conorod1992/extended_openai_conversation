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

# These workflows own their environments and test selection. Keep this list in
# sync with test-bearing workflow_dispatch workflows; live API acceptance,
# release publishing, image publishing, supplementary race amplification,
# and maintenance are separate from this validation run.
TEST_WORKFLOWS = (
    ("ci.yml", {}),
    ("frontend.yml", {}),
    ("real-ha.yml", {}),
    ("enhanced-stress.yml", {"campaign": "all", "intensity": "heavy"}),
    ("release-smoke.yml", {}),
    ("cross-browser-smoke.yml", {}),
    ("upgrade-acceptance.yml", {"from_version": "all"}),
    ("frontend-backend-version-skew.yml", {}),
    ("ha-version-upgrade-acceptance.yml", {}),
    ("hacs-install-update-acceptance.yml", {}),
    ("deployment-recovery.yml", {}),
    ("side-by-side-isolation.yml", {}),
    ("ipv6-only-networking-acceptance.yml", {}),
    ("ha-browser-compatibility.yml", {}),
    ("android-companion-app.yml", {}),
    ("ios-companion-app.yml", {}),
    ("mutation.yml", {"campaign": "all"}),
    ("hacs.yaml", {}),
    ("frontend-latency-diagnostics.yml", {"runs": "3", "diagnostic_picker": "false"}),
    ("haos-supervisor-vm-acceptance.yml", {}),
    ("official-ha-container.yml", {}),
    ("resource-constrained.yml", {}),
    ("deployment-architecture.yml", {}),
    ("docs.yml", {}),
    ("openai-sdk-compatibility.yml", {}),
    ("version-check.yml", {}),
)
CERTIFICATION_WORKFLOW = "nightly-programme.yml"

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


def programme_inputs(results: list[tuple[dict, dict]]) -> dict[str, str]:
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
    if (
        index.get("passed") is not True
        or index.get("candidate_sha") != TARGET_SHA
        or index.get("selected") != "all"
        or index.get("identity_errors")
        or index.get("execution_errors")
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
    }


def main() -> int:
    print(
        f"Running {len(TEST_WORKFLOWS)} test workflows for {TARGET_SHA} "
        f"on isolated ref {REF_NAME}; enhanced stress seed={STRESS_SEED}",
        flush=True,
    )
    api("POST", "git/refs", {"ref": f"refs/heads/{REF_NAME}", "sha": TARGET_SHA})
    launched: list[dict] = []
    launch_errors: list[tuple[str, str]] = []
    for workflow, inputs in TEST_WORKFLOWS:
        try:
            launched.append(dispatch(workflow, dict(inputs)))
        except Exception as error:
            launch_errors.append((workflow, str(error)))
            print(
                f"Could not dispatch {workflow}: {error}", file=sys.stderr, flush=True
            )
    results = wait_for_workflows(launched)
    if not launch_errors and all(
        run.get("conclusion") == "success" for _, run in results
    ):
        try:
            certification = dispatch(CERTIFICATION_WORKFLOW, programme_inputs(results))
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
        f"Commit: `{TARGET_SHA}`",
        f"Enhanced stress seed: `{STRESS_SEED}` (heavy)",
        f"Test workflows: {len(TEST_WORKFLOWS)}",
        "Nightly programme certification runs after all test workflows succeed.",
        "Live OpenAI acceptance is intentionally separate because it calls paid external APIs.",
        "",
        "| Workflow | Result | Run |",
        "|---|---|---|",
    ]
    result_by_name = {item["workflow"]: (run, item) for item, run in results}
    for workflow, _ in (*TEST_WORKFLOWS, (CERTIFICATION_WORKFLOW, {})):
        if workflow in result_by_name:
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
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
