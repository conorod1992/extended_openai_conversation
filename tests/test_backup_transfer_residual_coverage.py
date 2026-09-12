"""Residual branch coverage for backup transfer failure and cleanup paths."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import zipfile

import pytest

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses import backup_transfer
from custom_components.extended_openai_conversation_responses import transfer
from custom_components.extended_openai_conversation_responses.const import DOMAIN


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


def _write_archive(path, payload: bytes, manifest: dict | bytes | None = None) -> None:
    if manifest is None:
        manifest = {
            "format": backup_transfer.ARCHIVE_FORMAT,
            "version": backup_transfer.ARCHIVE_VERSION,
            "payload": backup_transfer.PAYLOAD_NAME,
            "payload_bytes": len(payload),
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
        }
    manifest_bytes = (
        manifest
        if isinstance(manifest, bytes)
        else json.dumps(manifest, separators=(",", ":")).encode()
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(backup_transfer.PAYLOAD_NAME, payload)
        archive.writestr(backup_transfer.MANIFEST_NAME, manifest_bytes)


async def test_remove_path_finishes_executor_cleanup_before_cancellation(tmp_path):
    """Cancellation cannot leave an executor-backed delete operation unfinished."""
    path = tmp_path / "pending-delete"
    path.write_bytes(b"data")
    release = asyncio.Event()

    async def executor_job(func, *args):
        await release.wait()
        return func(*args)

    hass = SimpleNamespace(async_add_executor_job=executor_job)
    task = asyncio.create_task(backup_transfer._async_remove_path(hass, str(path)))
    await asyncio.sleep(0)
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert not path.exists()


async def test_delete_sessions_continues_after_one_cleanup_is_cancelled(monkeypatch):
    """All detached files are attempted before cancellation is re-raised."""
    first = SimpleNamespace(path="first")
    second = SimpleNamespace(path="second")
    calls: list[str] = []

    async def delete(_hass, session):
        calls.append(session.path)
        if session is first:
            raise asyncio.CancelledError

    monkeypatch.setattr(backup_transfer, "_async_delete_session_file", delete)

    with pytest.raises(asyncio.CancelledError):
        await backup_transfer._async_delete_sessions(object(), [first, second])
    assert calls == ["first", "second"]


def test_resolve_agent_rejects_missing_wrong_domain_and_wrong_subentry_type():
    entries = {}
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=lambda entry_id: entries.get(entry_id))
    )

    with pytest.raises(backup.BackupError, match="Integration entry not found"):
        backup_transfer._resolve_agent(hass, "missing", "agent")

    entries["entry"] = SimpleNamespace(domain="other", subentries={})
    with pytest.raises(backup.BackupError, match="Integration entry not found"):
        backup_transfer._resolve_agent(hass, "entry", "agent")

    entries["entry"] = SimpleNamespace(
        domain=DOMAIN,
        subentries={"agent": SimpleNamespace(subentry_type="not-conversation")},
    )
    with pytest.raises(backup.BackupError, match="Conversation agent not found"):
        backup_transfer._resolve_agent(hass, "entry", "agent")


def test_redacted_document_uses_legacy_safe_configuration(monkeypatch):
    """Legacy full-backup documents redact both config-bearing sections."""
    calls = []

    def safe(value):
        calls.append(value)
        return {"safe": value["secret"]}

    monkeypatch.setattr(backup._safe_configuration, "__wrapped__", None, raising=False)
    monkeypatch.setattr(backup, "_safe_configuration", safe)
    snapshot = {
        "agent": {"title": "Agent", "config": {"secret": "agent"}},
        "request_rules": {"secret": "rules"},
    }

    result = backup_transfer._redacted_document(snapshot)

    assert result["agent"]["config"] == {"safe": "agent"}
    assert result["request_rules"] == {"safe": "rules"}
    assert calls == [{"secret": "agent"}, {"secret": "rules"}]


def test_build_archive_removes_temp_file_when_uncompressed_limit_is_exceeded(monkeypatch):
    """Failed archive construction does not leak its private temporary file."""
    monkeypatch.setattr(backup_transfer, "MAX_BACKUP_UNCOMPRESSED_BYTES", 1)
    created: list[str] = []
    real_mkstemp = backup_transfer.tempfile.mkstemp

    def mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        created.append(path)
        return fd, path

    monkeypatch.setattr(backup_transfer.tempfile, "mkstemp", mkstemp)
    snapshot = {
        "agent": {"title": "Agent", "config": {}},
        "request_rules": {},
        "created_at": "2026-09-12T12:00:00Z",
    }

    with pytest.raises(backup.BackupError, match="uncompressed safety limit"):
        backup_transfer._build_archive_file(snapshot)
    assert created and not os.path.exists(created[0])


def test_build_archive_removes_temp_file_when_compressed_limit_is_exceeded(monkeypatch):
    monkeypatch.setattr(backup_transfer, "MAX_BACKUP_ARCHIVE_BYTES", 1)
    created: list[str] = []
    real_mkstemp = backup_transfer.tempfile.mkstemp

    def mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        created.append(path)
        return fd, path

    monkeypatch.setattr(backup_transfer.tempfile, "mkstemp", mkstemp)
    snapshot = {
        "agent": {"title": "Agent", "config": {}},
        "request_rules": {},
        "created_at": "2026-09-12T12:00:00Z",
    }

    with pytest.raises(backup.BackupError, match="compressed safety limit"):
        backup_transfer._build_archive_file(snapshot)
    assert created and not os.path.exists(created[0])


async def test_async_build_archive_cancellation_cleans_late_result(tmp_path, monkeypatch):
    path = tmp_path / "late.zip"
    path.write_bytes(b"archive")
    removed = AsyncMock()
    monkeypatch.setattr(backup_transfer, "_async_remove_path", removed)

    async def executor_job(_func, _snapshot):
        await asyncio.sleep(0.01)
        return {"path": str(path)}

    hass = SimpleNamespace(async_add_executor_job=executor_job)
    task = asyncio.create_task(backup_transfer._async_build_archive_file(hass, {}))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    removed.assert_awaited_once_with(hass, str(path))


async def test_async_build_archive_cancellation_preserves_cancellation_if_worker_fails():
    async def executor_job(_func, _snapshot):
        await asyncio.sleep(0.01)
        raise RuntimeError("worker failed")

    hass = SimpleNamespace(async_add_executor_job=executor_job)
    task = asyncio.create_task(backup_transfer._async_build_archive_file(hass, {}))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_async_create_upload_cancellation_cleans_late_file(tmp_path, monkeypatch):
    path = tmp_path / "late-upload"
    removed = AsyncMock()
    monkeypatch.setattr(backup_transfer, "_async_remove_path", removed)

    async def executor_job(_func):
        await asyncio.sleep(0.01)
        return str(path)

    hass = SimpleNamespace(async_add_executor_job=executor_job)
    task = asyncio.create_task(backup_transfer._async_create_upload_file(hass))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    removed.assert_awaited_once_with(hass, str(path))


async def test_async_create_upload_cancellation_preserves_cancellation_if_worker_fails():
    async def executor_job(_func):
        await asyncio.sleep(0.01)
        raise OSError("disk failed")

    hass = SimpleNamespace(async_add_executor_job=executor_job)
    task = asyncio.create_task(backup_transfer._async_create_upload_file(hass))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_async_append_waits_for_executor_before_propagating_cancellation(tmp_path):
    path = tmp_path / "upload"
    path.write_bytes(b"")
    release = asyncio.Event()

    async def executor_job(func, *args):
        await release.wait()
        return func(*args)

    hass = SimpleNamespace(async_add_executor_job=executor_job)
    task = asyncio.create_task(
        backup_transfer._async_append_file(
            hass, str(path), base64.b64encode(b"data").decode(), 4
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert path.read_bytes() == b"data"


def test_member_safety_rejects_unsafe_zip_entries():
    assert backup_transfer._member_is_safe(zipfile.ZipInfo("backup.json")) is True
    for name in ("../backup.json", "/backup.json", "folder\\backup.json", ""):
        assert backup_transfer._member_is_safe(zipfile.ZipInfo(name)) is False

    directory = zipfile.ZipInfo("folder/")
    assert backup_transfer._member_is_safe(directory) is False
    symlink = zipfile.ZipInfo("backup.json")
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    assert backup_transfer._member_is_safe(symlink) is False
    encrypted = zipfile.ZipInfo("backup.json")
    encrypted.flag_bits |= 0x1
    assert backup_transfer._member_is_safe(encrypted) is False


def test_read_archive_member_bounded_rejects_actual_growth_and_read_errors(tmp_path):
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("backup.json", b"12345")
    with zipfile.ZipFile(path, "r") as archive:
        with pytest.raises(backup.BackupError, match="too large"):
            backup_transfer._read_archive_member_bounded(
                archive, "backup.json", 4, "too large", "corrupt"
            )

    broken = SimpleNamespace(open=MagicMock(side_effect=OSError("broken")))
    with pytest.raises(backup.BackupError, match="corrupt"):
        backup_transfer._read_archive_member_bounded(
            broken, "backup.json", 4, "too large", "corrupt"
        )


def test_archive_loader_rejects_unavailable_oversized_and_bad_zip(tmp_path, monkeypatch):
    missing = tmp_path / "missing.zip"
    with pytest.raises(backup.BackupError, match="archive is unavailable"):
        backup_transfer._load_archive_document(str(missing))

    path = tmp_path / "backup.zip"
    path.write_bytes(b"not-a-zip")
    real_getsize = backup_transfer.os.path.getsize
    monkeypatch.setattr(
        backup_transfer.os.path,
        "getsize",
        lambda candidate: backup_transfer.MAX_BACKUP_ARCHIVE_BYTES + 1
        if candidate == str(path)
        else real_getsize(candidate),
    )
    with pytest.raises(backup.BackupError, match="compressed backup exceeds"):
        backup_transfer._load_archive_document(str(path))
    monkeypatch.setattr(backup_transfer.os.path, "getsize", real_getsize)

    with pytest.raises(backup.BackupError, match="incomplete or corrupted"):
        backup_transfer._load_archive_document(str(path))


def test_archive_loader_rejects_unexpected_and_unsafe_members(tmp_path):
    unexpected = tmp_path / "unexpected.zip"
    with zipfile.ZipFile(unexpected, "w") as archive:
        archive.writestr("only-one", b"x")
    with pytest.raises(backup.BackupError, match="unexpected files"):
        backup_transfer._load_archive_document(str(unexpected))

    duplicate = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr(backup_transfer.PAYLOAD_NAME, b"{}")
        archive.writestr(backup_transfer.PAYLOAD_NAME, b"{}")
    with pytest.raises(backup.BackupError, match="unexpected files"):
        backup_transfer._load_archive_document(str(duplicate))

    unsafe = tmp_path / "unsafe.zip"
    payload = b"{}"
    manifest = {
        "format": backup_transfer.ARCHIVE_FORMAT,
        "version": backup_transfer.ARCHIVE_VERSION,
        "payload": backup_transfer.PAYLOAD_NAME,
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    with zipfile.ZipFile(unsafe, "w") as archive:
        payload_info = zipfile.ZipInfo(backup_transfer.PAYLOAD_NAME)
        payload_info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(payload_info, payload)
        archive.writestr(backup_transfer.MANIFEST_NAME, json.dumps(manifest))
    with pytest.raises(backup.BackupError, match="unsafe file entry"):
        backup_transfer._load_archive_document(str(unsafe))


def test_archive_loader_rejects_invalid_manifest_shapes(tmp_path):
    payload = b"{}"
    base = {
        "format": backup_transfer.ARCHIVE_FORMAT,
        "version": backup_transfer.ARCHIVE_VERSION,
        "payload": backup_transfer.PAYLOAD_NAME,
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    cases = [
        ["not", "an", "object"],
        {**base, "extra": True},
        {**base, "payload_bytes": True},
        {**base, "payload_bytes": 0},
        {**base, "payload_sha256": "not-a-sha"},
    ]
    for index, manifest in enumerate(cases):
        path = tmp_path / f"invalid-manifest-{index}.zip"
        _write_archive(path, payload, manifest)
        with pytest.raises(backup.BackupError, match="manifest is invalid"):
            backup_transfer._load_archive_document(str(path))


def test_archive_loader_rejects_payload_size_disagreement(tmp_path):
    payload = b"{}"
    manifest = {
        "format": backup_transfer.ARCHIVE_FORMAT,
        "version": backup_transfer.ARCHIVE_VERSION,
        "payload": backup_transfer.PAYLOAD_NAME,
        "payload_bytes": len(payload) + 1,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    path = tmp_path / "bad-size.zip"
    _write_archive(path, payload, manifest)
    with pytest.raises(backup.BackupError, match="manifest is invalid"):
        backup_transfer._load_archive_document(str(path))


async def test_start_export_rejects_unknown_mode_before_collection(hass):
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")
    with pytest.raises(backup.BackupError, match="mode must be full or custom"):
        await backup_transfer._start_export(
            hass, entry, subentry, {"mode": "mystery"}
        )


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
    monkeypatch.setattr(backup_transfer, "get_agent_maintenance_gate", lambda *_args: gate)
    monkeypatch.setattr(
        backup, "async_collect_backup_snapshot", AsyncMock(return_value={"snapshot": True})
    )
    monkeypatch.setattr(
        backup_transfer,
        "_async_build_archive_file",
        AsyncMock(side_effect=RuntimeError("builder failed")),
    )

    with pytest.raises(backup.BackupError, match="could not be created safely"):
        await backup_transfer._start_export(hass, entry, subentry)


async def test_import_chunk_cancellation_discards_staging_session(hass, tmp_path, monkeypatch):
    path = tmp_path / "upload"
    path.write_bytes(b"")
    session = _import_session(str(path), received=0, next_index=0)
    backup_transfer._imports(hass)[session.session_id] = session
    monkeypatch.setattr(
        backup_transfer,
        "_async_append_file",
        AsyncMock(side_effect=asyncio.CancelledError),
    )

    with pytest.raises(asyncio.CancelledError):
        await backup_transfer._import_chunk(
            hass,
            "entry-1",
            "agent-1",
            {
                "session_id": session.session_id,
                "index": 0,
                "data": base64.b64encode(b"data").decode(),
            },
        )
    assert session.session_id not in backup_transfer._imports(hass)
    assert not path.exists()


async def test_take_completed_import_rejects_session_removed_during_lock(hass, tmp_path, monkeypatch):
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


async def test_take_completed_import_rechecks_completion_under_lock(hass, tmp_path, monkeypatch):
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
async def test_command_dispatches_each_transfer_action(hass, monkeypatch, action, target):
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")
    monkeypatch.setattr(backup_transfer, "_resolve_agent", lambda *_args: (entry, subentry))
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


async def test_command_cancel_enforces_owner_before_deleting(hass, tmp_path, monkeypatch):
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")
    monkeypatch.setattr(backup_transfer, "_resolve_agent", lambda *_args: (entry, subentry))
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


async def test_websocket_translates_expected_error_and_returns_success(monkeypatch):
    connection = SimpleNamespace(send_error=MagicMock(), send_result=MagicMock())
    message = {
        "id": 7,
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "action": "noop",
    }
    monkeypatch.setattr(
        backup_transfer,
        "async_backup_transfer_command",
        AsyncMock(side_effect=backup.BackupError("bad request")),
    )
    await backup_transfer.websocket_backup_transfer(object(), connection, message)
    connection.send_error.assert_called_once_with(7, "invalid_request", "bad request")
    connection.send_result.assert_not_called()

    connection.send_error.reset_mock()
    monkeypatch.setattr(
        backup_transfer,
        "async_backup_transfer_command",
        AsyncMock(return_value={"ok": True}),
    )
    await backup_transfer.websocket_backup_transfer(object(), connection, message)
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


def test_setup_backup_transfer_websocket_registers_once(hass, monkeypatch):
    register = MagicMock()
    listen_once = MagicMock()
    monkeypatch.setattr(backup_transfer.websocket_api, "async_register_command", register)
    monkeypatch.setattr(hass.bus, "async_listen_once", listen_once)

    assert backup_transfer.setup_backup_transfer_websocket(hass) is True
    assert backup_transfer.setup_backup_transfer_websocket(hass) is False
    register.assert_called_once_with(hass, backup_transfer.websocket_backup_transfer)
    listen_once.assert_called_once()
