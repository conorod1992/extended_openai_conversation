"""Unit contracts for the official Home Assistant Container acceptance harness."""

import pytest

from ci.official_container_acceptance import _validate


def _proof(version="2026.10.0"):
    return {
        "after": {"homeassistant": version},
        "before": {"homeassistant": version},
        "paths": [
            "conversation",
            "structured_ai_task",
            "rest",
            "scrape",
            "memory",
            "knowledge",
        ],
        "entities": 2,
        "controls": {"callbacks": 3},
        "entry": "entry-1",
    }


def test_official_container_validation_requires_retained_runtime_identity():
    first = _proof()
    recovered = _proof()
    _validate(first, recovered, "2026.10.0")
    recovered["entry"] = "replacement"
    with pytest.raises(AssertionError):
        _validate(first, recovered, "2026.10.0")


@pytest.mark.parametrize(
    "change",
    ["version", "paths", "entities", "callbacks", "pytest"],
)
def test_official_container_validation_rejects_incomplete_runtime_evidence(change):
    first = _proof()
    recovered = _proof()
    if change == "version":
        recovered["after"]["homeassistant"] = "2026.9.4"
    elif change == "paths":
        recovered["paths"].remove("knowledge")
    elif change == "entities":
        recovered["entities"] = 1
    elif change == "callbacks":
        recovered["controls"]["callbacks"] = 4
    else:
        recovered["before"]["pytest"] = "9.0.0"
    with pytest.raises(AssertionError):
        _validate(first, recovered, "2026.10.0")
