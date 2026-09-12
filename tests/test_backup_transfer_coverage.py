"""Focused branch coverage for bounded backup transfer safeguards."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from types import SimpleNamespace
import zipfile

import pytest

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses import backup_transfer
from custom_components.extended_openai_conversation_responses import transfer
from tests.test_backup_transfer import _write_archive


def _session_path(tmp_path, name: str = "session.tmp") -> str:
    path = tmp_path / name
    path.write_bytes(b"")
    return str(path)


def _import_session(tmp_path, **overrides) -> backup_transfer.ImportSession:
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


def _export_session(tmp_path, **overrides) -> backup_transfer.ExportSession:
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


async def test_cleanup_expired_detaches_and_deletes_both_session_types(
    hass, tmp_path
) -> None:
    export = _export_session(
        tmp_path,
        session_id="expired-export",
        expires_at=backup_transfer.time.monotonic() - 1,
    )
    imported = _import_session(
        tmp_path,
        session_id="expired-import",
        path=_session_path(tmp_path, "import.tmp"),
        expires_at=backup_transfer.time.monotonic() - 1,
    )
    backup_transfer._exports(hass)[export.session_id] = export
    backup_transfer._imports(hass)[imported.session_id] = imported

    await backup_transfer._async_cleanup_expired(hass)

    assert backup_transfer._exports(hass) == {}
    assert backup_transfer._imports(hass) == {}
    assert not os.path.exists(export.path)
    assert not os.path.exists(imported.path)


def test_session_capacity_rejects_count_and_negative_reservation(
    hass, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(backup_transfer, "MAX_TRANSFER_SESSIONS", 1)
    export = _export_session(tmp_path)
    backup_transfer._exports(hass)[export.session_id] = export

    with pytest.raises(backup.BackupError, match="Too many"):
        backup_transfer._ensure_session_capacity(hass)

    backup_transfer._exports(hass).clear()
    with pytest.raises(backup.BackupError, match="temporary storage quota"):
        backup_transfer._ensure_session_capacity(hass, -1)


def test_safe_export_filename_handles_custom_and_empty_titles() -> None:
    document = {
        "format": transfer.TRANSFER_FORMAT,
        "created_at": "2026-09-12T10:00:00+00:00",
        "agent": {"title": "!!!"},
    }

    assert backup_transfer._safe_export_filename(document) == (
        "conversation-agent-custom-backup-2026-09-12.zip"
    )


def test_read_file_chunk_rejects_truncated_staged_archive(tmp_path) -> None:
    path = tmp_path / "short.zip"
    path.write_bytes(b"abc")

    with pytest.raises(backup.BackupError, match="incomplete"):
        backup_transfer._read_file_chunk_base64(str(path), 0, 4)


@pytest.mark.parametrize(
    ("encoded", "expected_length", "match"),
    [
        ("%%%", 1, "not valid base64"),
        (base64.b64encode(b"ab").decode(), 1, "unexpected length"),
    ],
)
def test_decode_append_file_rejects_invalid_chunks(
    tmp_path, encoded, expected_length, match
) -> None:
    path = tmp_path / "upload.tmp"
    path.write_bytes(b"")

    with pytest.raises(backup.BackupError, match=match):
        backup_transfer._decode_append_file(str(path), encoded, expected_length)

    assert path.read_bytes() == b""


def test_archive_rejects_unsupported_compression(tmp_path) -> None:
    path = tmp_path / "unsupported.zip"
    payload = json.dumps({"value": 1}).encode()
    manifest = {
        "format": backup_transfer.ARCHIVE_FORMAT,
        "version": backup_transfer.ARCHIVE_VERSION,
        "payload": backup_transfer.PAYLOAD_NAME,
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_BZIP2) as archive:
        archive.writestr(backup_transfer.PAYLOAD_NAME, payload)
        archive.writestr(backup_transfer.MANIFEST_NAME, json.dumps(manifest))

    with pytest.raises(backup.BackupError, match="unsupported compression"):
        backup_transfer._load_archive_document(str(path))


def test_archive_rejects_manifest_larger_than_limit(tmp_path, monkeypatch) -> None:
    path = tmp_path / "large-manifest.zip"
    payload = b"{}"
    _write_archive(path, payload)
    monkeypatch.setattr(backup_transfer, "MAX_MANIFEST_BYTES", 8)

    with pytest.raises(backup.BackupError, match="manifest is too large"):
        backup_transfer._load_archive_document(str(path))


@pytest.mark.parametrize(
    ("payload", "manifest_override", "match"),
    [
        (b"not-json", None, "not valid JSON"),
        (b"[]", None, "payload is invalid"),
        (b"{}", {"payload_sha256": "0" * 64}, "checksum does not match"),
    ],
)
def test_archive_rejects_bad_payloads(
    tmp_path, payload, manifest_override, match
) -> None:
    path = tmp_path / "bad-payload.zip"
    manifest = {
        "format": backup_transfer.ARCHIVE_FORMAT,
        "version": backup_transfer.ARCHIVE_VERSION,
        "payload": backup_transfer.PAYLOAD_NAME,
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    if manifest_override:
        manifest.update(manifest_override)
    _write_archive(path, payload, manifest=manifest)

    with pytest.raises(backup.BackupError, match=match):
        backup_transfer._load_archive_document(str(path))


def test_archive_rejects_invalid_manifest_json(tmp_path) -> None:
    path = tmp_path / "invalid-manifest.zip"
    payload = b"{}"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(backup_transfer.PAYLOAD_NAME, payload)
        archive.writestr(backup_transfer.MANIFEST_NAME, b"{")

    with pytest.raises(backup.BackupError, match="manifest is invalid"):
        backup_transfer._load_archive_document(str(path))


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (b"{", "incomplete or corrupted"),
        (b"[]", "not an Extended OpenAI Conversation backup"),
    ],
)
def test_legacy_json_rejects_malformed_documents(tmp_path, payload, match) -> None:
    path = tmp_path / "backup.json"
    path.write_bytes(payload)

    with pytest.raises(backup.BackupError, match=match):
        backup_transfer._load_legacy_json_document(str(path))


def test_legacy_json_rejects_oversized_file(tmp_path, monkeypatch) -> None:
    path = tmp_path / "backup.json"
    path.write_bytes(b"{}")
    monkeypatch.setattr(backup_transfer, "MAX_BACKUP_UNCOMPRESSED_BYTES", 1)

    with pytest.raises(backup.BackupError, match="128 MB safety limit"):
        backup_transfer._load_legacy_json_document(str(path))


def test_legacy_json_reports_unavailable_file(tmp_path) -> None:
    with pytest.raises(backup.BackupError, match="staged JSON backup is unavailable"):
        backup_transfer._load_legacy_json_document(str(tmp_path / "missing.json"))


@pytest.mark.parametrize(
    ("filename", "kind", "match"),
    [
        ("plain.json", "archive", "selected ZIP backup"),
        ("archive.zip", "legacy_json", "unexpectedly contains a ZIP archive"),
    ],
)
def test_uploaded_document_rejects_kind_signature_mismatch(
    tmp_path, filename, kind, match
) -> None:
    path = tmp_path / filename
    path.write_bytes(b"PKxx" if filename.endswith(".zip") else b"{}")

    with pytest.raises(backup.BackupError, match=match):
        backup_transfer._load_uploaded_document(str(path), kind)


def test_uploaded_document_reports_unavailable_staged_file(tmp_path) -> None:
    with pytest.raises(backup.BackupError, match="staged backup upload is unavailable"):
        backup_transfer._load_uploaded_document(str(tmp_path / "missing"), "auto")


async def test_discard_missing_sessions_is_false(hass) -> None:
    assert await backup_transfer._discard_export(hass, "missing") is False
    assert await backup_transfer._discard_import(hass, "missing") is False


def test_session_identity_rejects_cross_agent_access(tmp_path) -> None:
    session = _export_session(tmp_path)

    with pytest.raises(backup.BackupError, match="does not belong to this agent"):
        backup_transfer._require_session_identity(session, "entry-2", "agent-1")


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ({"index": 0}, "session is required"),
        ({"session_id": "x", "index": True}, "index is invalid"),
        ({"session_id": "x", "index": -1}, "index is invalid"),
    ],
)
async def test_export_chunk_rejects_invalid_request_fields(hass, data, match) -> None:
    with pytest.raises(backup.BackupError, match=match):
        await backup_transfer._export_chunk(hass, "entry-1", "agent-1", data)


async def test_export_chunk_rejects_missing_wrong_owner_and_out_of_range(
    hass, tmp_path
) -> None:
    with pytest.raises(backup.BackupError, match="expired or does not exist"):
        await backup_transfer._export_chunk(
            hass, "entry-1", "agent-1", {"session_id": "missing", "index": 0}
        )

    session = _export_session(tmp_path)
    backup_transfer._exports(hass)[session.session_id] = session
    with pytest.raises(backup.BackupError, match="does not belong"):
        await backup_transfer._export_chunk(
            hass, "entry-2", "agent-1", {"session_id": session.session_id, "index": 0}
        )
    with pytest.raises(backup.BackupError, match="out of range"):
        await backup_transfer._export_chunk(
            hass, "entry-1", "agent-1", {"session_id": session.session_id, "index": 1}
        )


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ({"filename": "", "size": 1}, "filename is invalid"),
        ({"filename": "x.json", "size": True}, "file size is invalid"),
        ({"filename": "x.json", "size": 0}, "file size is invalid"),
    ],
)
async def test_start_import_rejects_invalid_metadata(hass, data, match) -> None:
    with pytest.raises(backup.BackupError, match=match):
        await backup_transfer._start_import(hass, "entry-1", "agent-1", data)


async def test_start_import_enforces_archive_size_limit(hass, monkeypatch) -> None:
    monkeypatch.setattr(backup_transfer, "MAX_BACKUP_ARCHIVE_BYTES", 1)

    with pytest.raises(backup.BackupError, match="compressed backup exceeds"):
        await backup_transfer._start_import(
            hass,
            "entry-1",
            "agent-1",
            {"filename": "backup.zip", "size": 2},
        )


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


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ({"index": 0, "data": "YQ=="}, "session is required"),
        ({"session_id": "x", "index": True, "data": "YQ=="}, "index is invalid"),
        ({"session_id": "x", "index": 0, "data": ""}, "chunk is invalid"),
    ],
)
async def test_import_chunk_rejects_invalid_request_fields(hass, data, match) -> None:
    with pytest.raises(backup.BackupError, match=match):
        await backup_transfer._import_chunk(hass, "entry-1", "agent-1", data)


async def test_import_chunk_rejects_missing_wrong_owner_and_completed_upload(
    hass, tmp_path
) -> None:
    data = {"session_id": "missing", "index": 0, "data": "YQ=="}
    with pytest.raises(backup.BackupError, match="expired or does not exist"):
        await backup_transfer._import_chunk(hass, "entry-1", "agent-1", data)

    session = _import_session(tmp_path)
    backup_transfer._imports(hass)[session.session_id] = session
    data["session_id"] = session.session_id
    with pytest.raises(backup.BackupError, match="does not belong"):
        await backup_transfer._import_chunk(hass, "entry-2", "agent-1", data)

    session.received = session.expected_size
    with pytest.raises(backup.BackupError, match="already complete"):
        await backup_transfer._import_chunk(hass, "entry-1", "agent-1", data)


async def test_import_chunk_storage_error_discards_session(
    hass, tmp_path, monkeypatch
) -> None:
    session = _import_session(tmp_path)
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


@pytest.mark.parametrize("session_id", [None, ""])
def test_completed_import_requires_session_id(hass, session_id) -> None:
    with pytest.raises(backup.BackupError, match="session is required"):
        backup_transfer._completed_import(hass, session_id, "entry-1", "agent-1")


def test_completed_import_rejects_missing_and_wrong_owner(hass, tmp_path) -> None:
    with pytest.raises(backup.BackupError, match="expired or does not exist"):
        backup_transfer._completed_import(hass, "missing", "entry-1", "agent-1")

    session = _import_session(tmp_path, received=1)
    backup_transfer._imports(hass)[session.session_id] = session
    with pytest.raises(backup.BackupError, match="does not belong"):
        backup_transfer._completed_import(hass, session.session_id, "other", "agent-1")


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
