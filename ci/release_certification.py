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
    from .specialist_evidence import collect_specialist
    from .validation_inventory import CERTIFICATION_WORKFLOW, TEST_WORKFLOWS
except ImportError:
    from candidate_evidence import check_candidate, valid_sha
    from enhanced_evidence import SCHEMA
    from execution_contract import CONTRACT, check_execution
    from specialist_evidence import collect_specialist
    from validation_inventory import CERTIFICATION_WORKFLOW, TEST_WORKFLOWS

SPECIALIST_CERTIFICATION_WORKFLOWS = (
    "android-companion-app.yml",
    "haos-supervisor-vm-acceptance.yml",
    "hacs-install-update-acceptance.yml",
    "ipv6-only-networking-acceptance.yml",
    "frontend-backend-version-skew.yml",
    "ha-version-upgrade-acceptance.yml",
    "deployment-recovery.yml",
    "side-by-side-isolation.yml",
)

REQUIRED_WORKFLOWS = (
    "ci.yml",
    "frontend.yml",
    "cross-browser-smoke.yml",
    "real-ha.yml",
    "release-smoke.yml",
    "enhanced-stress.yml",
    "upgrade-acceptance.yml",
    "openai-sdk-compatibility.yml",
    "ha-browser-compatibility.yml",
    "deployment-architecture.yml",
    "resource-constrained.yml",
    "mutation.yml",
    "official-ha-container.yml",
    *SPECIALIST_CERTIFICATION_WORKFLOWS,
)
MUTATION_CAMPAIGNS = (
    "function-tools",
    "guest-security",
    "ha-permissions",
    "request-rules",
    "function-groups",
    "contract-sensitivity",
)
SCHEDULED_CERTIFICATION_WORKFLOWS = {
    "upgrade-acceptance.yml",
    "openai-sdk-compatibility.yml",
    "ha-browser-compatibility.yml",
    "deployment-architecture.yml",
    "resource-constrained.yml",
    "mutation.yml",
    "official-ha-container.yml",
    *SPECIALIST_CERTIFICATION_WORKFLOWS,
}
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
    run: dict, *, workflow_id: int, source_sha: str, events=None, branch="develop"
) -> bool:
    """Reject ancestors, other workflows, unfinished and non-successful runs."""
    return (
        run.get("workflow_id") == workflow_id
        and run.get("head_sha") == source_sha
        and run.get("head_branch") == branch
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


def official_container_errors(
    item: dict,
    source_sha: str,
    *,
    expected_machine: str,
    expected_image_arch: str,
) -> list[str]:
    """Require a source-bound proof from the published Home Assistant image."""
    errors = []
    if item.get("candidate_sha") != source_sha:
        errors.append("Official HA Container evidence is for another candidate")
    if item.get("passed") is not True or item.get("official_container") is not True:
        errors.append("Official HA Container acceptance did not pass")
    if set(item.get("phases", [])) != {"seed", "recover", "entrypoint"}:
        errors.append("Official HA Container lifecycle evidence is incomplete")
    if not item.get("retained_entry") or not item.get("retained_entities"):
        errors.append("Official HA Container restart did not preserve runtime identity")
    if not item.get("homeassistant") or not item.get("image_id"):
        errors.append("Official HA Container environment identity is incomplete")
    if item.get("machine") != expected_machine:
        errors.append(
            f"Official HA Container runner machine {item.get('machine')} != {expected_machine}"
        )
    if item.get("container_machine") != expected_machine:
        errors.append(
            f"Official HA Container runtime machine {item.get('container_machine')} != {expected_machine}"
        )
    if item.get("image_architecture") != expected_image_arch:
        errors.append(
            f"Official HA Container image architecture {item.get('image_architecture')} != {expected_image_arch}"
        )
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


def full_validation_cohort(actions: GitHubActions, source_sha: str) -> dict | None:
    """Bind release evidence to one successful develop parent and exact child attempts."""
    workflow_id, runs = actions.workflow_runs("full-validation.yml", source_sha)
    parents = [
        run
        for run in runs
        if qualifying_run(
            run,
            workflow_id=workflow_id,
            source_sha=source_sha,
            events={"workflow_dispatch"},
        )
    ]
    errors = []
    for parent in parents:
        try:
            artifacts = [
                item
                for item in actions.run_artifacts(parent["id"])
                if item["name"] == "full-validation-cohort"
            ]
            if (
                len(artifacts) != 1
                or artifacts[0].get("expired")
                or artifacts[0].get("size_in_bytes", 1) <= 0
            ):
                raise RuntimeError(
                    "Exactly one live Full Validation cohort artifact is required"
                )
            proof = actions.artifact_json(artifacts[0], "full-validation-cohort.json")
            ref = f"full-validation-{parent['id']}-{parent['run_attempt']}"
            if (
                proof.get("schema") != 1
                or proof.get("passed") is not True
                or proof.get("candidate_sha") != source_sha
                or proof.get("validation_ref") != ref
                or str(proof.get("run_id")) != str(parent["id"])
                or str(proof.get("run_attempt")) != str(parent["run_attempt"])
            ):
                raise RuntimeError(
                    "Full Validation candidate, parent or attempt differs"
                )
            rows = proof.get("runs", [])
            by_name = {row["workflow"]: row for row in rows}
            expected = {name for name, _ in TEST_WORKFLOWS} | {CERTIFICATION_WORKFLOW}
            if (
                set(by_name) != expected
                or len(rows) != len(expected)
                or len({row["run_id"] for row in rows}) != len(rows)
            ):
                raise RuntimeError("Full Validation child inventory differs")
            children = {}
            ids = {}
            for name, row in by_name.items():
                child_workflow_id, candidates = actions.workflow_runs(name, source_sha)
                matching = [
                    run
                    for run in candidates
                    if qualifying_run(
                        run,
                        workflow_id=child_workflow_id,
                        source_sha=source_sha,
                        events={"workflow_dispatch"},
                        branch=ref,
                    )
                    and run.get("id") == row.get("run_id")
                    and run.get("run_attempt") == row.get("run_attempt")
                ]
                if len(matching) != 1:
                    raise RuntimeError(
                        f"{name}: recorded child attempt is absent, stale or unsuccessful"
                    )
                children[name] = matching[0]
                ids[name] = child_workflow_id
            programme = children[CERTIFICATION_WORKFLOW]
            artifacts = [
                item
                for item in actions.run_artifacts(programme["id"])
                if item["name"] == f"nightly-programme-{source_sha}"
            ]
            if (
                len(artifacts) != 1
                or artifacts[0].get("expired")
                or artifacts[0].get("size_in_bytes", 1) <= 0
            ):
                raise RuntimeError(
                    "Full Validation programme certificate is missing or expired"
                )
            certificate = actions.artifact_json(artifacts[0], "nightly-programme.json")
            if (
                certificate.get("candidate_sha") != source_sha
                or certificate.get("passed") is not True
                or certificate.get("errors") != []
            ):
                raise RuntimeError("Full Validation programme certificate failed")
            evidence = certificate.get("workflows", {})
            for name in REQUIRED_WORKFLOWS:
                run = evidence.get(name, {}).get("run", {})
                if (
                    run.get("id") != children[name]["id"]
                    or run.get("run_attempt") != children[name]["run_attempt"]
                    or run.get("head_branch") != ref
                ):
                    raise RuntimeError(
                        f"{name}: programme evidence escaped its recorded cohort"
                    )
            # Recheck stable/dev identity, executed cases and both native architectures.
            try:
                from .nightly_programme import programme_errors
            except ImportError:
                from nightly_programme import programme_errors
            rejected = programme_errors(
                source_sha,
                certificate.get("ha_version"),
                REQUIRED_WORKFLOWS,
                evidence,
                full_architecture=True,
            )
            if rejected:
                raise RuntimeError("; ".join(rejected))
            return {"ref": ref, "runs": children, "workflow_ids": ids}
        except (RuntimeError, KeyError, TypeError, ValueError) as error:
            errors.append(f"parent {parent['id']}: {error}")
    if errors:
        raise RuntimeError("Full Validation cohort rejected: " + "; ".join(errors))
    return None


def certify(actions: GitHubActions, source_sha: str) -> dict[str, str]:
    """Return evidence URLs or fail closed with the missing check classes."""
    if not valid_sha(source_sha):
        raise RuntimeError("Release source must be one full immutable commit SHA")
    cohort = full_validation_cohort(actions, source_sha)
    evidence = {}
    missing = []
    for filename in REQUIRED_WORKFLOWS:
        try:
            if cohort:
                workflow_id = cohort["workflow_ids"][filename]
                runs = [cohort["runs"][filename]]
            else:
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
                branch=cohort["ref"] if cohort else "develop",
                events={"workflow_dispatch", "schedule"}
                if filename in SCHEDULED_CERTIFICATION_WORKFLOWS
                else None,
            )
        ]
        validation_errors = []
        if filename in {*SPECIALIST_CERTIFICATION_WORKFLOWS,
            "enhanced-stress.yml",
            "upgrade-acceptance.yml",
            "openai-sdk-compatibility.yml",
            "mutation.yml",
            "official-ha-container.yml",
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
                    if filename in SPECIALIST_CERTIFICATION_WORKFLOWS:
                        collect_specialist(actions, artifacts, jobs, filename, source_sha, run)
                        errors = []
                    elif filename == "enhanced-stress.yml":
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
                    elif filename == "mutation.yml":
                        errors = []
                        required_jobs = {
                            f"Mutation campaign ({campaign})"
                            for campaign in MUTATION_CAMPAIGNS
                        }
                        missing_jobs = required_jobs - successful
                        if missing_jobs:
                            errors.append(
                                "Required mutation campaigns absent: "
                                + ", ".join(sorted(missing_jobs))
                            )
                    elif filename == "official-ha-container.yml":
                        errors = []
                        architectures = {
                            "amd64": ("x86_64", "amd64"),
                            "arm64": ("aarch64", "arm64"),
                        }
                        for arch, (machine, image_arch) in architectures.items():
                            job_name = f"Official HA Container runtime ({arch})"
                            if job_name not in successful:
                                errors.append(
                                    f"Required successful architecture lane absent: {job_name}"
                                )
                            selected = [
                                item
                                for item in artifacts
                                if item["name"] == f"official-ha-container-{arch}-evidence"
                            ]
                            if len(selected) != 1:
                                errors.append(
                                    f"Exactly one official HA Container {arch} evidence artifact is required"
                                )
                                continue
                            errors.extend(
                                official_container_errors(
                                    actions.artifact_json(
                                        selected[0], "certification.json"
                                    ),
                                    source_sha,
                                    expected_machine=machine,
                                    expected_image_arch=image_arch,
                                )
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
