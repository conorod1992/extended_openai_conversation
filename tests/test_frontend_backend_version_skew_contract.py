"""Contracts for the frontend/backend version-skew acceptance lane."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "frontend-backend-version-skew.yml"
BACKEND_TEST = ROOT / "tests_real_ha" / "test_frontend_backend_version_skew_acceptance.py"
BROWSER_TEST = ROOT / "tests_browser" / "frontend-backend-version-skew.spec.mjs"
HARNESS = ROOT / "tests_browser" / "real-ha-harness.mjs"
MANAGEMENT = (
    ROOT
    / "custom_components"
    / "extended_openai_conversation_responses"
    / "management_ui.py"
)


def test_skew_workflow_is_nightly_manual_and_uses_release_assets() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger_block = text.split("concurrency:", 1)[0]

    assert "schedule:" in trigger_block
    assert "workflow_dispatch:" in trigger_block
    assert "pull_request:" not in trigger_block
    assert "push:" not in trigger_block
    assert "releases/latest" in text
    assert 'git archive "$FROM_VERSION"' in text
    assert "released_frontend_source_sha" in text


def test_skew_journey_uses_two_frontend_generations_and_delayed_old_write() -> None:
    backend = BACKEND_TEST.read_text(encoding="utf-8")
    browser = BROWSER_TEST.read_text(encoding="utf-8")
    harness = HARNESS.read_text(encoding="utf-8")

    assert "old_save_waiting" in backend
    assert "release_old_save" in backend
    assert '"revision" not in bridge.old_save_message' in backend
    assert 'client: "old"' in browser
    assert 'client: "new"' in browser
    assert "authoritativeTitle" in browser
    assert "staleTitle" in browser
    assert "frontend_root" in harness
    assert "browserClient" in harness


def test_configuration_writes_require_revision_for_old_tab_safety() -> None:
    text = MANAGEMENT.read_text(encoding="utf-8")
    assert "Configuration revision is required; reload the latest saved settings before saving." in text
    assert '_require_agent_config_revision(subentry, message["revision"])' in text
