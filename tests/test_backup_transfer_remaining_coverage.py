"""Focused coverage for the remaining backup-transfer resilience branches."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import zipfile

import pytest

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses import backup_transfer
from custom_components.extended_openai_conversation_responses import transfer
from custom_components.extended_openai_conversation_responses.const import DOMAIN


def _hass() -> SimpleNamespace:
    return SimpleNamespace(data={})


def _import_session(path: str, **overrides) -> backup_transfer.ImportSession:
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


def _export_session(path: str, **overrides) -> backup_transfer.ExportSession:
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


async def test_delete_sessions_preserves_first_of_multiple_cancellations(monkeypatch):
    """Later cancellation must not replace the cancellation already being propagated."""
    first_error = asyncio.CancelledError("first")
    second_error = asyncio.CancelledError("second")
    sessions = [SimpleNamespace(path="first"), SimpleNamespace(path="second")]
    calls: list[str] = []

    async def delete(_hass, session):
        calls.append(session.path)
        raise first_error if session is sessions[0] else second_error

    monkeypatch.setattr(backup_transfer, "_async_delete_session_file", delete)

    with pytest.raises(asyncio.CancelledError) as raised:
        await backup_transfer._async_delete_sessions(object(), sessions)

    assert raised.value is first_error
    assert calls == ["first", "second"]


def test_resolve_agent_success_and_transfer_redaction(monkeypatch):
    """Cover the successful lookup and portable-transfer redaction branches."""
    subentry = SimpleNamespace(subentry_type="conversation")
    entry = SimpleNamespace(domain=DOMAIN, subentries={"agent-1": subentry})
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=lambda entry_id: entry)
    )

    assert backup_transfer._resolve_agent(hass, "entry-1", "agent-1") == (
        entry,
        subentry,
    )

    redacted = {"redacted": True}
    redact = MagicMock(return_value=redacted)
    monkeypatch.setattr(transfer, "redact_transfer_document", redact)
    document = {"format": transfer.TRANSFER_FORMAT, "agent": {"title": "Portable"}}

    assert backup_transfer._redacted_document(document) is redacted
    redact.assert_called_once_with(document)


def test_archive_loader_rejects_short_actual_payload_after_valid_metadata(
    tmp_path, monkeypatch
):
    """A short bounded read is rejected even when ZIP metadata and manifest agree."""
    path = tmp_path / "short-payload.zip"
    payload = b"{}  "
    manifest = {
        "format": backup_transfer.ARCHIVE_FORMAT,
        "version": backup_transfer.ARCHIVE_VERSION,
        "payload": backup_transfer.PAYLOAD_NAME,
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(backup_transfer.PAYLOAD_NAME, payload)
        archive.writestr(
            backup_transfer.MANIFEST_NAME,
            json.dumps(manifest, separators=(",", ":")),
        )

    real_read = backup_transfer._read_archive_member_bounded

    def short_payload(archive, name, limit, overflow_message, corruption_message):
        if name == backup_transfer.PAYLOAD_NAME:
            return payload[:-1]
        return real_read(archive, name, limit, overflow_message, corruption_message)

    monkeypatch.setattr(
        backup_transfer, "_read_archive_member_bounded", short_payload
    )

    with pytest.raises(backup.BackupError, match="payload is incomplete"):
        backup_transfer._load_archive_document(str(path))


def test_legacy_json_loader_rejects_read_overflow_when_stat_underreports(
    tmp_path, monkeypatch
):
    """The actual read ceiling protects against a file growing after stat()."""
    path = tmp_path / "growing.json"
    path.write_bytes(b"12345")
    monkeypatch.setattr(backup_transfer, "MAX_BACKUP_UNCOMPRESSED_BYTES", 4)
    real_getsize = backup_transfer.os.path.getsize

    def underreported_size(candidate):
        if candidate == str(path):
            return 4
        return real_getsize(candidate)

    monkeypatch.setattr(backup_transfer.os.path, "getsize", underreported_size)

    with pytest.raises(backup.BackupError, match="JSON backup exceeds"):
        backup_transfer._load_legacy_json_document(str(path))


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
    monkeypatch.setattr(backup_transfer, "_ensure_session_capacity", lambda *_args: None)
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
    session = _export_session("/missing/archive.zip")
    backup_transfer._exports(hass)[session.session_id] = session
    hass.async_add_executor_job = AsyncMock(side_effect=OSError("gone"))
    monkeypatch.setattr(backup_transfer, "_async_cleanup_expired", AsyncMock())

    with pytest.raises(backup.BackupError, match="staged backup archive is unavailable"):
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
    monkeypatch.setattr(backup_transfer, "_ensure_session_capacity", lambda *_args: None)
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


async def test_import_chunk_rejects_already_complete_session(monkeypatch):
    """A complete session cannot accept an additional correctly-ordered chunk."""
    hass = _hass()
    session = _import_session("/tmp/upload", received=4, next_index=1)
    backup_transfer._imports(hass)[session.session_id] = session
    monkeypatch.setattr(backup_transfer, "_async_cleanup_expired", AsyncMock())

    with pytest.raises(backup.BackupError, match="upload is already complete"):
        await backup_transfer._import_chunk(
            hass,
            "entry-1",
            "agent-1",
            {
                "session_id": session.session_id,
                "index": 1,
                "data": base64.b64encode(b"data").decode("ascii"),
            },
        )


async def test_inspect_import_rejects_session_removed_while_waiting_for_lock(
    monkeypatch,
):
    """Inspection re-checks ownership of the session after acquiring its lock."""
    hass = _hass()
    session = _import_session("/tmp/upload")
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
    monkeypatch.setattr(backup_transfer, "_resolve_agent", lambda *_args: (entry, subentry))
    monkeypatch.setattr(backup_transfer, "_async_cleanup_expired", AsyncMock())
    session = _import_session("/tmp/upload", subentry_id="agent-other")
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


def test_stop_callback_schedules_full_transfer_cleanup(monkeypatch):
    """HA shutdown invokes the cleanup callback registered during websocket setup."""
    listen_once = MagicMock()
    create_task = MagicMock()
    hass = SimpleNamespace(
        data={},
        bus=SimpleNamespace(async_listen_once=listen_once),
        async_create_task=create_task,
    )
    monkeypatch.setattr(
        backup_transfer.websocket_api, "async_register_command", MagicMock()
    )

    assert backup_transfer.setup_backup_transfer_websocket(hass) is True
    callback = listen_once.call_args.args[1]
    callback(SimpleNamespace())

    create_task.assert_called_once()
    coroutine = create_task.call_args.args[0]
    assert create_task.call_args.args[1] == "clean up full backup transfers"
    coroutine.close()
