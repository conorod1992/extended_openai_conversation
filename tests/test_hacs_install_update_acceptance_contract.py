"""Contracts for the genuine HACS install/update acceptance lane."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "hacs-install-update-acceptance.yml"
JOURNEY = ROOT / "tests_real_ha" / "test_hacs_install_update_acceptance.py"


def test_hacs_acceptance_workflow_has_focused_pr_gate() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger_block = text.split("concurrency:", 1)[0]

    assert "schedule:" in trigger_block
    assert "workflow_dispatch:" in trigger_block
    assert "workflow_call:" in trigger_block
    policy = json.loads((ROOT / "ci/pr_workflows.json").read_text(encoding="utf-8"))
    paths = policy["hacs-install-update-acceptance"]["paths"]
    assert "ci/specialist_evidence.py" in paths
    assert "push:" not in trigger_block
    assert "hacs/integration/releases/latest" in text
    assert "hacs.zip" in text
    assert 'HACS_ACCEPTANCE_RELEASE: "6.8.3"' in text
    assert "HACS_ACCEPTANCE_CANDIDATE_REF" in text


def test_hacs_journey_uses_hacs_for_both_release_and_candidate() -> None:
    text = JOURNEY.read_text(encoding="utf-8")

    assert "hass.config_entries.flow.async_init(" in text
    assert '"GitHubDeviceAPI"' in text
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
    assert "_assert_obsolete_files_inert" in text
    assert '"retained_orphans"' in text
    assert "HACS-installed candidate file differs from checkout" in text
    assert '"frontend/dist/manifest.json"' in text
    assert "upgrade_helpers._released_phase" in text
    assert "upgrade_helpers._candidate_migration_phase" in text
    assert "upgrade_helpers._candidate_restart_phase" in text


def test_hacs_journey_covers_interrupted_replacement_recovery() -> None:
    text = JOURNEY.read_text(encoding="utf-8")

    assert "_simulate_interrupted_candidate_overlay" in text
    assert "test_hacs_recovers_from_interrupted_candidate_replacement" in text
    assert "remaining_release_files" in text
    assert (
        'for phase in ("hacs-update", "candidate-migrate", "candidate-restart")' in text
    )


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "unloaded",
        "token",
        "disabled",
        "repository",
        "not-installed",
        "missing-repository",
    ],
)
def test_candidate_restart_verifies_native_marketplace_adoption(
    tmp_path, monkeypatch, damage
):
    from homeassistant.config_entries import ConfigEntryState
    from tests_real_ha.test_hacs_install_update_acceptance import _ensure_hacs

    monkeypatch.setenv("HACS_ACCEPTANCE_GITHUB_TOKEN", "test-token")
    (tmp_path / "hacs-acceptance-state.json").write_text(
        json.dumps({"hacs_repository_id": "123"}), encoding="utf-8"
    )
    repository = SimpleNamespace(data=SimpleNamespace(id="123", installed=True))
    marketplace = SimpleNamespace(
        system=SimpleNamespace(disabled_reason=None),
        repositories=SimpleNamespace(get_by_full_name=lambda name: repository),
    )
    entry = SimpleNamespace(
        state=ConfigEntryState.LOADED,
        data={"token": "test-token"},
        runtime_data=marketplace,
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=lambda domain: [entry] if domain == "marketplace" else []
        ),
        async_block_till_done=AsyncMock(),
        config=SimpleNamespace(path=lambda name: str(tmp_path / name)),
    )
    if damage == "unloaded":
        entry.state = ConfigEntryState.SETUP_ERROR
    elif damage == "token":
        entry.data["token"] = "different"
    elif damage == "disabled":
        marketplace.system.disabled_reason = "invalid-token"
    elif damage == "repository":
        repository.data.id = "456"
    elif damage == "not-installed":
        repository.data.installed = False
    elif damage == "missing-repository":
        marketplace.repositories.get_by_full_name = lambda name: None
    if damage:
        with pytest.raises(AssertionError):
            asyncio.run(_ensure_hacs(hass))
    else:
        assert asyncio.run(_ensure_hacs(hass)) is marketplace


def test_historical_hacs_remains_supported_before_native_marketplace(tmp_path):
    from homeassistant.config_entries import ConfigEntryState
    from tests_real_ha.test_hacs_install_update_acceptance import _ensure_hacs

    entry = SimpleNamespace(state=ConfigEntryState.LOADED)
    hacs = SimpleNamespace(system=SimpleNamespace(disabled=False))
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=lambda domain: [entry] if domain == "hacs" else []
        ),
        async_block_till_done=AsyncMock(),
        data={"hacs": hacs},
    )
    assert asyncio.run(_ensure_hacs(hass)) is hacs
