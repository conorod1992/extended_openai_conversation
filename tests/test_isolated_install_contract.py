"""Isolation certification rejects contaminated or incomplete runtime evidence."""

from copy import deepcopy

import pytest

from ci.isolated_install_acceptance import validate_evidence
from ci.nightly_programme import programme_errors


def proof():
    return {
        "paths": [
            "conversation",
            "structured_ai_task",
            "rest",
            "scrape",
            "memory",
            "knowledge",
        ],
        "before": {"homeassistant": "2026.9.4"},
        "entry": "retained",
        "entities": 2,
        "controls": {"callbacks": 3},
    }


def test_complete_independent_runtime_evidence():
    validate_evidence(proof(), proof())


@pytest.mark.parametrize(
    "fault",
    [
        "pytest",
        "preinstalled_optional",
        "missing_scrape",
        "entry_replaced",
        "duplicate_entity",
        "duplicate_timer",
    ],
)
def test_isolated_install_rejects_false_claims(fault):
    first, second = proof(), deepcopy(proof())
    if fault == "pytest":
        first["before"]["pytest-homeassistant-custom-component"] = "installed"
    elif fault == "preinstalled_optional":
        first["before"]["lxml"] = "installed"
    elif fault == "missing_scrape":
        second["paths"].remove("scrape")
    elif fault == "entry_replaced":
        second["entry"] = "replacement"
    elif fault == "duplicate_entity":
        second["entities"] += 1
    else:
        second["controls"]["callbacks"] += 1
    with pytest.raises(AssertionError):
        validate_evidence(first, second)


@pytest.mark.parametrize(
    "fault", [None, "sha", "ha", "missing", "incomplete", "failed"]
)
def test_programme_binds_both_isolated_runtimes(fault):
    candidate = "a" * 40
    row = {
        "run": {"head_sha": candidate, "status": "completed", "conclusion": "success"},
        "isolated": [
            {
                "machine": arch,
                "candidate_sha": candidate,
                "passed": True,
                "homeassistant": "2026.9.4",
                "phases": [
                    "seed",
                    "recover",
                    "recover-recorder-first",
                    "recover-provider-first",
                    "auth",
                ],
            }
            for arch in ("aarch64", "x86_64")
        ],
    }
    if fault == "sha":
        row["isolated"][0]["candidate_sha"] = "b" * 40
    elif fault == "ha":
        row["isolated"][0]["homeassistant"] = "wrong"
    elif fault == "missing":
        row["isolated"].pop()
    elif fault == "incomplete":
        row["isolated"][0]["phases"].remove("recover-recorder-first")
    elif fault == "failed":
        row["isolated"][0]["passed"] = False
    workflow = "deployment-architecture.yml"
    errors = programme_errors(candidate, "2026.9.4", [workflow], {workflow: row})
    assert bool(errors) == bool(fault)
