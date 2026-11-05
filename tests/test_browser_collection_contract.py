"""Collection drift fails before browser execution without weakening certification."""

import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
import yaml

from ci import check_browser_collection as collection


@pytest.mark.parametrize(
    "nodes,expected,reviewed,reason",
    [
        (["a", "a", "b"], ["a", "a", "b"], ["a", "b"], None),
        (["a"], ["a", "b"], ["a", "b"], "missing: b"),
        (["a", "a"], ["a"], ["a"], "unexpected: a"),
        (["a", "b"], ["a"], ["a", "b"], "unexpected: b"),
        (["a", "b"], ["a", "b"], ["a"], "unreviewed: b"),
    ],
)
def test_collection_compares_multisets_and_mandatory_review(
    nodes, expected, reviewed, reason
):
    errors = collection.inventory_errors(nodes, expected, reviewed)
    assert not errors if reason is None else any(reason in error for error in errors)


@pytest.mark.parametrize(
    "fault,engine",
    [
        (fault, None)
        for fault in [
            None,
            "command",
            "missing",
            "duplicate",
            "failed",
            "empty",
            "executed",
            "uncollected",
        ]
    ]
    + [(None, "firefox"), (None, "webkit")],
)
def test_collection_uses_isolated_nonexecuting_ledgers(
    tmp_path, monkeypatch, fault, engine
):
    calls = []

    def run(command, **kwargs):
        assert "--list" in command
        assert "--reporter=./ci/playwright_execution.mjs" in command
        assert kwargs["cwd"] == tmp_path
        assert kwargs["env"].get("CROSS_BROWSER_ENGINE") == engine
        if engine:
            assert "--config=playwright.cross-browser-nightly.config.mjs" in command
            assert kwargs["env"]["CROSS_BROWSER_NIGHTLY"] == "1"
            assert kwargs["env"]["REAL_HA_BROWSER_PROFILE"] == engine
        output = Path(kwargs["env"]["STRESS_ARTIFACT_DIR"])
        assert output not in calls
        calls.append(output)
        if fault == "command":
            return SimpleNamespace(returncode=1, stdout="collection failed", stderr="")
        output.mkdir()
        case = {
            "nodeid": f"case-{len(calls)}",
            "collected": fault != "uncollected",
            "executed": fault == "executed",
        }
        ledger = {
            "runner": "playwright",
            "status": "failed" if fault == "failed" else "passed",
            "cases": [] if fault == "empty" else [case],
        }
        if fault != "missing":
            (output / "execution-playwright-one.json").write_text(
                json.dumps(ledger), encoding="utf-8"
            )
        if fault == "duplicate":
            (output / "execution-playwright-two.json").write_text(
                json.dumps(ledger), encoding="utf-8"
            )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(collection.subprocess, "run", run)
    monkeypatch.setenv("STRESS_ARTIFACT_DIR", "caller-evidence")
    if fault:
        with pytest.raises(RuntimeError):
            collection.collect_static(tmp_path, engine=engine)
    else:
        assert collection.collect_static(tmp_path, engine=engine) == (
            ["case-1"] if engine else ["case-1", "case-2"]
        )
    assert not (tmp_path / "caller-evidence").exists()


def test_preflight_matches_actual_static_commands_and_runs_before_browser_install():
    root = Path(__file__).resolve().parents[1]
    steps = yaml.safe_load(
        (root / ".github/workflows/enhanced-stress.yml").read_text(encoding="utf-8")
    )["jobs"]["browser"]["steps"]
    main = next(
        step["run"]
        for step in steps
        if step.get("name") == "Run long-lived browser campaign"
    )
    recheck = next(
        step["run"]
        for step in steps
        if step.get("name") == "Recheck gated stale responses and listener ownership"
    )
    assert collection.COLLECTIONS[0][0] in main
    assert collection.COLLECTIONS[1][0] in recheck
    assert (
        tuple(re.findall(r"tests_browser/[\w-]+\.spec\.mjs", recheck))
        == collection.COLLECTIONS[1][1]
    )
    steps = yaml.safe_load(
        (root / ".github/workflows/frontend.yml").read_text(encoding="utf-8")
    )["jobs"]["browser-smoke"]["steps"]
    preflight = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Check complete browser collection inventory"
    )
    assert steps[preflight]["if"] == "matrix.shard == 1"
    install = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Install Playwright test runner"
    )
    browser = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Install Chromium and system dependencies"
    )
    assert install < preflight < browser
