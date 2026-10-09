"""Contract tests for the expensive Home Assistant version-upgrade lane."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ha-version-upgrade-acceptance.yml"
JOURNEY = ROOT / "tests_real_ha" / "test_ha_version_upgrade_acceptance.py"
RELEASE_HELPERS = ROOT / "tests_real_ha" / "test_release_upgrade_acceptance.py"


def test_ha_version_upgrade_workflow_has_focused_pr_gate() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger_block = text.split("concurrency:", 1)[0]

    assert "schedule:" in trigger_block
    assert "workflow_dispatch:" in trigger_block
    assert "workflow_call:" in trigger_block
    policy = json.loads((ROOT / "ci/pr_workflows.json").read_text(encoding="utf-8"))
    paths = policy["ha-version-upgrade-acceptance"]["paths"]
    assert "ci/specialist_evidence.py" in paths
    assert "push:" not in trigger_block
    assert "tests_real_ha/test_ha_version_upgrade_acceptance.py" in text
    assert 'json.load(open("hacs.json"' in text
    assert '"oldest-supported"' in text
    assert '"current-stable"' in text
    # The historical virtualenv cannot inherit EOAI dependencies from the
    # surrounding stable image before the child imports the integration.
    assert '"${RUNNER_TEMP}/ha-oldest/bin/python" ci/install_ha_dependencies.py' in text


def test_ha_version_upgrade_journey_keeps_eoai_fixed_and_checks_durable_state() -> None:
    text = JOURNEY.read_text(encoding="utf-8")

    assert "shutil.copytree(source, destination)" in text
    assert "for runtime in runtimes[1:]" in text
    assert '"transition"' in text
    assert '"restart"' in text
    assert "candidate_digest == upgrade_helpers._component_digest(destination)" in text

    for durable_boundary in (
        "CONF_FUNCTION_GROUPS",
        "CONF_ARCHIVE_RETENTION_DAYS",
        "PERSISTED_HA_UPGRADE_DELAY",
        "archive_session_id",
    ):
        assert durable_boundary in text

    # Memory, knowledge and request rules are checked by the shared release
    # journey. Verify both delegation and its assertions rather than requiring
    # those field names to be duplicated in the HA upgrade wrapper.
    assert "await upgrade_helpers._released_phase(hass, config_dir)" in text
    assert "await upgrade_helpers._candidate_migration_phase(hass, config_dir)" in text
    assert "await upgrade_helpers._candidate_restart_phase(hass, config_dir)" in text
    helpers = RELEASE_HELPERS.read_text(encoding="utf-8")
    assert "await _assert_populated_release_state(hass, entry.entry_id, state)" in helpers
    for durable_boundary in ("memory_marker", "knowledge_marker", "request_rule_marker"):
        assert f'state["{durable_boundary}"]' in helpers
