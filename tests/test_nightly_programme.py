"""Sensitivity witnesses for programme-level candidate/environment governance."""

from copy import deepcopy

import pytest

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
