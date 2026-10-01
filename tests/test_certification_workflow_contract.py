"""Keep candidate checkout and release matrix policy aligned with actual workflows."""

import json
from pathlib import Path
import re

import yaml

from ci.release_certification import (
    ADVISORY_SDK_LANE,
    SUPPORTED_SDK_LANES,
    UPGRADE_EPOCHS,
)

ROOT = Path(__file__).resolve().parents[1]


def workflow(filename):
    return yaml.safe_load(
        (ROOT / ".github/workflows" / filename).read_text(encoding="utf-8")
    )


def test_every_enhanced_job_checks_out_the_one_prepare_candidate():
    jobs = workflow("enhanced-stress.yml")["jobs"]
    assert (
        jobs["prepare"]["outputs"]["candidate_sha"]
        == "${{ steps.candidate.outputs.sha }}"
    )
    checkouts = []
    for name, job in jobs.items():
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                checkouts.append(name)
                assert step["with"]["ref"] == (
                    "${{ github.sha }}"
                    if name == "prepare"
                    else "${{ needs.prepare.outputs.candidate_sha }}"
                )
    assert set(checkouts) == set(jobs)
    step = next(
        step
        for step in jobs["certify"]["steps"]
        if step.get("name") == "Consolidate actual job results"
    )
    assert (
        step["env"]["ENHANCED_CANDIDATE_SHA"]
        == "${{ needs.prepare.outputs.candidate_sha }}"
    )


def test_existing_historical_matrix_supports_complete_dispatch_and_exact_checkout():
    upgrade = workflow("upgrade-acceptance.yml")["jobs"]["upgrade"]
    matrix = upgrade["strategy"]["matrix"]["from_version"]
    epochs = json.loads(re.search(r"'(\[\"latest\"[^']+\])'", matrix).group(1))
    assert epochs == list(UPGRADE_EPOCHS)
    assert "inputs.from_version == 'all'" in matrix
    checkout = next(
        step
        for step in upgrade["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert (
        checkout["with"]["ref"]
        == "${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}"
    )
    assert any(
        step.get("with", {}).get("name")
        == "upgrade-evidence-${{ matrix.from_version }}"
        and step.get("if") == "always()"
        for step in upgrade["steps"]
    )


def test_supported_sdk_policy_matches_matrix_and_manifest_ceiling():
    sdk = workflow("openai-sdk-compatibility.yml")["jobs"]["sdk-contract"]
    assert sdk["strategy"]["matrix"]["sdk"] == [*SUPPORTED_SDK_LANES, ADVISORY_SDK_LANE]
    assert sdk["continue-on-error"] == "${{ matrix.sdk == 'latest-3x-early-warning' }}"
    checkout = next(
        step
        for step in sdk["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["ref"] == "${{ github.sha }}"
    manifest = json.loads(
        (
            ROOT
            / "custom_components/extended_openai_conversation_responses/manifest.json"
        ).read_text(encoding="utf-8")
    )
    supported = next(
        value for value in manifest["requirements"] if value.startswith("openai")
    )
    floor, ceiling = re.fullmatch(r"openai>=([0-9.]+),<=([0-9.]+)", supported).groups()
    assert (SUPPORTED_SDK_LANES[0], SUPPORTED_SDK_LANES[-1]) == (floor, ceiling)
