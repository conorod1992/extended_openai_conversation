"""Lifecycle coverage for bounded full-backup transfers."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, replace
import hashlib
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    backup_transfer,
    transfer,
)


def test_preview_revision_excludes_generated_export_timestamp() -> None:
    """A second snapshot read stays valid until actual target state changes."""

    @dataclass
    class Target:
        config: dict[str, str]
        created_at: str
        integration_version: str

    original = Target({"prompt": "before"}, "first-read", "5.3.0")
    reread = replace(original, created_at="second-read")
    changed = replace(reread, config={"prompt": "after"})
    assert backup_transfer._snapshot_revision(
        original
    ) == backup_transfer._snapshot_revision(reread)
    assert backup_transfer._snapshot_revision(
        original
    ) != backup_transfer._snapshot_revision(changed)


async def test_export_chunk_streams_archive_and_enforces_agent_identity(
    hass, tmp_path, monkeypatch
) -> None:
    """A staged export is streamed in bounded chunks only to its owning agent."""
    monkeypatch.setattr(backup_transfer, "BACKUP_CHUNK_BYTES", 4)
    payload = b"abcdefgh"
    path = tmp_path / "export.zip"
    path.write_bytes(payload)
    session = backup_transfer.ExportSession(
        session_id="export-1",
        entry_id="entry-1",
        subentry_id="agent-1",
        path=str(path),
        filename="agent.zip",
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        expires_at=10**12,
    )
    backup_transfer._exports(hass)[session.session_id] = session

    try:
        first = await backup_transfer._export_chunk(
            hass,
            "entry-1",
            "agent-1",
            {"session_id": session.session_id, "index": 0},
        )
        second = await backup_transfer._export_chunk(
            hass,
            "entry-1",
            "agent-1",
            {"session_id": session.session_id, "index": 1},
        )

        assert base64.b64decode(first["data"]) == b"abcd"
        assert first["offset"] == 0
        assert first["bytes"] == 4
        assert first["final"] is False
        assert base64.b64decode(second["data"]) == b"efgh"
        assert second["offset"] == 4
        assert second["bytes"] == 4
        assert second["final"] is True

        with pytest.raises(backup.BackupError, match="does not belong to this agent"):
            await backup_transfer._export_chunk(
                hass,
                "entry-1",
                "agent-other",
                {"session_id": session.session_id, "index": 0},
            )
    finally:
        await backup_transfer._discard_export(hass, session.session_id)


async def test_import_can_be_inspected_then_restored_and_consumes_staging_file(
    hass, monkeypatch
) -> None:
    """Inspection is non-consuming, while restore consumes the completed upload."""
    monkeypatch.setattr(backup_transfer, "BACKUP_CHUNK_BYTES", 4)
    started = await backup_transfer._start_import(
        hass,
        "entry-1",
        "agent-1",
        {"filename": "backup.json", "size": 8},
    )
    session_id = started["session_id"]
    staged_path = backup_transfer._imports(hass)[session_id].path

    for index, chunk in enumerate((b"abcd", b"efgh")):
        await backup_transfer._import_chunk(
            hass,
            "entry-1",
            "agent-1",
            {
                "session_id": session_id,
                "index": index,
                "data": base64.b64encode(chunk).decode(),
            },
        )

    prepared = object()
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")
    load_calls = 0

    async def load_prepared(_hass, path, kind, target_subentry_id):
        nonlocal load_calls
        load_calls += 1
        assert path == staged_path
        assert kind == "legacy_json"
        assert target_subentry_id == "agent-1"
        return prepared

    async def current_snapshot(*_args):
        return object()

    async def materialize(
        _hass,
        actual_entry,
        actual_subentry,
        actual_prepared,
        *,
        sections,
        current_snapshot,
    ):
        assert actual_entry is entry
        assert actual_subentry is subentry
        assert actual_prepared is prepared
        assert sections == ["memory"]
        assert current_snapshot is not None
        return object(), {"changes": 1, "selected_sections": ["memory"]}

    async def restore(
        _hass, actual_entry, actual_subentry, actual_prepared, *, sections, precondition
    ):
        assert actual_entry is entry
        assert actual_subentry is subentry
        assert actual_prepared is prepared
        assert sections == ["memory"]
        await precondition()
        return {"restored": True}

    monkeypatch.setattr(backup_transfer, "_async_load_prepared_restore", load_prepared)
    monkeypatch.setattr(
        backup_transfer, "_resolve_agent", lambda *_args: (entry, subentry)
    )
    monkeypatch.setattr(transfer, "async_materialize_restore", materialize)
    monkeypatch.setattr(transfer, "_current_snapshot", current_snapshot)
    monkeypatch.setattr(
        backup_transfer, "_snapshot_revision", lambda _value: "revision"
    )
    monkeypatch.setattr(
        transfer,
        "inspection_for_frontend",
        lambda actual_prepared: {"prepared": actual_prepared is prepared},
    )
    monkeypatch.setattr(transfer, "async_restore_transfer", restore)

    inspection = await backup_transfer._inspect_import(
        hass,
        "entry-1",
        "agent-1",
        {"session_id": session_id, "sections": ["memory"]},
    )
    assert inspection["prepared"] is True
    assert inspection["preview"] == {"changes": 1, "selected_sections": ["memory"]}
    assert isinstance(inspection["preview_token"], str)
    assert session_id in backup_transfer._imports(hass)
    assert os.path.exists(staged_path)

    restored = await backup_transfer._restore_import(
        hass,
        entry,
        subentry,
        {
            "session_id": session_id,
            "sections": ["memory"],
            "preview_token": inspection["preview_token"],
        },
    )

    assert restored == {"restored": True}
    assert load_calls == 2
    assert session_id not in backup_transfer._imports(hass)
    assert not os.path.exists(staged_path)


async def test_restore_failure_still_consumes_and_deletes_completed_upload(
    hass, monkeypatch
) -> None:
    """A failed restore cannot leave a reusable completed upload behind."""
    started = await backup_transfer._start_import(
        hass,
        "entry-1",
        "agent-1",
        {"filename": "backup.json", "size": 4},
    )
    session_id = started["session_id"]
    staged_path = backup_transfer._imports(hass)[session_id].path
    await backup_transfer._import_chunk(
        hass,
        "entry-1",
        "agent-1",
        {
            "session_id": session_id,
            "index": 0,
            "data": base64.b64encode(b"abcd").decode(),
        },
    )

    async def load_prepared(*_args):
        return object()

    async def fail_restore(*_args, precondition, **_kwargs):
        await precondition()
        raise RuntimeError("restore failed")

    monkeypatch.setattr(backup_transfer, "_async_load_prepared_restore", load_prepared)
    monkeypatch.setattr(transfer, "async_restore_transfer", fail_restore)
    monkeypatch.setattr(transfer, "_current_snapshot", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        backup_transfer, "_snapshot_revision", lambda _value: "revision"
    )
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")
    session = backup_transfer._imports(hass)[session_id]
    session.preview_token = "preview"
    session.preview_revision = "revision"
    session.preview_sections = ("memory",)
    backup_transfer._latest_previews(hass)[("entry-1", "agent-1")] = (
        session_id,
        "preview",
    )

    with pytest.raises(RuntimeError, match="restore failed"):
        await backup_transfer._restore_import(
            hass,
            entry,
            subentry,
            {"session_id": session_id, "preview_token": "preview"},
        )

    assert session_id not in backup_transfer._imports(hass)
    assert not os.path.exists(staged_path)


async def test_expired_export_and_import_sessions_are_detached_and_deleted(
    hass, tmp_path
) -> None:
    """Lazy expiry cleanup removes both registry state and staged files."""
    export_path = tmp_path / "expired-export.zip"
    import_path = tmp_path / "expired-import.json"
    export_path.write_bytes(b"export")
    import_path.write_bytes(b"import")

    export_session = backup_transfer.ExportSession(
        session_id="expired-export",
        entry_id="entry-1",
        subentry_id="agent-1",
        path=str(export_path),
        filename="backup.zip",
        size=export_path.stat().st_size,
        sha256="0" * 64,
        expires_at=0,
    )
    import_session = backup_transfer.ImportSession(
        session_id="expired-import",
        entry_id="entry-1",
        subentry_id="agent-1",
        path=str(import_path),
        filename="backup.json",
        expected_size=import_path.stat().st_size,
        kind="legacy_json",
        expires_at=0,
    )
    backup_transfer._exports(hass)[export_session.session_id] = export_session
    backup_transfer._imports(hass)[import_session.session_id] = import_session

    await backup_transfer._async_cleanup_expired(hass)

    assert backup_transfer._exports(hass) == {}
    assert backup_transfer._imports(hass) == {}
    assert not export_path.exists()
    assert not import_path.exists()


def _session_path(tmp_path, name: str = "session.tmp") -> str:
    path = tmp_path / name
    path.write_bytes(b"")
    return str(path)


def _empty_import_session(tmp_path, **overrides) -> backup_transfer.ImportSession:
    values = {
        "session_id": "import-1",
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "path": _session_path(tmp_path),
        "filename": "backup.json",
        "expected_size": 1,
        "kind": "legacy_json",
        "expires_at": backup_transfer.time.monotonic() + 60,
    }
    values.update(overrides)
    return backup_transfer.ImportSession(**values)


def _empty_export_session(tmp_path, **overrides) -> backup_transfer.ExportSession:
    values = {
        "session_id": "export-1",
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "path": _session_path(tmp_path, "export.tmp"),
        "filename": "backup.zip",
        "size": 1,
        "sha256": "0" * 64,
        "expires_at": backup_transfer.time.monotonic() + 60,
    }
    values.update(overrides)
    return backup_transfer.ExportSession(**values)


async def test_discard_missing_sessions_is_false(hass) -> None:
    assert await backup_transfer._discard_export(hass, "missing") is False
    assert await backup_transfer._discard_import(hass, "missing") is False


async def test_start_import_wraps_temp_file_creation_error(hass, monkeypatch) -> None:
    async def fail_create(_hass):
        raise OSError("disk unavailable")

    monkeypatch.setattr(backup_transfer, "_async_create_upload_file", fail_create)

    with pytest.raises(backup.BackupError, match="temporary backup upload file"):
        await backup_transfer._start_import(
            hass,
            "entry-1",
            "agent-1",
            {"filename": "backup.json", "size": 1},
        )


async def test_import_chunk_storage_error_discards_session(
    hass, tmp_path, monkeypatch
) -> None:
    session = _empty_import_session(tmp_path)
    backup_transfer._imports(hass)[session.session_id] = session

    async def fail_append(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(backup_transfer, "_async_append_file", fail_append)

    with pytest.raises(backup.BackupError, match="could not be stored safely"):
        await backup_transfer._import_chunk(
            hass,
            "entry-1",
            "agent-1",
            {"session_id": session.session_id, "index": 0, "data": "YQ=="},
        )

    assert session.session_id not in backup_transfer._imports(hass)
    assert not os.path.exists(session.path)


@pytest.mark.parametrize(
    ("action", "data", "expected"),
    [
        ("export_cancel", {}, False),
        ("export_cancel", {"session_id": "missing"}, False),
        ("import_cancel", {}, False),
        ("import_cancel", {"session_id": "missing"}, False),
    ],
)
async def test_command_cancel_missing_sessions(
    hass, monkeypatch, action, data, expected
) -> None:
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")
    monkeypatch.setattr(
        backup_transfer, "_resolve_agent", lambda *_args: (entry, subentry)
    )

    result = await backup_transfer.async_backup_transfer_command(
        hass,
        {
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "action": action,
            "data": data,
        },
    )

    assert result == {"cancelled": expected}


async def test_command_rejects_non_object_data_and_unknown_action(
    hass, monkeypatch
) -> None:
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")
    monkeypatch.setattr(
        backup_transfer, "_resolve_agent", lambda *_args: (entry, subentry)
    )
    message = {
        "entry_id": entry.entry_id,
        "subentry_id": subentry.subentry_id,
        "action": "unknown",
    }

    with pytest.raises(backup.BackupError, match="data must be an object"):
        await backup_transfer.async_backup_transfer_command(
            hass, {**message, "data": "not-a-dict"}
        )

    with pytest.raises(backup.BackupError, match="Unsupported"):
        await backup_transfer.async_backup_transfer_command(hass, message)


def _hass() -> SimpleNamespace:
    return SimpleNamespace(data={})


def _completed_import_session(path: str, **overrides) -> backup_transfer.ImportSession:
    values = {
        "session_id": "import-remaining",
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "path": path,
        "filename": "backup.json",
        "expected_size": 4,
        "kind": "legacy_json",
        "expires_at": 10**12,
        "received": 4,
        "next_index": 1,
    }
    values.update(overrides)
    return backup_transfer.ImportSession(**values)


def _staged_export_session(path: str, **overrides) -> backup_transfer.ExportSession:
    values = {
        "session_id": "export-remaining",
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "path": path,
        "filename": "backup.zip",
        "size": 4,
        "sha256": "0" * 64,
        "expires_at": 10**12,
    }
    values.update(overrides)
    return backup_transfer.ExportSession(**values)


class _Exclusive:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *_args):
        return False


async def test_start_export_preserves_expected_backup_error(monkeypatch):
    """Known archive validation errors are not replaced by the generic wrapper."""
    hass = _hass()
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")
    gate = SimpleNamespace(exclusive=lambda: _Exclusive())
    expected = backup.BackupError("specific archive failure")

    monkeypatch.setattr(
        backup_transfer, "get_agent_maintenance_gate", lambda *_args: gate
    )
    monkeypatch.setattr(
        backup, "async_collect_backup_snapshot", AsyncMock(return_value={"ok": True})
    )
    monkeypatch.setattr(
        backup_transfer,
        "_async_build_archive_file",
        AsyncMock(side_effect=expected),
    )

    with pytest.raises(backup.BackupError) as raised:
        await backup_transfer._start_export(hass, entry, subentry)

    assert raised.value is expected


async def test_start_export_registry_failure_removes_built_archive(monkeypatch):
    """A finished archive cannot be orphaned if session registration fails."""
    hass = _hass()
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")
    gate = SimpleNamespace(exclusive=lambda: _Exclusive())
    remove = AsyncMock()
    monkeypatch.setattr(backup_transfer, "_async_cleanup_expired", AsyncMock())
    monkeypatch.setattr(
        backup_transfer, "_ensure_session_capacity", lambda *_args: None
    )
    monkeypatch.setattr(
        backup_transfer, "get_agent_maintenance_gate", lambda *_args: gate
    )
    monkeypatch.setattr(
        backup, "async_collect_backup_snapshot", AsyncMock(return_value={"ok": True})
    )
    monkeypatch.setattr(
        backup_transfer,
        "_async_build_archive_file",
        AsyncMock(
            return_value={
                "path": "/tmp/built-backup.zip",
                "filename": "backup.zip",
                "size": 4,
                "sha256": "0" * 64,
                "uncompressed_size": 8,
            }
        ),
    )
    monkeypatch.setattr(backup_transfer, "_async_remove_path", remove)

    calls = 0

    class FailingRegistration:
        async def __aenter__(self):
            raise RuntimeError("registry failed")

        async def __aexit__(self, *_args):
            return False

    def registry_lock(_hass):
        nonlocal calls
        calls += 1
        return asyncio.Lock() if calls == 1 else FailingRegistration()

    monkeypatch.setattr(backup_transfer, "_registry_lock", registry_lock)

    with pytest.raises(RuntimeError, match="registry failed"):
        await backup_transfer._start_export(hass, entry, subentry)

    remove.assert_awaited_once_with(hass, "/tmp/built-backup.zip")


async def test_export_chunk_wraps_staged_file_oserror(monkeypatch):
    """A disappearing staged archive is reported as a bounded backup error."""
    hass = _hass()
    session = _staged_export_session("/missing/archive.zip")
    backup_transfer._exports(hass)[session.session_id] = session
    hass.async_add_executor_job = AsyncMock(side_effect=OSError("gone"))
    monkeypatch.setattr(backup_transfer, "_async_cleanup_expired", AsyncMock())

    with pytest.raises(
        backup.BackupError, match="staged backup archive is unavailable"
    ):
        await backup_transfer._export_chunk(
            hass,
            "entry-1",
            "agent-1",
            {"session_id": session.session_id, "index": 0},
        )


async def test_start_import_registry_failure_removes_upload_file(monkeypatch):
    """A newly-created upload file is removed if its session cannot be registered."""
    hass = _hass()
    remove = AsyncMock()
    monkeypatch.setattr(backup_transfer, "_async_cleanup_expired", AsyncMock())
    monkeypatch.setattr(
        backup_transfer, "_ensure_session_capacity", lambda *_args: None
    )
    monkeypatch.setattr(
        backup_transfer,
        "_async_create_upload_file",
        AsyncMock(return_value="/tmp/new-upload"),
    )
    monkeypatch.setattr(backup_transfer, "_async_remove_path", remove)

    calls = 0

    class FailingRegistration:
        async def __aenter__(self):
            raise RuntimeError("registry failed")

        async def __aexit__(self, *_args):
            return False

    def registry_lock(_hass):
        nonlocal calls
        calls += 1
        return asyncio.Lock() if calls == 1 else FailingRegistration()

    monkeypatch.setattr(backup_transfer, "_registry_lock", registry_lock)

    with pytest.raises(RuntimeError, match="registry failed"):
        await backup_transfer._start_import(
            hass,
            "entry-1",
            "agent-1",
            {"filename": "backup.json", "size": 4},
        )

    remove.assert_awaited_once_with(hass, "/tmp/new-upload")


async def test_inspect_import_rejects_session_removed_while_waiting_for_lock(
    monkeypatch,
):
    """Inspection re-checks ownership of the session after acquiring its lock."""
    hass = _hass()
    session = _completed_import_session("/tmp/upload")
    backup_transfer._imports(hass)[session.session_id] = session
    monkeypatch.setattr(backup_transfer, "_async_cleanup_expired", AsyncMock())

    class RemovingLock:
        async def __aenter__(self):
            backup_transfer._imports(hass).pop(session.session_id, None)

        async def __aexit__(self, *_args):
            return False

    session.lock = RemovingLock()

    with pytest.raises(backup.BackupError, match="expired or was cancelled"):
        await backup_transfer._inspect_import(
            hass,
            "entry-1",
            "agent-1",
            {"session_id": session.session_id},
        )


async def test_import_cancel_checks_identity_and_non_string_is_noop(monkeypatch):
    """Import cancellation cannot delete another agent's session."""
    hass = _hass()
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")
    monkeypatch.setattr(
        backup_transfer, "_resolve_agent", lambda *_args: (entry, subentry)
    )
    monkeypatch.setattr(backup_transfer, "_async_cleanup_expired", AsyncMock())
    session = _completed_import_session("/tmp/upload", subentry_id="agent-other")
    backup_transfer._imports(hass)[session.session_id] = session

    with pytest.raises(backup.BackupError, match="does not belong to this agent"):
        await backup_transfer.async_backup_transfer_command(
            hass,
            {
                "entry_id": "entry-1",
                "subentry_id": "agent-1",
                "action": "import_cancel",
                "data": {"session_id": session.session_id},
            },
        )

    result = await backup_transfer.async_backup_transfer_command(
        hass,
        {
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "action": "import_cancel",
            "data": {"session_id": None},
        },
    )
    assert result == {"cancelled": False}
    assert session.session_id in backup_transfer._imports(hass)


