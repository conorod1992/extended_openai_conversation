"""Contracts for the genuine HACS install/update acceptance lane."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "hacs-install-update-acceptance.yml"
JOURNEY = ROOT / "tests_real_ha" / "test_hacs_install_update_acceptance.py"


def test_hacs_acceptance_workflow_is_nightly_manual_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger_block = text.split("concurrency:", 1)[0]

    assert "schedule:" in trigger_block
    assert "workflow_dispatch:" in trigger_block
    assert "pull_request:" not in trigger_block
    assert "push:" not in trigger_block
    assert "hacs/integration/releases/latest" in text
    assert "hacs.zip" in text
    assert 'HACS_ACCEPTANCE_RELEASE: "6.8.3"' in text
    assert "HACS_ACCEPTANCE_CANDIDATE_REF" in text


def test_hacs_journey_uses_hacs_for_both_release_and_candidate() -> None:
    text = JOURNEY.read_text(encoding="utf-8")

    assert "await hacs.async_register_repository(" in text
    assert "await repository.async_download_repository(ref=release)" in text
    assert "await repository.async_download_repository(ref=candidate_ref)" in text
    assert "shutil.copytree(source, destination)" in text
    # The only direct copy is HACS itself into a clean HA config. EOAI is installed
    # and updated exclusively by HACS.
    assert text.count("shutil.copytree(source, destination)") == 1


def test_hacs_journey_proves_obsolete_files_and_exact_candidate_tree() -> None:
    text = JOURNEY.read_text(encoding="utf-8")

    assert '"guest_performance.py"' in text
    assert '"frontend/management-bootstrap.js"' in text
    assert "HACS left obsolete release file active after update" in text
    assert "_tree_digest(installed) == _tree_digest(candidate)" in text
    assert '"frontend/dist/manifest.json"' in text
    assert "upgrade_helpers._released_phase" in text
    assert "upgrade_helpers._candidate_migration_phase" in text
    assert "upgrade_helpers._candidate_restart_phase" in text
