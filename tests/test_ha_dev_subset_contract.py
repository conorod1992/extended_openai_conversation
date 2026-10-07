"""Contract checks for the reviewed HA-dev upcoming acceptance subset."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ci/run_real_ha_upcoming_tests.sh"

REQUIRED_HA_DEV_BOUNDARIES = {
    "tests_real_ha/test_ha_runtime_mutation.py",
    "tests_real_ha/test_request_rules_script_semantics.py",
    "tests_real_ha/test_native_automation_acceptance.py",
    "tests_real_ha/test_native_recorder_acceptance.py",
    "tests_real_ha/test_user_ownership_privacy.py",
    "tests_real_ha/test_template_shutdown_acceptance.py",
}


def _selected_paths() -> set[str]:
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index("TEST_PATHS=(")
    end = text.index("\n)\n", start)
    block = text[start:end]
    return {
        line.strip()
        for line in block.splitlines()
        if line.strip().startswith("tests_real_ha/")
    }


def test_ha_dev_subset_keeps_reviewed_upstream_sensitive_boundaries():
    selected = _selected_paths()
    assert REQUIRED_HA_DEV_BOUNDARIES <= selected


def test_ha_dev_subset_remains_a_targeted_subset_not_full_stable_duplication():
    selected = _selected_paths()
    all_real_ha = {
        str(path.relative_to(ROOT))
        for path in (ROOT / "tests_real_ha").glob("test_*.py")
    }
    assert len(selected) < len(all_real_ha)
    assert len(selected) <= len(all_real_ha) // 2
