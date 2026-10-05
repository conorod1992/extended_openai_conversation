"""Certify the exact source commit before the release workflow publishes it."""

from __future__ import annotations

import argparse
from io import BytesIO
import json
import os
import sys
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from zipfile import ZipFile

try:
    from .candidate_evidence import check_candidate, valid_sha
    from .enhanced_evidence import SCHEMA
    from .execution_contract import CONTRACT, check_execution
except ImportError:
    from candidate_evidence import check_candidate, valid_sha
    from enhanced_evidence import SCHEMA
    from execution_contract import CONTRACT, check_execution

REQUIRED_WORKFLOWS = (
    "ci.yml",
    "frontend.yml",
    "cross-browser-smoke.yml",
    "real-ha.yml",
    "release-smoke.yml",
    "enhanced-stress.yml",
    "upgrade-acceptance.yml",
    "openai-sdk-compatibility.yml",
)
UPGRADE_EPOCHS = ("latest", "6.8.2", "6.7.0", "6.5.0", "6.3.1", "6.2.0")
SUPPORTED_SDK_LANES = ("2.21.0", "2.45.0", "3.10.0")
ADVISORY_SDK_LANE = "latest-3x-early-warning"
HEAVY_CAMPAIGNS = (
    "runtime",
    "lifecycle",
    "ai-task",
    "voice-intercom",
    "archive",
    "feature-crossroads",
    "setup",
    "backup",
    "backup-transfer",
    "persistence",
    "request-rules",
    "guest-security",
    "quiet-hours",
    "functions",
    "provider-resilience",
    "memory-knowledge",
    "large-installation",
    "chaos",
    "process-chaos",
)
HEAVY_JOBS = frozenset(
    {"prepare", "Consolidated nightly certification"}
    | {f"{campaign} / heavy" for campaign in HEAVY_CAMPAIGNS}
    | {
        f"{browser} / heavy"
        for browser in ("browser", "browser-firefox", "browser-webkit")
    }
    | {
        f"HA {point} / shared lifecycle contract"
        for point in ("oldest", "stable", "dev")
    }
)


def qualifying_run(
    run: dict, *, workflow_id: int, source_sha: str, events=None
) -> bool:
    """Reject ancestors, other workflows, unfinished and non-successful runs."""
    return (
        run.get("workflow_id") == workflow_id
        and run.get("head_sha") == source_sha
        and run.get("head_branch") == "develop"
        and run.get("event") in (events or {"push", "workflow_dispatch"})
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
    )


def complete_heavy_nightly(run: dict, jobs: list[dict]) -> bool:
    """Require the full heavy campaign, browsers, HA matrix, and final gate."""
    if run.get("event") != "workflow_dispatch":
        return False
    successful = {job.get("name") for job in jobs if job.get("conclusion") == "success"}
    return successful >= HEAVY_JOBS


def enhanced_release_errors(index: dict, source_sha: str) -> list[str]:
    """Recheck the full heavy certificate against this exact source's contract."""
    errors = []
    if (
        index.get("schema") != SCHEMA
        or index.get("passed") is not True
        or index.get("selected") != "all"
    ):
        errors.append("Full successful enhanced certificate is required")
    if index.get("candidate_sha") != source_sha:
        errors.append(
            f"Enhanced intended candidate {index.get('candidate_sha')}, release source {source_sha}"
        )
    if index.get("execution_errors") or index.get("identity_errors"):
        errors.append(
            "Enhanced certificate contains rejected execution/identity evidence"
        )
    required = {(campaign, "heavy", None) for campaign in HEAVY_CAMPAIGNS}
    required |= {
        (f"browser{suffix}", "heavy", None) for suffix in ("", "-firefox", "-webkit")
    }
    required |= {
        ("lifecycle-matrix", "normal", point) for point in ("oldest", "stable", "dev")
    }
    jobs = index.get("jobs", [])
    keys = [
        (item.get("campaign"), item.get("intensity"), item.get("ha_point"))
        for item in jobs
    ]
    if not required <= set(keys) or len(keys) != len(set(keys)):
        errors.append("Missing/duplicate required heavy or HA lifecycle evidence")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    for item in jobs:
        if item.get("status") != "success" or item.get("schema") != SCHEMA:
            errors.append(f"Invalid enhanced envelope: {item.get('campaign')}")
        errors.extend(check_candidate(item, source_sha))
        errors.extend(check_execution(item, contract))
    return errors


