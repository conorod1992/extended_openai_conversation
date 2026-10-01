"""A successful run agreeing on the wrong commit must still fail certification."""

from copy import deepcopy
import json
import subprocess
from types import SimpleNamespace

import pytest

from ci import enhanced_certification as gate, enhanced_evidence as evidence
from ci.candidate_evidence import check_candidate

SOURCE = "b" * 40
WRONG = "a" * 40


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
    "shas,expected", [([SOURCE, SOURCE], 0), ([WRONG, WRONG], 1), ([SOURCE, WRONG], 1)]
)
def test_real_final_gate_requires_intended_candidate_even_when_jobs_agree(
    tmp_path, monkeypatch, shas, expected
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
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
