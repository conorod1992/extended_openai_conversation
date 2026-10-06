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
    assert "github.event_name == 'schedule'" in workflow
    assert "inputs.campaign == 'all'" in workflow