def matrix_envelope_errors(
    item: dict, source_sha: str, kind: str, lane: str
) -> list[str]:
    errors = check_candidate(item, source_sha, execution=False)
    if (
        item.get("schema") != SCHEMA
        or item.get("status") != "success"
        or item.get("workflow_kind") != kind
        or item.get("lane") != lane
    ):
        errors.append(f"Missing successful {kind} evidence for {lane}")
    if kind == "sdk":
        actual = item.get("environment", {}).get("packages", {}).get("openai")
        if actual != lane:
            errors.append(f"SDK lane {lane} actually exercised {actual}")
    elif (
        not valid_sha(item.get("upgrade_source_sha"))
        or not item.get("source_version")
        or (lane != "latest" and item.get("source_version") != lane)
    ):
        errors.append(f"Upgrade lane {lane} lacks matching released payload identity")
    return errors


class _NoArtifactRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # The signed blob request must never receive the GitHub bearer token.
        return None


class GitHubActions:
    """Small, read-only GitHub Actions REST client."""

    def __init__(self, repository: str, token: str) -> None:
        self.repository = repository
        self.token = token

    def get(self, path: str, **query: object) -> dict:
        url = f"https://api.github.com/repos/{self.repository}/{path}"
        if query:
            url += "?" + urlencode(query)
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    def workflow_runs(self, filename: str, source_sha: str) -> tuple[int, list[dict]]:
        workflow_id = self.get(f"actions/workflows/{quote(filename)}")["id"]
        runs = []
        for page in range(1, 11):
            batch = self.get(
                f"actions/workflows/{workflow_id}/runs",
                head_sha=source_sha,
                per_page=100,
                page=page,
            ).get("workflow_runs", [])
            runs.extend(batch)
            if len(batch) < 100:
                break
        else:
            raise RuntimeError(
                f"Too many {filename} runs for {source_sha}; cannot certify"
            )
        return workflow_id, runs

    def run_jobs(self, run_id: int) -> list[dict]:
        result = self.get(f"actions/runs/{run_id}/jobs", per_page=100)
        if result.get("total_count", 0) > 100:
            raise RuntimeError(f"Nightly run {run_id} has too many jobs to certify")
        return result.get("jobs", [])

    def run_artifacts(self, run_id: int) -> list[dict]:
        result = self.get(f"actions/runs/{run_id}/artifacts", per_page=100)
        if result.get("total_count", 0) > 100:
            raise RuntimeError(f"Run {run_id} has too many artifacts to certify")
        return result.get("artifacts", [])

    def artifact_json(self, artifact: dict, filename: str) -> dict:
        if artifact.get("expired"):
            raise RuntimeError(f"Required artifact {artifact['name']} expired")
        request = Request(
            f"https://api.github.com/repos/{self.repository}/actions/artifacts/{artifact['id']}/zip",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
            },
        )
        opener = build_opener(_NoArtifactRedirect())
        try:
            response = opener.open(request, timeout=30)
        except HTTPError as error:
            if error.code != 302:
                raise
            location = error.headers.get("Location", "")
            error.close()
            parsed = urlsplit(location)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise RuntimeError("Invalid artifact download redirect") from None
            response = urlopen(
                Request(location), timeout=30
            )  # No authorization header.
        with response:
            archive = response.read(32 * 1024 * 1024 + 1)
        if len(archive) > 32 * 1024 * 1024:
            raise RuntimeError("Certification artifact exceeds bounded download size")
        with ZipFile(BytesIO(archive)) as zipped:
            matching = [
                entry
                for entry in zipped.infolist()
                if entry.filename.rsplit("/", 1)[-1] == filename
            ]
            if len(matching) != 1 or matching[0].file_size > 32 * 1024 * 1024:
                raise RuntimeError(
                    f"Artifact must contain exactly one bounded {filename}"
                )
            # Read the one structural JSON member; never extract archive paths.
            return json.loads(zipped.read(matching[0]))


