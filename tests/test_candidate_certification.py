"""A successful run agreeing on the wrong commit must still fail certification."""

from copy import deepcopy
import json
import subprocess
from types import SimpleNamespace

import pytest

from ci import enhanced_certification as gate, enhanced_evidence as evidence
from ci.candidate_evidence import check_candidate
from ci.execution_contract import CONTRACT, expected_cases

SOURCE = "b" * 40
WRONG = "a" * 40


def lifecycle_item(point, version):
    """Otherwise-valid, source-bound evidence with the real mandatory inventory."""
    row = item()
    identity = {
        "python": "3.14.8",
        "packages": {"homeassistant": version, "openai": "2.45.0"},
        "homeassistant_source_commit": "d" * 40 if point == "dev" else None,
    }
    envelope = {
        "eoai_sha": SOURCE,
        "environment": identity,
        "environment_fingerprint": evidence.environment_fingerprint(identity),
    }
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    row.update(
        **envelope,
        campaign="lifecycle-matrix",
        ha_point=point,
        ha_version=version,
        execution_runs=[{**envelope, "execution_id": "probe", "runner": "pytest"}],
        execution_cases=[
            {
                "nodeid": node,
                "execution_id": "probe",
                "collected": True,
                "executed": True,
                "outcome": "passed",
            }
            for node in sorted(expected_cases(contract, "lifecycle-matrix"))
        ],
        measured_totals=contract.get("minimums", {}).get("lifecycle-matrix", {}),
    )
    return row


