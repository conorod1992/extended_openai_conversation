"""Deterministic Skill recovery classifications preserve exact owned versions."""

import json

import pytest

from custom_components.extended_openai_conversation_responses.skill_transactions import (
    commit_transaction,
    prepare_transaction,
    recover_transaction,
    recover_transactions,
)
from homeassistant.exceptions import HomeAssistantError


def _transaction(tmp_path, kind="publish"):
    installed, root = tmp_path / "installed", tmp_path / ".staging"
    target = installed / "owned"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("OLD")
    staged = root / "candidate" if kind == "publish" else None
    if staged is not None:
        staged.mkdir(parents=True)
        (staged / "SKILL.md").write_text("NEW")
    suffix = "backup" if kind == "publish" else "remove"
    backup = root / f"owned.{suffix}-{'a' * 32}"
    journal = prepare_transaction(root, target, backup, staged)
    return installed, root, target, staged, backup, journal


@pytest.mark.parametrize("kind", ["publish", "remove"])
def test_uncommitted_first_rename_restores_exact_previous_version(tmp_path, kind):
    installed, root, target, staged, backup, journal = _transaction(tmp_path, kind)
    target.rename(backup)
    recover_transactions(root, installed)
    assert (target / "SKILL.md").read_text() == "OLD"
    assert not backup.exists() and not journal.exists()
    assert staged is None or not staged.exists()


@pytest.mark.parametrize("committed", [False, True])
def test_published_candidate_obeys_durable_commit_decision(tmp_path, committed):
    installed, _root, target, staged, backup, journal = _transaction(tmp_path)
    target.rename(backup)
    staged.rename(target)
    if committed:
        commit_transaction(journal)
    recover_transaction(journal, installed)
    assert (target / "SKILL.md").read_text() == ("NEW" if committed else "OLD")
    assert not backup.exists() and not journal.exists()


def test_committed_removal_does_not_resurrect_old_version(tmp_path):
    installed, root, target, _staged, backup, journal = _transaction(tmp_path, "remove")
    target.rename(backup)
    commit_transaction(journal)
    recover_transactions(root, installed)
    assert not target.exists() and not backup.exists() and not journal.exists()


def test_unjournaled_backup_is_preserved_for_manual_recovery(tmp_path, caplog):
    root = tmp_path / ".staging"
    backup = root / f"owned.backup-{'a' * 32}"
    backup.mkdir(parents=True)
    (backup / "SKILL.md").write_text("UNRELATED-OLD")
    recover_transactions(root, tmp_path / "installed")
    assert (backup / "SKILL.md").read_text() == "UNRELATED-OLD"
    assert "recover manually" in caplog.text


@pytest.mark.parametrize(
    "mutation",
    ["path_escape", "backup_identity", "target_identity", "oversized", "invalid_phase"],
)
def test_ambiguous_recovery_fails_without_deleting_recoverable_data(tmp_path, mutation):
    installed, _root, target, staged, backup, journal = _transaction(tmp_path)
    target.rename(backup)
    document = json.loads(journal.read_text())
    if mutation == "path_escape":
        document["staged"] = "../unrelated"
    elif mutation == "backup_identity":
        document["previous_identity"] = [-1, -1]
    elif mutation == "target_identity":
        target.mkdir()
        (target / "SKILL.md").write_text("EXTERNAL")
    elif mutation == "invalid_phase":
        document["phase"] = "unknown"
    journal.write_text("x" * 17000 if mutation == "oversized" else json.dumps(document))
    with pytest.raises((HomeAssistantError, ValueError)):
        recover_transaction(journal, installed)
    assert (backup / "SKILL.md").read_text() == "OLD"
    assert (staged / "SKILL.md").read_text() == "NEW"
    assert journal.exists()