def _import_session(path: str, **overrides) -> backup_transfer.ImportSession:
    values = {
        "session_id": "import-1",
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "path": path,
        "filename": "backup.json",
        "expected_size": 4,
        "kind": "legacy_json",
        "expires_at": 10**12,
        "received": 4,
        "next_index": 1,
    }
    values.update(overrides)
    return backup_transfer.ImportSession(**values)


def _export_session(path: str, **overrides) -> backup_transfer.ExportSession:
    values = {
        "session_id": "export-1",
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "path": path,
        "filename": "backup.zip",
        "size": 4,
        "sha256": "0" * 64,
        "expires_at": 10**12,
    }
    values.update(overrides)
    return backup_transfer.ExportSession(**values)


async def test_start_export_wraps_unexpected_archive_builder_error(hass, monkeypatch):
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")
    gate = SimpleNamespace(exclusive=MagicMock())

    class Exclusive:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *_args):
            return False

    gate.exclusive.return_value = Exclusive()
    monkeypatch.setattr(
        backup_transfer, "get_agent_maintenance_gate", lambda *_args: gate
    )
    monkeypatch.setattr(
        backup,
        "async_collect_backup_snapshot",
        AsyncMock(return_value={"snapshot": True}),
    )
    monkeypatch.setattr(
        backup_transfer,
        "_async_build_archive_file",
        AsyncMock(side_effect=RuntimeError("builder failed")),
    )

    with pytest.raises(backup.BackupError, match="could not be created safely"):
        await backup_transfer._start_export(hass, entry, subentry)


