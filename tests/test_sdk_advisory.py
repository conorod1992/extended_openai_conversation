"""An unavailable SDK advisory must not masquerade as tested compatibility."""

import json
from pathlib import Path
import sys
from types import SimpleNamespace

from packaging.requirements import InvalidRequirement
import pytest
import yaml

from ci import sdk_advisory, workflow_evidence

REQUESTED = "openai>3.26.0,<4"


@pytest.mark.parametrize(
    "constraints",
    [
        "openai==3.26.0\n",
        "# Core constraints\nOpenAI == 3.26.0 # integration pin\n",
        "openai==3.10.0\n",
        "openai==4.0.0\n",
        'openai==3.26.0; python_version >= "3.12"\n',
    ],
)
def test_only_exact_active_pin_outside_requested_range_is_unavailable(constraints):
    assert sdk_advisory.unavailable_reason(constraints, REQUESTED)


@pytest.mark.parametrize(
    "constraints",
    [
        "openai==3.27.0\n",
        "openai==3.27.0rc1\n",
        'openai==3.26.0; python_version < "3.0"\n',
        "openai>=3.26.0,<4\n",
        "openai==3.26.*\n",
        "openai-extra==3.26.0\n",
        "httpx==0.28.1\n",
        "# openai==3.26.0\n",
    ],
)
def test_other_constraints_proceed_to_normal_install_and_wire_checks(constraints):
    assert sdk_advisory.unavailable_reason(constraints, REQUESTED) is None


def test_malformed_sdk_constraint_is_a_failure_instead_of_a_skip():
    with pytest.raises(InvalidRequirement):
        sdk_advisory.unavailable_reason("openai==not-a-version\n", REQUESTED)
    with pytest.raises(ValueError, match="must request openai"):
        sdk_advisory.unavailable_reason("", "other>3")


@pytest.mark.parametrize("pin,unavailable", [("3.26.0", True), ("3.27.0", False)])
def test_production_guard_reports_unavailable_combination_explicitly(
    tmp_path, monkeypatch, pin, unavailable
):
    package = tmp_path / "homeassistant"
    package.mkdir()
    (package / "package_constraints.txt").write_text(f"openai=={pin}\n")
    monkeypatch.setitem(
        sys.modules,
        "homeassistant",
        SimpleNamespace(__file__=str(package / "__init__.py")),
    )
    outputs, summary = tmp_path / "outputs", tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    sdk_advisory.main(REQUESTED)
    if unavailable:
        assert (
            f"unavailable_reason=Home Assistant pins openai=={pin};"
            in outputs.read_text()
        )
        assert "SDK advisory not run" in summary.read_text()
        assert "No wire-test pass is claimed" in summary.read_text()
    else:
        assert not outputs.exists()
        assert not summary.exists()


@pytest.mark.parametrize(
    "status,expected",
    [("success", "skipped"), ("failure", "failure"), ("cancelled", "cancelled")],
)
def test_advisory_artifact_records_skip_without_hiding_other_failures(
    tmp_path, monkeypatch, status, expected
):
    monkeypatch.setenv("EVIDENCE_LANE", "latest-3x-early-warning")
    monkeypatch.setenv("ENHANCED_JOB_STATUS", status)
    monkeypatch.setenv("SDK_UNAVAILABLE_REASON", "Home Assistant pins openai==3.26.0")
    monkeypatch.setattr(workflow_evidence, "envelope", lambda **data: data)
    destination = tmp_path / "workflow-evidence.json"
    workflow_evidence.main("sdk", destination)
    evidence = json.loads(destination.read_text())
    assert evidence["status"] == expected
    assert evidence["skip_reason"] == "Home Assistant pins openai==3.26.0"


@pytest.mark.parametrize("kind,lane", [("sdk", "3.26.0"), ("upgrade", "latest")])
def test_supported_lanes_cannot_be_reclassified_as_skipped(
    tmp_path, monkeypatch, kind, lane
):
    monkeypatch.setenv("EVIDENCE_LANE", lane)
    monkeypatch.setenv("ENHANCED_JOB_STATUS", "failure")
    monkeypatch.setenv("SDK_UNAVAILABLE_REASON", "not applicable")
    monkeypatch.setattr(workflow_evidence, "envelope", lambda **data: data)
    destination = tmp_path / "workflow-evidence.json"
    workflow_evidence.main(kind, destination)
    assert json.loads(destination.read_text())["status"] == "failure"


def test_workflow_keeps_supported_installs_constrained_and_wire_checks_required():
    root = Path(__file__).resolve().parents[1]
    job = yaml.safe_load(
        (root / ".github/workflows/openai-sdk-compatibility.yml").read_text()
    )["jobs"]["sdk-contract"]
    steps = {step["name"]: step for step in job["steps"]}
    guard = steps["Check advisory SDK eligibility"]
    assert guard["if"] == "matrix.sdk == 'latest-3x-early-warning'"
    assert job["env"]["ADVISORY_SDK_SPEC"] == REQUESTED
    assert guard["run"] == 'python ci/sdk_advisory.py "$ADVISORY_SDK_SPEC"'
    selection = steps["Select SDK version for early warning"]
    assert '--constraint "$CONSTRAINTS" "$SPEC"' in selection["run"]
    for step in (selection, steps["Run focused real SDK wire contract"]):
        assert step["if"] == "steps.advisory.outputs.unavailable_reason == ''"
        assert "continue-on-error" not in step
    evidence = steps["Record exact-source SDK evidence"]
    assert evidence["if"] == "always()"
    assert (
        evidence["env"]["SDK_UNAVAILABLE_REASON"]
        == "${{ steps.advisory.outputs.unavailable_reason }}"
    )
