"""Certify required nightly evidence together for one immutable candidate."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ci.candidate_evidence import check_candidate, valid_sha
from ci.compatibility_evidence import check_ha_environment
from ci.execution_contract import check_execution
from ci.release_certification import (
    HEAVY_CAMPAIGNS,
    MUTATION_CAMPAIGNS,
    REQUIRED_WORKFLOWS,
    SPECIALIST_CERTIFICATION_WORKFLOWS,
    SUPPORTED_SDK_LANES,
    UPGRADE_EPOCHS,
    GitHubActions,
    matrix_envelope_errors,
    official_container_errors,
)

from ci.specialist_evidence import collect_specialist, specialist_errors

WORKFLOWS = REQUIRED_WORKFLOWS


def programme_errors(candidate_sha, stable_version, required, evidence, *, full_architecture=False):
    """Reject missing workflows, stale candidates and independently drifting HA."""
    errors = []
    if not valid_sha(candidate_sha) or not stable_version:
        return ["Programme requires an immutable candidate and resolved HA version"]
    if set(evidence) != set(required):
        errors.append("Required nightly workflow inventory differs from evidence")
    dev_sources = set()
    for workflow, row in evidence.items():
        run = row["run"]
        if run.get("head_sha") != candidate_sha or run.get("status") != "completed" or run.get("conclusion") != "success":
            errors.append(f"{workflow}: missing successful exact-candidate run")
        if workflow == "deployment-architecture.yml":
            proofs = row.get("isolated", [])
            if {proof.get("machine") for proof in proofs} != {"x86_64", "aarch64"} or len(proofs) != 2:
                errors.append("Isolated runtime requires both native architecture proofs")
            for proof in proofs:
                if proof.get("candidate_sha") != candidate_sha or proof.get("passed") is not True or proof.get("homeassistant") != stable_version:
                    errors.append("Isolated runtime candidate/environment/result differs")
                if set(proof.get("phases", [])) != {"seed", "recover", "recover-recorder-first", "recover-provider-first", "auth"}:
                    errors.append("Isolated runtime cold-start evidence is incomplete")
        if workflow in SPECIALIST_CERTIFICATION_WORKFLOWS:
            errors += specialist_errors(row.get("specialist", {}), workflow, candidate_sha, run)
        if workflow == "official-ha-container.yml":
            proofs = row.get("official", {})
            expected = {"amd64", "arm64"} if full_architecture else {"amd64"}
            if not isinstance(proofs, dict) or not expected <= set(proofs) or set(proofs) - {"amd64", "arm64"}:
                errors.append("Official HA Container architecture evidence is missing")
            else:
                for arch, proof in proofs.items():
                    errors += [f"{workflow}/{arch}: {error}" for error in official_container_errors(
                        proof, candidate_sha, expected_machine="x86_64" if arch == "amd64" else "aarch64", expected_image_arch=arch)]
                    if proof.get("homeassistant") != stable_version:
                        errors.append("Official HA Container did not exercise resolved stable HA")
        for envelope in row.get("envelopes", []):
            if envelope.get("status") != "success":
                errors.append(f"{workflow}: unsuccessful evidence envelope")
            errors += [f"{workflow}: {error}" for error in check_candidate(envelope, candidate_sha, execution=bool(envelope.get("execution_runs")))]
            if envelope.get("ha_point") not in {"oldest", "dev"}:
                errors += [f"{workflow}: {error}" for error in check_ha_environment(envelope, version=stable_version)]
            elif envelope.get("ha_point") == "dev":
                source = envelope.get("environment", {}).get("homeassistant_source_commit")
                if not valid_sha(source):
                    errors.append(f"{workflow}: missing immutable HA dev source")
                dev_sources.add(source)
            else:
                minimum = json.loads((Path(__file__).resolve().parents[1] / "hacs.json").read_text(encoding="utf-8"))["homeassistant"]
                errors += check_ha_environment(envelope, version=minimum)
            if envelope.get("execution_runs"):
                errors += [f"{workflow}: {error}" for error in check_execution(envelope)]
        if workflow in {"enhanced-stress.yml", "upgrade-acceptance.yml", "openai-sdk-compatibility.yml", "ha-browser-compatibility.yml"} and not row.get("envelopes"):
            errors.append(f"{workflow}: missing required source/environment-bound artifacts")
    if len(dev_sources) > 1:
        errors.append("Nightly workflows exercised different HA dev snapshots")
    return errors


def collect(actions, candidate_sha, *, validation_ref=None):
    evidence = {}
    for workflow in WORKFLOWS:
        workflow_id, runs = actions.workflow_runs(workflow, candidate_sha)
        candidates = [run for run in runs if run.get("workflow_id") == workflow_id and run.get("head_sha") == candidate_sha and run.get("status") == "completed" and run.get("conclusion") == "success" and run.get("event") in {"push", "schedule", "workflow_dispatch"} and (not validation_ref or run.get("head_branch") == validation_ref)]
        if not candidates:
            continue
        run = candidates[0]
        artifacts = actions.run_artifacts(run["id"])
        envelopes = []
        isolated = []
        official = {}
        specialist = None
        if workflow == "mutation.yml":
            jobs = actions.run_jobs(run["id"])
            successful = {
                job.get("name")
                for job in jobs
                if job.get("conclusion") == "success"
            }
            required = {
                f"Mutation campaign ({campaign})"
                for campaign in MUTATION_CAMPAIGNS
            }
            if not required <= successful:
                raise RuntimeError("Nightly mutation campaign inventory is incomplete")
        if workflow == "enhanced-stress.yml":
            indexes = [artifact for artifact in artifacts if artifact["name"].startswith("certification-index-")]
            if len(indexes) != 1:
                raise RuntimeError("Exactly one Enhanced index is required")
            index = actions.artifact_json(indexes[0], "certification-final.json")
            if index.get("passed") is not True or index.get("candidate_sha") != candidate_sha or index.get("selected") != "all" or index.get("execution_errors") or index.get("identity_errors"):
                raise RuntimeError("Enhanced programme evidence was rejected")
            envelopes = index["jobs"]
            required_campaigns = {*HEAVY_CAMPAIGNS, "browser", "browser-firefox", "browser-webkit", "lifecycle-matrix"}
            if not required_campaigns <= {item.get("campaign") for item in envelopes}:
                raise RuntimeError("Enhanced programme inventory is incomplete")
        elif workflow in {"upgrade-acceptance.yml", "openai-sdk-compatibility.yml"}:
            kind = "upgrade" if workflow == "upgrade-acceptance.yml" else "sdk"
            for lane in UPGRADE_EPOCHS if kind == "upgrade" else SUPPORTED_SDK_LANES:
                selected = [artifact for artifact in artifacts if artifact["name"] == f"{kind}-evidence-{lane}"]
                if len(selected) != 1:
                    raise RuntimeError(f"Missing {kind} evidence for {lane}")
                envelope = actions.artifact_json(selected[0], "workflow-evidence.json")
                errors = matrix_envelope_errors(envelope, candidate_sha, kind, lane)
                if errors:
                    raise RuntimeError("; ".join(errors))
                envelopes.append(envelope)
        elif workflow == "ha-browser-compatibility.yml":
            for point in ("oldest", "stable", "dev"):
                selected = [artifact for artifact in artifacts if artifact["name"] == f"ha-native-browser-{point}-chromium"]
                if len(selected) != 1:
                    raise RuntimeError(f"Missing native browser evidence for {point}")
                envelopes.append(actions.artifact_json(selected[0], "certification.json"))
        if workflow == "deployment-architecture.yml":
            for architecture in ("x86_64-native", "arm64-native"):
                selected = [artifact for artifact in artifacts if artifact["name"] == f"isolated-deployment-{architecture}"]
                if len(selected) != 1:
                    raise RuntimeError(f"Missing isolated runtime evidence for {architecture}")
                isolated.append(actions.artifact_json(selected[0], "certification.json"))
        if workflow in SPECIALIST_CERTIFICATION_WORKFLOWS:
            specialist = collect_specialist(actions, artifacts, actions.run_jobs(run["id"]), workflow, candidate_sha, run)
        if workflow == "official-ha-container.yml":
            jobs = actions.run_jobs(run["id"])
            for arch in ("amd64", "arm64"):
                selected = [artifact for artifact in artifacts if artifact["name"] == f"official-ha-container-{arch}-evidence"]
                if arch == "arm64" and not selected:
                    continue
                if len(selected) != 1 or selected[0].get("expired") or selected[0].get("size_in_bytes", 1) <= 0:
                    raise RuntimeError(f"Missing official HA Container evidence for {arch}")
                if not any(job.get("name") == f"Official HA Container runtime ({arch})" and job.get("conclusion") == "success" for job in jobs):
                    raise RuntimeError(f"Official HA Container {arch} job did not succeed")
                official[arch] = actions.artifact_json(selected[0], "certification.json")
        evidence[workflow] = {
            "run": run,
            "envelopes": envelopes,
            "isolated": isolated,
            "official": official,
            "specialist": specialist,
        }
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--ha-version", required=True)
    parser.add_argument("--full-architecture", action="store_true")
    parser.add_argument("--validation-ref")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    actions = GitHubActions(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    try:
        evidence = collect(actions, args.candidate, validation_ref=args.validation_ref)
        errors = programme_errors(args.candidate, args.ha_version, WORKFLOWS, evidence, full_architecture=args.full_architecture)
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        evidence, errors = {}, [str(error)]
    args.output.write_text(json.dumps({"candidate_sha": args.candidate, "ha_version": args.ha_version, "passed": not errors, "errors": errors, "workflows": evidence}, indent=2) + "\n", encoding="utf-8")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