async def test_take_completed_import_rejects_session_removed_during_lock(
    hass, tmp_path, monkeypatch
):
    path = tmp_path / "upload"
    path.write_bytes(b"data")
    session = _import_session(str(path))
    backup_transfer._imports(hass)[session.session_id] = session

    class RemovingLock:
        async def __aenter__(self):
            backup_transfer._imports(hass).pop(session.session_id, None)

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(backup_transfer, "_registry_lock", lambda _hass: RemovingLock())
    with pytest.raises(backup.BackupError, match="expired or was cancelled"):
        await backup_transfer._take_completed_import(
            hass, session.session_id, "entry-1", "agent-1"
        )


async def test_take_completed_import_rechecks_completion_under_lock(
    hass, tmp_path, monkeypatch
):
    path = tmp_path / "upload"
    path.write_bytes(b"data")
    session = _import_session(str(path))
    backup_transfer._imports(hass)[session.session_id] = session

    class MutatingLock:
        async def __aenter__(self):
            session.received = 3

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(backup_transfer, "_registry_lock", lambda _hass: MutatingLock())
    with pytest.raises(backup.BackupError, match="upload is incomplete"):
        await backup_transfer._take_completed_import(
            hass, session.session_id, "entry-1", "agent-1"
        )


@pytest.mark.parametrize(
    ("action", "target"),
    [
        ("setup_export", "setup"),
        ("export_start", "export_start"),
        ("export_chunk", "export_chunk"),
        ("import_start", "import_start"),
        ("import_chunk", "import_chunk"),
        ("import_inspect", "import_inspect"),
        ("import_restore", "import_restore"),
    ],
)
async def test_command_dispatches_each_transfer_action(
    hass, monkeypatch, action, target
):
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")
    monkeypatch.setattr(
        backup_transfer, "_resolve_agent", lambda *_args: (entry, subentry)
    )
    result = {"target": target}

    if action == "setup_export":
        mocked = AsyncMock(return_value=result)
        monkeypatch.setattr(transfer, "async_create_setup_export", mocked)
    else:
        name = {
            "export_start": "_start_export",
            "export_chunk": "_export_chunk",
            "import_start": "_start_import",
            "import_chunk": "_import_chunk",
            "import_inspect": "_inspect_import",
            "import_restore": "_restore_import",
        }[action]
        mocked = AsyncMock(return_value=result)
        monkeypatch.setattr(backup_transfer, name, mocked)

    actual = await backup_transfer.async_backup_transfer_command(
        hass,
        {
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "action": action,
            "data": {"marker": True},
        },
    )
    assert actual == result
    mocked.assert_awaited_once()