@pytest.mark.parametrize(
    "resolved,reported,installed,expected",
    [
        ("2026.9.4", "2026.9.4", "2026.9.4", 0),
        ("2026.9.4", "2026.9.3", "2026.9.3", 1),
        ("2026.9.4", "2026.10.0b1", "2026.10.0b1", 1),
        (None, "2026.9.4", "2026.9.4", 1),
        ("", "2026.9.4", "2026.9.4", 1),
        ("2026.10.0b1", "2026.10.0b1", "2026.10.0b1", 1),
        ("2026.9.4", None, "2026.9.4", 1),
        ("2026.9.4", "2026.9.4", None, 1),
        ("2026.9.4", "2026.9.3", "2026.9.4", 1),
    ],
)
def test_final_lifecycle_gate_requires_independently_resolved_stable_version(
    tmp_path, monkeypatch, resolved, reported, installed, expected
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("ENHANCED_EXPECTED_STABLE_HA_VERSION", raising=False)
    for key, value in {
        "ENHANCED_CANDIDATE_SHA": SOURCE,
        "ENHANCED_CAMPAIGNS": "[]",
        "ENHANCED_INTENSITIES": '["normal"]',
        "ENHANCED_SELECTED": "lifecycle",
        "STRESS_SEED": "123",
        "ENHANCED_NEEDS": "{}",
    }.items():
        monkeypatch.setenv(key, value)
    if resolved is not None:
        monkeypatch.setenv("ENHANCED_EXPECTED_STABLE_HA_VERSION", resolved)
    monkeypatch.setattr(
        gate,
        "actual_jobs",
        lambda: {
            f"HA {point} / shared lifecycle contract": "success"
            for point in ("oldest", "stable", "dev")
        },
    )
    rows = {
        "oldest": lifecycle_item("oldest", "2026.3.0b0"),
        "stable": lifecycle_item("stable", installed),
        "dev": lifecycle_item("dev", "2026.10.0.dev0"),
    }
    if reported is None:
        rows["stable"].pop("ha_version")
    else:
        rows["stable"]["ha_version"] = reported
    for point, row in rows.items():
        directory = tmp_path / point
        directory.mkdir()
        (directory / "certification.json").write_text(json.dumps(row), encoding="utf-8")
    assert gate.main(tmp_path) == expected
    index = json.loads((tmp_path / "certification-final.json").read_text())
    assert index["passed"] is (expected == 0)
    assert not index["execution_errors"]
    assert all("Stable" in error for error in index["identity_errors"])
    if expected:
        assert index["identity_errors"]
    else:
        assert index["expected_stable_ha_version"] == resolved
        recorded = {row["ha_point"]: row for row in index["jobs"]}
        # The floor may intentionally be a beta, while dev retains its own Core
        # identity and runtime fingerprint instead of taking the stable version.
        assert recorded["oldest"]["ha_version"] == "2026.3.0b0"
        assert recorded["dev"]["ha_version"] == "2026.10.0.dev0"
        assert recorded["dev"]["environment"]["homeassistant_source_commit"] == "d" * 40


def item(sha=SOURCE):
    identity = {"python": "3.14.0", "packages": {"openai": "3.10.0"}}
    env = {
        "eoai_sha": sha,
        "environment": identity,
        "environment_fingerprint": evidence.environment_fingerprint(identity),
    }
    return {
        **env,
        "schema": evidence.SCHEMA,
        "campaign": "runtime",
        "intensity": "normal",
        "status": "success",
        "execution_runs": [{**env, "execution_id": "probe", "runner": "pytest"}],
        "execution_cases": [
            {
                "nodeid": "probe",
                "execution_id": "probe",
                "collected": True,
                "executed": True,
                "outcome": "passed",
            }
        ],
    }


@pytest.mark.parametrize(
    "event,omitted,expected",
    [
        ("schedule", ("long-lifetime", "heavy"), 0),
        ("workflow_dispatch", ("long-lifetime", "heavy"), 1),
        ("schedule", ("long-lifetime", "normal"), 1),
        ("schedule", ("runtime", "heavy"), 1),
        ("workflow_dispatch", None, 0),
    ],
)
def test_final_gate_requires_every_row_of_the_actual_lifetime_matrix(
    tmp_path, monkeypatch, event, omitted, expected
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    for key, value in {
        "GITHUB_EVENT_NAME": event,
        "ENHANCED_CANDIDATE_SHA": SOURCE,
        "ENHANCED_EXPECTED_STABLE_HA_VERSION": "2026.9.4",
        "ENHANCED_CAMPAIGNS": '["runtime","long-lifetime"]',
        "ENHANCED_INTENSITIES": '["normal","heavy"]',
        "ENHANCED_SELECTED": "runtime",
        "STRESS_SEED": "123",
        "ENHANCED_NEEDS": "{}",
    }.items():
        monkeypatch.setenv(key, value)
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    jobs = {}
    for campaign in ("runtime", "long-lifetime"):
        for intensity in ("normal", "heavy"):
            if (campaign, intensity) == omitted:
                continue
            row = lifecycle_item("stable", "2026.9.4")
            row.pop("ha_point")
            row.update(
                campaign=campaign,
                intensity=intensity,
                execution_cases=[
                    {
                        "nodeid": node,
                        "execution_id": "probe",
                        "collected": True,
                        "executed": True,
                        "outcome": "passed",
                    }
                    for node in sorted(expected_cases(contract, campaign))
                ],
                measured_totals=contract.get("minimums", {}).get(campaign, {}),
            )
            directory = tmp_path / f"{campaign}-{intensity}"
            directory.mkdir()
            (directory / "certification.json").write_text(json.dumps(row), encoding="utf-8")
            jobs[f"{campaign} / {intensity}"] = "success"
    monkeypatch.setattr(gate, "actual_jobs", lambda: jobs)
    assert gate.main(tmp_path) == expected
    index = json.loads((tmp_path / "certification-final.json").read_text())
    assert index["passed"] is (expected == 0)
    assert not index["identity_errors"]
    assert not index["execution_errors"]


@pytest.mark.parametrize(
    "shas,expected", [([SOURCE, SOURCE], 0), ([WRONG, WRONG], 1), ([SOURCE, WRONG], 1)]
)
def test_real_final_gate_requires_intended_candidate_even_when_jobs_agree(
    tmp_path, monkeypatch, shas, expected
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("ENHANCED_EXPECTED_STABLE_HA_VERSION", raising=False)
    for key, value in {
        "ENHANCED_CANDIDATE_SHA": SOURCE,
        "ENHANCED_CAMPAIGNS": '["runtime","functions"]',
        "ENHANCED_INTENSITIES": '["normal"]',
        "ENHANCED_SELECTED": "runtime",
        "STRESS_SEED": "123",
        "ENHANCED_NEEDS": "{}",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        gate,
        "actual_jobs",
        lambda: {"runtime / normal": "success", "functions / normal": "success"},
    )
    # Execution completeness has its own negative tests; isolate candidate binding.
    monkeypatch.setattr(gate, "check_execution", lambda _: [])
    for campaign, sha in zip(("runtime", "functions"), shas, strict=True):
        row = {**item(sha), "campaign": campaign}
        directory = tmp_path / campaign
        directory.mkdir()
        (directory / "certification.json").write_text(json.dumps(row), encoding="utf-8")
    assert gate.main(tmp_path) == expected
    index = json.loads(
        (tmp_path / "certification-final.json").read_text(encoding="utf-8")
    )
    assert index["candidate_sha"] == SOURCE
    assert index["passed"] is (expected == 0)
    if expected:
        assert any(
            SOURCE in error and WRONG in error for error in index["identity_errors"]
        )


def test_summary_cannot_relabel_an_execution_ledger_from_another_commit():
    row = item()
    row["execution_runs"][0]["eoai_sha"] = WRONG
    assert any(WRONG in error for error in check_candidate(row, SOURCE))


def test_every_case_requires_an_identifiable_ledger():
    row = item()
    row["execution_cases"][0]["execution_id"] = "absent"
    assert any("no source-bound" in error for error in check_candidate(row, SOURCE))


def test_environment_change_requires_its_actual_fingerprint():
    row = item()
    row["environment"]["packages"]["openai"] = "2.21.0"
    assert any("fingerprint" in error for error in check_candidate(row, SOURCE))


def test_git_failure_cannot_substitute_the_invocation_sha(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", SOURCE)

    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(evidence.subprocess, "check_output", failed)
    assert evidence.checkout_sha() == "unknown"


def test_environment_records_key_versions_and_ha_commit_without_installation_urls(
    monkeypatch,
):
    monkeypatch.setattr(
        evidence,
        "distribution",
        lambda _: SimpleNamespace(
            read_text=lambda _: json.dumps(
                {
                    "url": "https://PRIVATE-INSTALLATION-URL.example.invalid/secret",
                    "vcs_info": {"commit_id": WRONG},
                }
            )
        ),
    )
    monkeypatch.setattr(evidence, "_version", lambda _: "fixture-version")
    identity = evidence.environment_identity()
    assert identity["homeassistant_source_commit"] == WRONG
    assert set(identity["packages"]) == {
        "homeassistant",
        "home-assistant-frontend",
        "openai",
        "httpx",
        "aiohttp",
        "pytest-homeassistant-custom-component",
    }
    assert "PRIVATE-INSTALLATION-URL" not in json.dumps(identity)
    ordered = {key: identity[key] for key in reversed(identity)}
    assert evidence.environment_fingerprint(
        identity
    ) == evidence.environment_fingerprint(ordered)
    changed = deepcopy(identity)
    changed["packages"]["openai"] = "another-sdk"
    assert evidence.environment_fingerprint(
        identity
    ) != evidence.environment_fingerprint(changed)
