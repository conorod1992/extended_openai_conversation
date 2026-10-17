"""Immutable HA dev resolution, workflow wiring and cohort identity regressions."""

from copy import deepcopy
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from ci import ha_dev_snapshot
from ci.validation_inventory import HA_DEV_WORKFLOWS, TEST_WORKFLOWS
from tests.test_full_validation_contract import DEV_SHA, _driver


def test_pinned_workflows_do_not_resolve_a_later_dev_tip(monkeypatch):
    lookup = Mock(
        side_effect=[
            SimpleNamespace(stdout=f"{DEV_SHA}\trefs/heads/dev\n"),
            SimpleNamespace(stdout=f"{'b' * 40}\trefs/heads/dev\n"),
        ]
    )
    monkeypatch.setattr(ha_dev_snapshot.subprocess, "run", lookup)
    cohort = ha_dev_snapshot.resolve_ha_dev_sha()
    assert [ha_dev_snapshot.resolve_ha_dev_sha(cohort) for _ in HA_DEV_WORKFLOWS] == [
        DEV_SHA
    ] * len(HA_DEV_WORKFLOWS)
    lookup.assert_called_once_with(
        [
            "git",
            "ls-remote",
            "https://github.com/home-assistant/core.git",
            "refs/heads/dev",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "pin", ["dev", "a" * 39, "a" * 41, "A" * 40, "a" * 40 + "\n", "abc; echo injected"]
)
def test_invalid_pin_fails_without_resolving_a_different_source(monkeypatch, pin):
    lookup = Mock()
    monkeypatch.setattr(ha_dev_snapshot.subprocess, "run", lookup)
    with pytest.raises(ValueError, match="full lowercase"):
        ha_dev_snapshot.resolve_ha_dev_sha(pin)
    lookup.assert_not_called()


@pytest.mark.parametrize(
    "output",
    [
        "",
        "bad refs/heads/dev",
        f"{DEV_SHA} refs/heads/stable",
        f"{DEV_SHA} refs/heads/dev\n{'b' * 40} refs/heads/dev",
    ],
)
def test_standalone_resolution_rejects_ambiguous_or_invalid_identity(
    monkeypatch, output
):
    monkeypatch.setattr(
        ha_dev_snapshot.subprocess,
        "run",
        Mock(return_value=SimpleNamespace(stdout=output)),
    )
    with pytest.raises(ValueError):
        ha_dev_snapshot.resolve_ha_dev_sha()


@pytest.mark.parametrize(
    "error",
    [subprocess.TimeoutExpired("git", 30), subprocess.CalledProcessError(1, "git")],
)
def test_standalone_resolution_failure_is_not_silently_ignored(monkeypatch, error):
    monkeypatch.setattr(ha_dev_snapshot.subprocess, "run", Mock(side_effect=error))
    with pytest.raises(type(error)):
        ha_dev_snapshot.resolve_ha_dev_sha()


def test_full_cohort_dispatches_one_pin_to_every_dev_consumer(monkeypatch, tmp_path):
    driver = _driver(monkeypatch, tmp_path)
    inventory = deepcopy(TEST_WORKFLOWS)
    dispatched = {}
    monkeypatch.setattr(driver, "api", Mock())

    def dispatch(workflow, inputs):
        dispatched[workflow] = deepcopy(inputs)
        return {
            "workflow": workflow,
            "run_id": len(dispatched),
            "url": "https://example.test/run",
        }

    monkeypatch.setattr(driver, "dispatch", dispatch)
    monkeypatch.setattr(
        driver,
        "wait_for_workflows",
        lambda rows: [(row, {"conclusion": "success"}) for row in rows],
    )
    monkeypatch.setattr(
        driver, "programme_inputs", lambda rows, pin: {"ha_dev_sha": pin}
    )
    monkeypatch.chdir(tmp_path)
    assert driver.main() == 0
    driver.resolve_ha_dev_sha.assert_called_once_with()
    assert dispatched.keys() >= HA_DEV_WORKFLOWS
    for name, original in TEST_WORKFLOWS:
        assert dispatched[name] == {
            **original,
            **({"ha_dev_sha": DEV_SHA} if name in HA_DEV_WORKFLOWS else {}),
        }
    assert dispatched[driver.CERTIFICATION_WORKFLOW]["ha_dev_sha"] == DEV_SHA
    assert inventory == TEST_WORKFLOWS


@pytest.mark.parametrize("workflow", sorted(HA_DEV_WORKFLOWS))
def test_all_full_validation_dev_consumers_honor_the_same_optional_input(workflow):
    root = Path(__file__).resolve().parents[1]
    text = (root / ".github/workflows" / workflow).read_text(encoding="utf-8")
    data = yaml.load(text, Loader=yaml.BaseLoader)
    for event in ("workflow_dispatch", "workflow_call"):
        if event in data["on"]:
            spec = data["on"][event]["inputs"]["ha_dev_sha"]
            assert spec["type"] == "string" and spec["required"] == "false"
    resolvers = [
        step
        for job in data["jobs"].values()
        for step in job.get("steps", [])
        if "ci/ha_dev_snapshot.py" in step.get("run", "")
    ]
    assert len(resolvers) == 1
    assert resolvers[0]["env"]["REQUESTED_HA_DEV_SHA"] == "${{ inputs.ha_dev_sha }}"
    assert '--sha "$REQUESTED_HA_DEV_SHA"' in resolvers[0]["run"]
    assert "git ls-remote" not in text
    job = next(
        job for job in data["jobs"].values() if resolvers[0] in job.get("steps", [])
    )
    before_resolver = job["steps"][: job["steps"].index(resolvers[0])]
    assert any(
        step.get("uses", "").startswith("actions/checkout@") for step in before_resolver
    )


def test_final_certification_receives_the_cohort_pin():
    root = Path(__file__).resolve().parents[1]
    data = yaml.load(
        (root / ".github/workflows/nightly-programme.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    step = next(
        step
        for step in data["jobs"]["certify"]["steps"]
        if "ci.nightly_programme" in step.get("run", "")
    )
    assert data["on"]["workflow_dispatch"]["inputs"]["ha_dev_sha"]["type"] == "string"
    assert step["env"]["HA_DEV_SHA"] == "${{ inputs.ha_dev_sha }}"
    assert '--ha-dev-sha "$HA_DEV_SHA"' in step["run"]


def test_full_validation_inventory_cannot_omit_a_new_dev_resolver():
    root = Path(__file__).resolve().parents[1]
    consumers = set()
    for name, _ in TEST_WORKFLOWS:
        text = (root / ".github/workflows" / name).read_text(encoding="utf-8")
        if (
            "ha_dev_snapshot.py" in text
            or "HA_CORE_BRANCH:" in text
            or re.search(r"refs/heads/dev\b", text)
        ):
            consumers.add(name)
    assert consumers == HA_DEV_WORKFLOWS
