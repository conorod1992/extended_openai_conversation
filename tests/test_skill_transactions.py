"""Deterministic Skill recovery classifications preserve exact owned versions."""

import json

import pytest

from custom_components.extended_openai_conversation_responses import skill_transactions
from custom_components.extended_openai_conversation_responses.skill_transactions import (
    commit_transaction,
    prepare_download,
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


def test_download_recovery_leaves_active_and_unknown_staging(tmp_path):
    root, installed = tmp_path / ".staging", tmp_path / "installed"
    owner = prepare_download(root, "example")
    try:
        (owner.directory / "partial").write_text("download in progress")
        legacy = root / f"unknown.download-{'b' * 32}"
        legacy.mkdir()
        (legacy / "partial").write_text("unowned")
        recover_transactions(root, installed)
        assert (owner.directory / "partial").exists()
    finally:
        owner.handle.close()  # Process exited without running normal cleanup.
    recover_transactions(root, installed)
    assert not owner.directory.exists() and not owner.record.exists()
    assert (legacy / "partial").read_text() == "unowned"


@pytest.mark.parametrize("handed_off", [False, True])
def test_download_recovery_respects_prepared_replacement(tmp_path, handed_off):
    root, installed = tmp_path / ".staging", tmp_path / "installed"
    owner = prepare_download(root, "example")
    target = installed / "example"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("OLD")
    backup = root / f"example.backup-{'a' * 32}"
    try:
        (owner.directory / "SKILL.md").write_text("NEW")
        journal = prepare_transaction(root, target, backup, owner.directory)
        if handed_off:
            owner.hand_off()
            target.rename(backup)
            owner.directory.rename(target)
    finally:
        owner.handle.close()
    recover_transactions(root, installed)
    assert (target / "SKILL.md").read_text() == "OLD"
    assert not any(
        path.exists() for path in (journal, backup, owner.directory, owner.record)
    )


def test_download_recovery_retries_a_failed_cleanup(tmp_path, monkeypatch):
    root, installed = tmp_path / ".staging", tmp_path / "installed"
    owner = prepare_download(root, "example")
    owner.handle.close()
    with monkeypatch.context() as patch:

        def unavailable(_path):
            raise OSError("directory temporarily unavailable")

        patch.setattr(skill_transactions, "_remove", unavailable)
        recover_transactions(root, installed)
    assert owner.directory.exists() and owner.record.exists()
    recover_transactions(root, installed)
    recover_transactions(root, installed)
    assert not owner.directory.exists() and not owner.record.exists()


@pytest.mark.parametrize(
    "record_text",
    [
        '{"name":"example","identity":[-1,-1]}',
        " " * (16 * 1024 + 1),
        "[" * 1500 + "]" * 1500,
    ],
    ids=["incorrect-owner", "oversized", "deeply-nested"],
)
def test_download_recovery_preserves_unverified_payload(tmp_path, record_text):
    root, installed = tmp_path / ".staging", tmp_path / "installed"
    owner = prepare_download(root, "example")
    owner.handle.close()
    payload = owner.directory / "partial"
    payload.write_text("Preserve unverified data")
    owner.record.write_text(record_text)
    recover_transactions(root, installed)
    assert payload.read_text() == "Preserve unverified data"
    assert owner.record.exists()


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

@pytest.mark.parametrize("failure_stage", ["fdopen", "lock", "mkdir", "fsync"])
def test_download_setup_failure_cleans_its_artifacts(tmp_path, monkeypatch, failure_stage):
    import fcntl

    root = tmp_path / ".staging"
    module, name = (fcntl, "flock") if failure_stage == "lock" else (skill_transactions.os, failure_stage)

    def fail(*_args, **_kwargs):
        raise OSError("setup failed")

    with monkeypatch.context() as fault:
        if failure_stage == "mkdir":
            mkdir = type(root).mkdir

            def maybe_fail(path, *args, **kwargs):
                if path.parent == root:
                    fail()
                mkdir(path, *args, **kwargs)

            fault.setattr(type(root), "mkdir", maybe_fail)
        else:
            fault.setattr(module, name, fail)
        with pytest.raises(OSError, match="setup failed"):
            prepare_download(root, "example")
    assert list(root.iterdir()) == []
    owner = prepare_download(root, "example")
    owner.close()
    assert list(root.iterdir()) == []


def test_recovery_retires_only_unlocked_empty_download_record(tmp_path):
    import fcntl

    root = tmp_path / ".staging"
    root.mkdir()
    token = "a" * 32
    record = root / f"download-{token}.json"
    directory = root / f"example.download-{token}"
    directory.mkdir()
    payload = directory / "partial"
    payload.write_text("unverified")
    with record.open("wb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        recover_transactions(root, tmp_path / "installed")
        assert record.exists()
    recover_transactions(root, tmp_path / "installed")
    recover_transactions(root, tmp_path / "installed")
    assert not record.exists()
    assert payload.read_text() == "unverified"
