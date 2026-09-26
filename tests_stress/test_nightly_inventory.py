"""Guard enhanced-nightly test inventory against silent coverage gaps."""

from __future__ import annotations

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "enhanced-stress.yml"
EXCLUSIONS = ROOT / "tests_stress" / "nightly_real_ha_exclusions.json"


def _workflow_test_paths(prefix: str) -> set[str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    return set(re.findall(rf"{re.escape(prefix)}/[A-Za-z0-9_./-]+\.py", text))


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

    assert selected.isdisjoint(excluded)
    assert selected | excluded == actual
