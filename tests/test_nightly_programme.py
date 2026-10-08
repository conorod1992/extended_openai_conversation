"""Sensitivity witnesses for programme-level candidate/environment governance."""

from copy import deepcopy
import hashlib
import json
import sys

import pytest

from ci import enhanced_evidence
from ci.compatibility_evidence import check_ha_environment
from ci.enhanced_evidence import environment_fingerprint
from ci.nightly_programme import programme_errors

SHA = "b" * 40


def envelope():
    identity = {"packages": {"homeassistant": "2026.9.4", "pytest-homeassistant-custom-component": "reviewed"}}
    return {"eoai_sha": SHA, "status": "success", "environment": identity, "environment_fingerprint": environment_fingerprint(identity)}


def test_programme_rejects_missing_stale_and_wrong_environment_evidence():
    evidence = {"nightly": {"run": {"head_sha": SHA, "status": "completed", "conclusion": "success"}, "envelopes": [envelope()]}}
    assert programme_errors(SHA, "2026.9.4", ["nightly"], evidence) == []
    assert programme_errors(SHA, "2026.9.4", ["nightly", "missing"], evidence)
    for path, value in [("head_sha", "a" * 40), ("status", "in_progress"), ("conclusion", "failure")]:
        changed = deepcopy(evidence)
        changed["nightly"]["run"][path] = value
        assert programme_errors(SHA, "2026.9.4", ["nightly"], changed)
    changed = deepcopy(evidence)
    changed["nightly"]["envelopes"][0] = envelope() | {"eoai_sha": "a" * 40}
    assert programme_errors(SHA, "2026.9.4", ["nightly"], changed)
    assert programme_errors(SHA, "2026.10.0", ["nightly"], evidence)


@pytest.mark.parametrize("field,value", [("homeassistant", "2026.9.3"), ("pytest-homeassistant-custom-component", "unexpected")])
def test_version_guard_checks_execution_ledger_as_well_as_job_summary(field, value):
    summary = envelope()
    ledger = deepcopy(summary)
    ledger["runner"] = "pytest"
    ledger["environment"]["packages"][field] = value
    ledger["environment_fingerprint"] = environment_fingerprint(ledger["environment"])
    summary["execution_runs"] = [ledger]
    assert check_ha_environment(summary, version="2026.9.4", plugin_version="reviewed")


