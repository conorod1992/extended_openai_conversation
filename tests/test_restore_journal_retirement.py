"""Journal absence cannot release quarantine before directory durability settles."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    restore_recovery,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from homeassistant.exceptions import HomeAssistantError
from tests.restore_participants import participants
from tests.test_restore_recovery import _identity, _states


@pytest.mark.parametrize("sync_fails", [False, True])
def test_directory_sync_closes_descriptor_even_on_failure(
    tmp_path, monkeypatch, sync_fails
):
    fake_os = SimpleNamespace(
        name="posix",
        O_RDONLY=0,
        O_DIRECTORY=1,
        open=Mock(return_value=81),
        fsync=Mock(side_effect=OSError("sync failed") if sync_fails else None),
        close=Mock(),
    )
    monkeypatch.setattr(restore_recovery, "os", fake_os)
    path = tmp_path / ".storage" / "journal"
    if sync_fails:
        with pytest.raises(OSError, match="sync failed"):
            restore_recovery._sync_journal_directory(str(path))
    else:
        restore_recovery._sync_journal_directory(str(path))
    fake_os.open.assert_called_once_with(path.parent, 1)
    fake_os.fsync.assert_called_once_with(81)
    fake_os.close.assert_called_once_with(81)


def test_new_storage_directory_absence_syncs_config_parent(tmp_path, monkeypatch):
    fake_os = SimpleNamespace(
        name="posix",
        O_RDONLY=0,
        O_DIRECTORY=1,
        open=Mock(side_effect=[FileNotFoundError(), 82]),
        fsync=Mock(),
        close=Mock(),
    )
    monkeypatch.setattr(restore_recovery, "os", fake_os)
    restore_recovery._sync_journal_directory(str(tmp_path / ".storage" / "journal"))
    assert fake_os.open.call_args_list[1].args == (tmp_path, 1)
    fake_os.fsync.assert_called_once_with(82)
    fake_os.close.assert_called_once_with(82)


def test_non_posix_does_not_claim_directory_fsync_support(tmp_path, monkeypatch):
    fake_os = SimpleNamespace(name="nt", open=Mock())
    monkeypatch.setattr(restore_recovery, "os", fake_os)
    restore_recovery._sync_journal_directory(str(tmp_path / "journal"))
    fake_os.open.assert_not_called()


@pytest.mark.parametrize("persistent_failure", [False, True])
async def test_deleted_journal_requires_durable_absence_before_releasing_quarantine(
    hass, monkeypatch, persistent_failure
):
    entry, subentry = _identity()
    target, rollback = _states()
    journal = restore_recovery._new_journal(
        entry.entry_id, subentry.subentry_id, target, rollback
    )
    journal["phase"] = "committed"
    store = restore_recovery._journal_store(hass, entry.entry_id, subentry.subentry_id)
    await store.async_save(journal)
    path = Path(store.path)
    apply = AsyncMock()
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=participants())
    )
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)
    monkeypatch.setattr(restore_recovery, "reset_restored_runtime", Mock())
    monkeypatch.setattr(restore_recovery, "_update_configuration", AsyncMock())
    native_sync = restore_recovery._sync_journal_directory
    failures = 0

    def fail_after_unlink(name):
        nonlocal failures
        if not path.exists() and (persistent_failure or failures == 0):
            failures += 1
            raise OSError("directory sync failed")
        native_sync(name)

    gate = get_agent_maintenance_gate(hass, entry.entry_id, subentry.subentry_id)
    with monkeypatch.context() as fault:
        fault.setattr(restore_recovery, "_sync_journal_directory", fail_after_unlink)
        with pytest.raises(backup.BackupError, match="could not be recovered"):
            await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
        assert not path.exists()
        assert gate.recovery_required is persistent_failure
        if persistent_failure:
            with pytest.raises(HomeAssistantError):
                gate.require_available()
    # A later successful directory sync settles absence. It must not replay an
    # already completed transaction just because the unlink acknowledgement failed.
    assert not await restore_recovery.async_recover_pending_restore(
        hass, entry, subentry
    )
    assert not gate.recovery_required
    assert apply.await_count == 1
