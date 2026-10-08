"""Execution evidence for required specialist acceptance workflows."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

REQUIRED_SOURCES = {
    "android-companion-app.yml": ("companion-app-smoke",),
    "haos-supervisor-vm-acceptance.yml": (),
    "hacs-install-update-acceptance.yml": (
        "test_hacs_installs_release_updates_candidate_and_preserves_state",
        "test_hacs_recovers_from_interrupted_candidate_replacement",
    ),
    "ipv6-only-networking-acceptance.yml": (
        "test_ipv6_only_provider_stream_rest_tool_and_failure_recovery",
    ),
    "frontend-backend-version-skew.yml": (
        "test_released_frontend_cannot_overwrite_newer_candidate_state",
    ),
    "ha-version-upgrade-acceptance.yml": (
        "test_candidate_survives_home_assistant_version_upgrades",
    ),
    "deployment-recovery.yml": (
        "test_native_home_assistant_backup_restores_populated_eoai_to_fresh_installation",
        "test_management_panel_survives_real_https_reverse_proxy",
        "test_management_panel_survives_https_certificate_rotation_in_one_ha_lifetime",
    ),
    "side-by-side-isolation.yml": (
        "test_duplicate_conversation_titles_across_parents_keep_identity",
        "test_duplicate_ai_task_titles_across_parents_route_by_entity_identity",
        "test_deleted_parent_recreated_with_same_names_gets_fresh_generation",
        "test_original_and_fork_run_side_by_side_without_cross_domain_ownership",
    ),
}


def execution_cases(report: Path) -> list[str]:
    """Read executed successful cases; a skipped or empty report proves nothing."""
    root = ET.parse(report).getroot()
    cases = list(root.iter("testcase"))
    if not cases or any(
        case.find("failure") is not None or case.find("error") is not None
        for case in cases
    ):
        raise ValueError("Specialist report is empty or failed")
    return [
        f"{case.get('classname', '')}.{case.get('name', '')}"
        for case in cases
        if case.find("skipped") is None
    ]


def specialist_errors(item: dict, workflow: str, sha: str, run: dict) -> list[str]:
    errors = []
    if (
        item.get("candidate_sha") != sha
        or item.get("workflow") != workflow
        or item.get("passed") is not True
    ):
        errors.append("Specialist candidate/workflow/result differs")
    if str(item.get("run_id")) != str(run.get("id")) or str(
        item.get("run_attempt")
    ) != str(run.get("run_attempt")):
        errors.append("Specialist run identity differs")
    cases = item.get("executed_cases")
    if (
        not isinstance(cases, list)
        or not cases
        or any(not isinstance(case, str) or not case for case in cases)
    ):
        errors.append("Specialist executed tests are missing")
        return errors
    for source in REQUIRED_SOURCES[workflow]:
        if not any(source in case for case in cases):
            errors.append(f"Required specialist tests did not execute: {source}")
    if workflow == "haos-supervisor-vm-acceptance.yml" and set(cases) != {
        "supervisor_core_restart",
        "supervisor_full_backup_restore_reboot",
    }:
        errors.append("Required Supervisor acceptance phases did not execute")
    return errors


def collect_specialist(
    actions, artifacts: list[dict], jobs: list[dict], workflow: str, sha: str, run: dict
) -> dict:
    selected = [
        item
        for item in artifacts
        if item.get("name") == "specialist-execution-evidence"
    ]
    if (
        len(selected) != 1
        or selected[0].get("expired") is True
        or selected[0].get("size_in_bytes", 1) <= 0
    ):
        raise RuntimeError(
            "Exactly one unexpired nonempty specialist execution artifact is required"
        )
    if not any(
        job.get("conclusion") == "success"
        and any(
            step.get("name") == "Record specialist execution evidence"
            and step.get("conclusion") == "success"
            for step in job.get("steps", [])
        )
        for job in jobs
    ):
        raise RuntimeError("Specialist execution evidence job was skipped or failed")
    item = actions.artifact_json(selected[0], "specialist-execution.json")
    errors = specialist_errors(item, workflow, sha, run)
    if errors:
        raise RuntimeError("; ".join(errors))
    return item


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", choices=REQUIRED_SOURCES, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--haos-proof", type=Path)
    args = parser.parse_args()
    if (
        subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        != os.environ["CANDIDATE_SHA"]
    ):
        raise ValueError("Specialist checkout differs from candidate")
    if args.haos_proof:
        proof = json.loads(args.haos_proof.read_text())
        cases = [
            phase
            for phase in (
                "supervisor_core_restart",
                "supervisor_full_backup_restore_reboot",
            )
            if proof.get(phase) is True
        ]
        if (
            proof.get("candidate_sha") != os.environ["CANDIDATE_SHA"]
            or not proof.get("provider_calls")
            or not proof.get("tool_marker")
        ):
            raise ValueError("HA OS execution proof is incomplete")
    else:
        cases = execution_cases(args.report)
    item = {
        "candidate_sha": os.environ["CANDIDATE_SHA"],
        "workflow": args.workflow,
        "passed": True,
        "run_id": os.environ["GITHUB_RUN_ID"],
        "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "executed_cases": cases,
    }
    errors = specialist_errors(
        item,
        args.workflow,
        item["candidate_sha"],
        {"id": item["run_id"], "run_attempt": item["run_attempt"]},
    )
    if errors:
        raise ValueError("; ".join(errors))
    Path("specialist-execution.json").write_text(json.dumps(item, indent=2) + "\n")


if __name__ == "__main__":
    main()