def test_prebuilt_wheel_source_requires_consistent_recorded_build_identity(tmp_path, monkeypatch):
    source = tmp_path / "ha-core-sha"
    identity = tmp_path / "environment.identity.json"
    source.write_text(SHA)
    monkeypatch.setattr(enhanced_evidence, "_version", lambda _: "2026.10.0.dev0")
    built = {"extra": [f"ha_core_sha={SHA}"], "installed_python_packages": ["homeassistant==2026.10.0.dev0"], "python_version": sys.version.split()[0]}
    digest = hashlib.sha256(json.dumps(built, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    identity.write_text(json.dumps(built | {"sha256": digest}))
    assert enhanced_evidence.prebuilt_ha_source(identity, source) == SHA
    source.write_text("a" * 40)
    assert enhanced_evidence.prebuilt_ha_source(identity, source) is None
    source.write_text(SHA)
    identity.write_text(json.dumps(built | {"sha256": "wrong"}))
    assert enhanced_evidence.prebuilt_ha_source(identity, source) is None


def test_enhanced_nightly_requires_real_mariadb_and_postgresql_recorder():
    """The full overnight lane must execute, not skip, both external Recorder backends."""
    from pathlib import Path

    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "enhanced-stress.yml"
    ).read_text(encoding="utf-8")

    assert "external-recorder:" in workflow
    assert "image: mariadb:11.4" in workflow
    assert "image: postgres:17" in workflow
    assert "EOAI_TEST_MARIADB_URL:" in workflow
    assert "EOAI_TEST_POSTGRES_URL:" in workflow
    assert 'EOAI_REQUIRE_EXTERNAL_RECORDER: "1"' in workflow
    assert (
        "test_recorder_time_retention_lifecycle.py::"
        "test_native_history_against_external_recorder_database"
        in workflow
    )
    assert (
        "test_recorder_time_retention_lifecycle.py::"
        "test_recorder_backend_switch_preserves_history_and_statistics_semantics"
        in workflow
    )
    assert "github.event_name == 'schedule'" in workflow
    assert "inputs.campaign == 'all'" in workflow


@pytest.mark.parametrize("arches", [["amd64"], ["amd64", "arm64"]])
def test_collector_reads_current_official_artifacts_and_architecture_policy(monkeypatch, arches):
    from ci import nightly_programme
    from tests.test_release_certification import FakeActions, SOURCE

    actions = FakeActions()
    workflow = "official-ha-container.yml"
    run_id = actions.runs[workflow][0]["id"]
    actions.artifacts[run_id] = [item for item in actions.artifacts[run_id] if any(f"-{arch}-" in item["name"] for arch in arches)]
    monkeypatch.setattr(nightly_programme, "WORKFLOWS", (workflow,))
    evidence = nightly_programme.collect(actions, SOURCE)
    assert set(evidence[workflow]["official"]) == set(arches)
    assert not nightly_programme.programme_errors(SOURCE, "2026.9.4", [workflow], evidence)
    assert bool(nightly_programme.programme_errors(SOURCE, "2026.9.4", [workflow], evidence, full_architecture=True)) == (len(arches) == 1)
    evidence[workflow]["official"]["amd64"]["machine"] = "aarch64"
    assert nightly_programme.programme_errors(SOURCE, "2026.9.4", [workflow], evidence)


@pytest.mark.parametrize("fault", ["missing", "expired", "empty", "skipped", "candidate"])
def test_official_collector_rejects_invalid_evidence(monkeypatch, fault):
    from ci import nightly_programme
    from tests.test_release_certification import FakeActions, SOURCE

    actions = FakeActions()
    workflow = "official-ha-container.yml"
    run_id = actions.runs[workflow][0]["id"]
    artifact = actions.artifacts[run_id][0]
    if fault == "missing":
        actions.artifacts[run_id] = []
    elif fault == "expired":
        artifact["expired"] = True
    elif fault == "empty":
        artifact["size_in_bytes"] = 0
    elif fault == "skipped":
        actions.jobs[run_id][0]["conclusion"] = "skipped"
    else:
        actions.contents[artifact["id"]]["candidate_sha"] = "a" * 40
    monkeypatch.setattr(nightly_programme, "WORKFLOWS", (workflow,))
    if fault == "candidate":
        evidence = nightly_programme.collect(actions, SOURCE)
        assert nightly_programme.programme_errors(SOURCE, "2026.9.4", [workflow], evidence)
    else:
        with pytest.raises(RuntimeError):
            nightly_programme.collect(actions, SOURCE)


def test_full_validation_cohort_ignores_newer_daily_evidence(monkeypatch):
    from ci import nightly_programme
    from tests.test_release_certification import FakeActions, SOURCE

    actions = FakeActions()
    workflow = "official-ha-container.yml"
    full = actions.runs[workflow][0]
    full["head_branch"] = "full-validation-123-1"
    daily = {**full, "id": 999, "head_branch": "develop", "event": "schedule"}
    actions.runs[workflow].insert(0, daily)
    actions.artifacts[999] = [item for item in actions.artifacts[full["id"]]
                              if "-amd64-" in item["name"]]
    actions.jobs[999] = actions.jobs[full["id"]]
    monkeypatch.setattr(nightly_programme, "WORKFLOWS", (workflow,))
    evidence = nightly_programme.collect(actions, SOURCE, validation_ref=full["head_branch"])
    assert evidence[workflow]["run"]["id"] == full["id"]
    assert not nightly_programme.programme_errors(
        SOURCE, "2026.9.4", [workflow], evidence, full_architecture=True
    )
    assert not nightly_programme.collect(actions, SOURCE, validation_ref="missing-cohort")
    daily_evidence = nightly_programme.collect(actions, SOURCE)
    assert set(daily_evidence[workflow]["official"]) == {"amd64"}
