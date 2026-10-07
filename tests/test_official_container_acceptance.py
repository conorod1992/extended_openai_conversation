"""Unit contracts for the official Home Assistant Container acceptance harness."""

from subprocess import CompletedProcess
from urllib.error import HTTPError

import pytest

from ci import official_container_acceptance as container
from ci.official_container_acceptance import _validate


def test_normal_entrypoint_accepts_authenticated_api_readiness(monkeypatch, tmp_path):
    commands = []

    def docker(*args, **kwargs):
        commands.append(args)
        return CompletedProcess(args, 0, "true" if args[0] == "inspect" else "", "")

    def api_request(url, **kwargs):
        # The minimal config has no frontend root page. Its explicitly enabled
        # API must be serving, even though this probe has no access token.
        assert url == "http://127.0.0.1:18123/api/"
        raise HTTPError(url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(container, "_docker", docker)
    monkeypatch.setattr(container, "urlopen", api_request)
    container._normal_entrypoint("official-image", tmp_path, tmp_path)
    assert (tmp_path / "entrypoint.log").exists()
    assert commands[-2][0] == "stop"
    assert commands[-1][0] == "rm"


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
