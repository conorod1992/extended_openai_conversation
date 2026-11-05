"""Contracts for the genuine HACS install/update acceptance lane."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.config_entries import ConfigEntryState
from tests_real_ha import test_hacs_install_update_acceptance as journey

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


@pytest.fixture
def owned_repository(tmp_path, monkeypatch):
    state = {
        "hacs_entry_id": "retained-entry",
        "hacs_repository_id": "123",
        "repository_id_after_update": "123",
        "installed_commit_after_update": "candidate-sha",
        "installed_version_after_update": "candidate-version",
    }
    state_path = tmp_path / journey._HACS_STATE_FILE
    state_path.write_text(json.dumps(state), encoding="utf-8")
    native_store = tmp_path / ".storage/marketplace.repositories"
    native_store.parent.mkdir()
    native_store.write_text("{}", encoding="utf-8")
    repository = SimpleNamespace(
        data=SimpleNamespace(
            full_name=journey.REPOSITORY,
            id="123",
            installed=True,
            installed_commit="candidate-sha",
            installed_version="candidate-version",
        )
    )
    manager = SimpleNamespace(
        system=SimpleNamespace(disabled=False),
        repositories=SimpleNamespace(get_by_full_name=Mock(return_value=repository)),
    )
    entry = SimpleNamespace(
        entry_id="retained-entry",
        state=ConfigEntryState.LOADED,
        reason=None,
        data={"token": "test-token"},
        runtime_data=manager,
    )
    entries = {"marketplace": [entry], "hacs": []}
    hass = SimpleNamespace(
        async_block_till_done=AsyncMock(),
        config_entries=SimpleNamespace(async_entries=lambda domain: entries[domain]),
        data={"hacs": manager},
    )
    monkeypatch.setenv(journey._TOKEN_ENV, "test-token")
    monkeypatch.setattr(journey, "_repository_owner_domain", lambda: "marketplace")
    return SimpleNamespace(
        root=tmp_path,
        path=state_path,
        store=native_store,
        state=state,
        repository=repository,
        manager=manager,
        entry=entry,
        entries=entries,
        hass=hass,
    )


@pytest.mark.parametrize("domain", ["hacs", "marketplace"])
async def test_both_repository_owners_preserve_identity_across_cold_phases(
    owned_repository, monkeypatch, domain
):
    probe = owned_repository
    monkeypatch.setattr(journey, "_repository_owner_domain", lambda: domain)
    probe.entries["marketplace"] = [probe.entry] if domain == "marketplace" else []
    probe.entries["hacs"] = [probe.entry] if domain == "hacs" else []
    for phase in ("candidate-migrate", "candidate-restart"):
        await journey._assert_repository_owner(probe.hass, probe.root, phase)
    saved = json.loads(probe.path.read_text(encoding="utf-8"))
    assert set(saved["owner_checkpoints"]) == {"candidate-migrate", "candidate-restart"}
    for checkpoint in saved["owner_checkpoints"].values():
        assert checkpoint == {
            "domain": domain,
            "entry_id": "retained-entry",
            "repository_id": "123",
            "installed_commit": "candidate-sha",
            "installed_version": "candidate-version",
        }
    assert saved["hacs_entry_id"] == probe.state["hacs_entry_id"]


@pytest.mark.parametrize(
    "damage",
    [
        "missing-owner",
        "duplicate-owner",
        "new-entry",
        "not-loaded",
        "disabled",
        "missing-repository",
        "foreign-repository",
        "wrong-id",
        "not-installed",
        "wrong-commit",
        "wrong-version",
        "changed-token",
        "legacy-entry",
        "legacy-files",
        "missing-native-store",
    ],
)
async def test_successor_loss_cannot_be_hidden_by_recreating_hacs(
    owned_repository, damage
):
    probe = owned_repository
    if damage == "missing-owner":
        probe.entries["marketplace"] = []
    elif damage == "duplicate-owner":
        probe.entries["marketplace"] *= 2
    elif damage == "new-entry":
        probe.entry.entry_id = "fresh-entry"
    elif damage == "not-loaded":
        probe.entry.state = ConfigEntryState.SETUP_ERROR
    elif damage == "disabled":
        probe.manager.system.disabled = True
    elif damage == "missing-repository":
        probe.manager.repositories.get_by_full_name.return_value = None
    elif damage == "foreign-repository":
        probe.repository.data.full_name = "someone/else"
    elif damage == "wrong-id":
        probe.repository.data.id = "foreign-id"
    elif damage == "not-installed":
        probe.repository.data.installed = False
    elif damage == "wrong-commit":
        probe.repository.data.installed_commit = "old-commit"
    elif damage == "wrong-version":
        probe.repository.data.installed_version = "old-version"
    elif damage == "changed-token":
        probe.entry.data["token"] = "other-token"
    elif damage == "legacy-entry":
        probe.entries["hacs"] = [probe.entry]
    elif damage == "legacy-files":
        (probe.root / "custom_components/hacs").mkdir(parents=True)
    elif damage == "missing-native-store":
        probe.store.unlink()
    before = probe.path.read_bytes()
    with pytest.raises(AssertionError):
        await journey._assert_repository_owner(
            probe.hass, probe.root, "candidate-migrate"
        )
    assert probe.path.read_bytes() == before


@pytest.mark.parametrize("available,domain", [(True, "marketplace"), (False, "hacs")])
def test_owner_selection_follows_core_capability_not_missing_data(
    monkeypatch, available, domain
):
    monkeypatch.setattr(
        journey.importlib.util,
        "find_spec",
        lambda name: object() if available else None,
    )
    assert journey._repository_owner_domain() == domain


@pytest.mark.parametrize("phase", ["candidate-migrate", "candidate-restart"])
async def test_every_candidate_cold_boot_checks_retained_owner(
    monkeypatch, tmp_path, phase
):
    owner = AsyncMock()
    monkeypatch.setattr(journey, "_assert_repository_owner", owner)
    monkeypatch.setattr(journey, "_assert_obsolete_modules_not_loaded", lambda: None)
    monkeypatch.setattr(journey, "_assert_obsolete_files_inert", lambda path: None)
    monkeypatch.setattr(journey, "_candidate_tree_status", lambda path: {})
    monkeypatch.setattr(
        journey.upgrade_helpers, "_candidate_migration_phase", AsyncMock()
    )
    monkeypatch.setattr(
        journey.upgrade_helpers, "_candidate_restart_phase", AsyncMock()
    )
    hass = object()
    action = (
        journey._candidate_migrate
        if phase == "candidate-migrate"
        else journey._candidate_restart
    )
    await action(hass, tmp_path)
    owner.assert_awaited_once_with(hass, tmp_path, phase)


@pytest.mark.parametrize("include_first", [False, True])
def test_export_rejects_incomplete_owner_evidence(
    owned_repository, monkeypatch, include_first
):
    probe = owned_repository
    probe.state["owner_checkpoints"] = (
        {"candidate-migrate": {}} if include_first else {}
    )
    probe.path.write_text(json.dumps(probe.state), encoding="utf-8")
    output = probe.root / "evidence.json"
    monkeypatch.setenv(journey._EVIDENCE_ENV, str(output))
    with pytest.raises(AssertionError, match="Both retained repository-owner"):
        journey._write_evidence(probe.root)
    assert not output.exists()
