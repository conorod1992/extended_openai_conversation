"""Guard enhanced-nightly test inventory against silent coverage gaps."""

from __future__ import annotations

import json
from pathlib import Path
import re

import yaml

from ci.enhanced_evidence import evidence_filename

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "enhanced-stress.yml"
EXCLUSIONS = ROOT / "tests_stress" / "nightly_real_ha_exclusions.json"


def _workflow_test_paths(prefix: str) -> set[str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    paths = set(re.findall(rf"{re.escape(prefix)}/[A-Za-z0-9_./-]+\.py", text))
    # The shared HA lifecycle runner is selected by the workflow as a script,
    # so account for the test paths it invokes when checking the inventory.
    if prefix == "tests_real_ha" and "ci/run_ha_lifecycle_contract.sh" in text:
        lifecycle_runner = ROOT / "ci" / "run_ha_lifecycle_contract.sh"
        paths.update(
            re.findall(
                rf"{re.escape(prefix)}/[A-Za-z0-9_./-]+\.py",
                lifecycle_runner.read_text(encoding="utf-8"),
            )
        )
    return paths


def _test_files(directory: str) -> set[str]:
    return {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / directory).glob("test_*.py")
    }


def test_every_stress_test_is_assigned_to_enhanced_nightly() -> None:
    """Every stress test must be explicitly exercised by the enhanced workflow."""
    assert _test_files("tests_stress") == _workflow_test_paths("tests_stress")


def test_every_real_ha_test_is_selected_or_explicitly_excluded() -> None:
    """New real-HA tests may not silently fall outside enhanced nightly coverage."""
    actual = _test_files("tests_real_ha")
    selected = _workflow_test_paths("tests_real_ha")
    payload = json.loads(EXCLUSIONS.read_text(encoding="utf-8"))
    excluded = set(payload["excluded"])
    alternatives = payload["alternative_evidence"]
    assert set(alternatives) == excluded
    for path, evidence in alternatives.items():
        assert evidence["reason"].strip(), path
        jobs = yaml.safe_load((ROOT / ".github/workflows" / evidence["workflow"]).read_text(encoding="utf-8"))["jobs"]
        job = jobs[evidence["job"]]
        commands = "\n".join(step.get("run", "") for step in job["steps"])
        if "matrix.test-paths" in commands:
            commands += json.dumps(job["strategy"]["matrix"])
        assert evidence["selector"] in commands, path
        if evidence["selector"] == "tests_real_ha/":
            # Directory evidence must actually select this file. Explicit specialist
            # ignores require a dedicated alternative rather than a vague CI claim.
            assert f"--ignore={path}" not in commands, path
        else:
            assert evidence["selector"] == path
    partial = payload.get("partial_selected", {})
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for path, classification in partial.items():
        assert path in selected and path not in excluded
        assert classification["remaining_evidence"].strip()
        nodes = set(re.findall(rf"{re.escape(path)}::([A-Za-z0-9_]+)", workflow))
        assert nodes == set(classification["nodes"]), f"{path}: partial selection drifted"
        source = (ROOT / path).read_text(encoding="utf-8")
        for node in nodes:
            assert re.search(rf"(?:async )?def {re.escape(node)}\(", source), f"{path}::{node} missing"
        # A bare file selection would make the partial classification obsolete.
        assert not re.search(rf"{re.escape(path)}(?![A-Za-z0-9_.:/-])", workflow)
    node_files = {path for path in selected if re.search(rf"{re.escape(path)}::", workflow)}
    assert node_files == set(partial), "node-only selections must explicitly classify the remainder"

    assert selected.isdisjoint(excluded)
    assert selected | excluded == actual


def test_evidence_filenames_bound_parametrized_node_ids() -> None:
    base = "tests_stress/test_provider_event_sequences.py::test_duplicate_events"
    one = evidence_filename(f"{base}[chat_completions-{'x' * 4000}]")
    two = evidence_filename(f"{base}[responses-{'x' * 4000}]")
    assert one != two
    assert one.startswith("tests_stress_test_provider_event_sequences.py_test_duplicate_events-")
    assert len(one.encode("utf-8")) < 200
    assert all(character not in one for character in ':\\/[]')


def test_native_browser_version_matrix_stays_compact_and_names_its_points() -> None:
    import yaml

    workflow = ROOT / ".github/workflows/ha-browser-compatibility.yml"
    text = workflow.read_text(encoding="utf-8")
    jobs = yaml.safe_load(text)["jobs"]
    smoke = jobs["native-browser"]
    assert set(smoke["strategy"]["matrix"]["ha-version"]) == {"oldest", "stable", "dev"}
    commands = "\n".join(step.get("run", "") for step in smoke["steps"])
    paths = set(re.findall(r"tests_real_ha/[A-Za-z0-9_./-]+\.py", commands))
    assert paths == {"tests_real_ha/test_browser_compatibility_acceptance.py"}
    assert "git+https://github.com/home-assistant/core.git@$HA_DEV_SHA" in text
    assert "ha-native-browser-${{ matrix.ha-version }}" in text
    assert 'RUN_REAL_HA_BROWSER_COMPATIBILITY: "1"' in text