def certify(actions: GitHubActions, source_sha: str) -> dict[str, str]:
    """Return evidence URLs or fail closed with the missing check classes."""
    if not valid_sha(source_sha):
        raise RuntimeError("Release source must be one full immutable commit SHA")
    evidence = {}
    missing = []
    for filename in REQUIRED_WORKFLOWS:
        try:
            workflow_id, runs = actions.workflow_runs(filename, source_sha)
        except Exception as exc:
            missing.append(
                f"{filename}: expected successful workflow run; metadata could not be verified ({exc})"
            )
            continue
        candidates = [
            run
            for run in runs
            if qualifying_run(
                run,
                workflow_id=workflow_id,
                source_sha=source_sha,
                events={"workflow_dispatch", "schedule"}
                if filename
                in {"upgrade-acceptance.yml", "openai-sdk-compatibility.yml"}
                else None,
            )
        ]
        validation_errors = []
        if filename in {
            "enhanced-stress.yml",
            "upgrade-acceptance.yml",
            "openai-sdk-compatibility.yml",
        }:
            complete = []
            for run in candidates:
                try:
                    jobs = actions.run_jobs(run["id"])
                    successful = {
                        job.get("name")
                        for job in jobs
                        if job.get("conclusion") == "success"
                    }
                    artifacts = actions.run_artifacts(run["id"])
                    if filename == "enhanced-stress.yml":
                        if not complete_heavy_nightly(run, jobs):
                            raise RuntimeError("Full heavy nightly jobs are required")
                        selected = [
                            item
                            for item in artifacts
                            if item["name"].startswith("certification-index-")
                        ]
                        if len(selected) != 1:
                            raise RuntimeError(
                                "Exactly one final enhanced certificate is required"
                            )
                        errors = enhanced_release_errors(
                            actions.artifact_json(
                                selected[0], "certification-final.json"
                            ),
                            source_sha,
                        )
                    else:
                        kind = (
                            "upgrade" if filename == "upgrade-acceptance.yml" else "sdk"
                        )
                        lanes = (
                            UPGRADE_EPOCHS if kind == "upgrade" else SUPPORTED_SDK_LANES
                        )
                        errors = []
                        for lane in lanes:
                            name = (
                                f"{lane} to develop"
                                if kind == "upgrade"
                                else f"SDK wire contract / {lane}"
                            )
                            if name not in successful:
                                errors.append(
                                    f"Required successful matrix lane absent: {name}"
                                )
                            selected = [
                                item
                                for item in artifacts
                                if item["name"] == f"{kind}-evidence-{lane}"
                            ]
                            if len(selected) != 1:
                                errors.append(
                                    f"Exactly one {kind} envelope is required for {lane}"
                                )
                                continue
                            errors.extend(
                                matrix_envelope_errors(
                                    actions.artifact_json(
                                        selected[0], "workflow-evidence.json"
                                    ),
                                    source_sha,
                                    kind,
                                    lane,
                                )
                            )
                        # The latest-3x lane is deliberately advisory beyond the
                        # reviewed supported ceiling; it cannot replace a supported lane.
                    if errors:
                        raise RuntimeError("; ".join(errors))
                    complete.append(run)
                except Exception as exc:
                    validation_errors.append(f"run {run['id']}: {exc}")
            candidates = complete
        if candidates:
            evidence[filename] = candidates[0]["html_url"]
        else:
            detail = (
                "complete heavy Enhanced nightly"
                if filename == "enhanced-stress.yml"
                else "successful workflow run"
            )
            suffix = (
                f"; evidence validation errors: {', '.join(validation_errors)}"
                if validation_errors
                else ""
            )
            missing.append(f"{filename}: expected {detail} on this exact SHA{suffix}")
    if missing:
        raise RuntimeError(
            f"Release source {source_sha} is not certified:\n- "
            + "\n- ".join(missing)
            + "\nA passing ancestor or another branch's latest run is insufficient."
        )
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        parser.error("GH_TOKEN or GITHUB_TOKEN is required")
    try:
        evidence = certify(GitHubActions(args.repository, token), args.sha)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Release source {args.sha} has exact-SHA certification:")
    for filename, url in evidence.items():
        print(f"- {filename}: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