async def test_command_cancel_enforces_owner_before_deleting(
    hass, tmp_path, monkeypatch
):
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")
    monkeypatch.setattr(
        backup_transfer, "_resolve_agent", lambda *_args: (entry, subentry)
    )
    path = tmp_path / "export"
    path.write_bytes(b"data")
    session = _export_session(str(path), subentry_id="agent-other")
    backup_transfer._exports(hass)[session.session_id] = session

    with pytest.raises(backup.BackupError, match="does not belong to this agent"):
        await backup_transfer.async_backup_transfer_command(
            hass,
            {
                "entry_id": "entry-1",
                "subentry_id": "agent-1",
                "action": "export_cancel",
                "data": {"session_id": session.session_id},
            },
        )
    assert session.session_id in backup_transfer._exports(hass)


async def test_websocket_translates_expected_error_and_returns_success(
    hass, monkeypatch
):
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=True),
        send_error=MagicMock(),
        send_result=MagicMock(),
    )
    message = {
        "id": 7,
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "action": "noop",
    }
    scheduled: list[asyncio.Task] = []

    def create_background_task(coro, *_args, **_kwargs):
        task = asyncio.create_task(coro)
        scheduled.append(task)
        return task

    hass.async_create_background_task.side_effect = create_background_task

    monkeypatch.setattr(
        backup_transfer,
        "async_backup_transfer_command",
        AsyncMock(side_effect=backup.BackupError("bad request")),
    )
    backup_transfer.websocket_backup_transfer(hass, connection, message)
    await scheduled.pop()
    connection.send_error.assert_called_once_with(7, "invalid_request", "bad request")
    connection.send_result.assert_not_called()

    connection.send_error.reset_mock()
    monkeypatch.setattr(
        backup_transfer,
        "async_backup_transfer_command",
        AsyncMock(return_value={"ok": True}),
    )
    backup_transfer.websocket_backup_transfer(hass, connection, message)
    await scheduled.pop()
    connection.send_result.assert_called_once_with(7, {"ok": True})
    connection.send_error.assert_not_called()


async def test_cleanup_all_detaches_and_deletes_export_and_import(hass, tmp_path):
    export_path = tmp_path / "export"
    import_path = tmp_path / "import"
    export_path.write_bytes(b"data")
    import_path.write_bytes(b"data")
    export = _export_session(str(export_path))
    imported = _import_session(str(import_path))
    backup_transfer._exports(hass)[export.session_id] = export
    backup_transfer._imports(hass)[imported.session_id] = imported

    await backup_transfer._async_cleanup_all(hass)

    assert backup_transfer._exports(hass) == {}
    assert backup_transfer._imports(hass) == {}
    assert not export_path.exists()
    assert not import_path.exists()
