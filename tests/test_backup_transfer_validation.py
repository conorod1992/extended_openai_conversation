"""Backup transfer archive and request validation."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from types import SimpleNamespace
from unittest.mock import MagicMock
import zipfile

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    backup_transfer,
    transfer,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from tests.test_backup_transfer import _write_archive


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


def test_session_capacity_rejects_count_and_negative_reservation(
    hass, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(backup_transfer, "MAX_TRANSFER_SESSIONS", 1)
    export = _empty_export_session(tmp_path)
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


def test_session_identity_rejects_cross_agent_access(tmp_path) -> None:
    session = _empty_export_session(tmp_path)

    with pytest.raises(backup.BackupError, match="does not belong to this agent"):
        backup_transfer._require_session_identity(session, "entry-2", "agent-1")


@pytest.mark.parametrize("session_id", [None, ""])
def test_completed_import_requires_session_id(hass, session_id) -> None:
    with pytest.raises(backup.BackupError, match="session is required"):
        backup_transfer._completed_import(hass, session_id, "entry-1", "agent-1")


def test_completed_import_rejects_missing_and_wrong_owner(hass, tmp_path) -> None:
    with pytest.raises(backup.BackupError, match="expired or does not exist"):
        backup_transfer._completed_import(hass, "missing", "entry-1", "agent-1")

    session = _empty_import_session(tmp_path, received=1)
    backup_transfer._imports(hass)[session.session_id] = session
    with pytest.raises(backup.BackupError, match="does not belong"):
        backup_transfer._completed_import(hass, session.session_id, "other", "agent-1")


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

    monkeypatch.setattr(backup_transfer, "_read_archive_member_bounded", short_payload)

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


def _write_raw_manifest_archive(
    path, payload: bytes, manifest: dict | bytes | None = None
) -> None:
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


def test_resolve_agent_rejects_missing_wrong_domain_and_wrong_subentry_type():
    entries = {}
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_get_entry=lambda entry_id: entries.get(entry_id)
        )
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


def test_build_archive_removes_temp_file_when_uncompressed_limit_is_exceeded(
    monkeypatch,
):
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


def test_member_safety_rejects_unsafe_zip_entries():
    assert backup_transfer._member_is_safe(zipfile.ZipInfo("backup.json")) is True
    for name in ("../backup.json", "/backup.json", "folder\\backup.json", ""):
        member = zipfile.ZipInfo("backup.json")
        # ZipInfo normalizes backslashes on Windows; exercise the raw unsafe name.
        member.filename = name
        assert backup_transfer._member_is_safe(member) is False

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
    with (
        zipfile.ZipFile(path, "r") as archive,
        pytest.raises(backup.BackupError, match="too large"),
    ):
        backup_transfer._read_archive_member_bounded(
            archive, "backup.json", 4, "too large", "corrupt"
        )

    broken = SimpleNamespace(open=MagicMock(side_effect=OSError("broken")))
    with pytest.raises(backup.BackupError, match="corrupt"):
        backup_transfer._read_archive_member_bounded(
            broken, "backup.json", 4, "too large", "corrupt"
        )


def test_archive_loader_rejects_unavailable_oversized_and_bad_zip(
    tmp_path, monkeypatch
):
    missing = tmp_path / "missing.zip"
    with pytest.raises(backup.BackupError, match="archive is unavailable"):
        backup_transfer._load_archive_document(str(missing))

    path = tmp_path / "backup.zip"
    path.write_bytes(b"not-a-zip")
    real_getsize = backup_transfer.os.path.getsize
    monkeypatch.setattr(
        backup_transfer.os.path,
        "getsize",
        lambda candidate: (
            backup_transfer.MAX_BACKUP_ARCHIVE_BYTES + 1
            if candidate == str(path)
            else real_getsize(candidate)
        ),
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
        _write_raw_manifest_archive(path, payload, manifest)
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
    _write_raw_manifest_archive(path, payload, manifest)
    with pytest.raises(backup.BackupError, match="manifest is invalid"):
        backup_transfer._load_archive_document(str(path))


def test_setup_backup_transfer_websocket_registers_once(hass, monkeypatch):
    register = MagicMock()
    listen_once = MagicMock()
    monkeypatch.setattr(
        backup_transfer.websocket_api, "async_register_command", register
    )
    monkeypatch.setattr(hass.bus, "async_listen_once", listen_once)

    assert backup_transfer.setup_backup_transfer_websocket(hass) is True
    assert backup_transfer.setup_backup_transfer_websocket(hass) is False
    register.assert_called_once_with(hass, backup_transfer.websocket_backup_transfer)
    listen_once.assert_called_once()


async def test_start_export_rejects_unknown_mode_before_collection(hass):
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")
    with pytest.raises(backup.BackupError, match="mode must be full or custom"):
        await backup_transfer._start_export(hass, entry, subentry, {"mode": "mystery"})


async def test_import_chunk_rejects_missing_wrong_owner_and_completed_upload(
    hass, tmp_path
) -> None:
    data = {"session_id": "missing", "index": 0, "data": "YQ=="}
    with pytest.raises(backup.BackupError, match="expired or does not exist"):
        await backup_transfer._import_chunk(hass, "entry-1", "agent-1", data)

    session = _empty_import_session(tmp_path)
    backup_transfer._imports(hass)[session.session_id] = session
    data["session_id"] = session.session_id
    with pytest.raises(backup.BackupError, match="does not belong"):
        await backup_transfer._import_chunk(hass, "entry-2", "agent-1", data)

    session.received = session.expected_size
    with pytest.raises(backup.BackupError, match="already complete"):
        await backup_transfer._import_chunk(hass, "entry-1", "agent-1", data)


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


async def test_start_import_enforces_archive_size_limit(hass, monkeypatch) -> None:
    monkeypatch.setattr(backup_transfer, "MAX_BACKUP_ARCHIVE_BYTES", 1)

    with pytest.raises(backup.BackupError, match="compressed backup exceeds"):
        await backup_transfer._start_import(
            hass,
            "entry-1",
            "agent-1",
            {"filename": "backup.zip", "size": 2},
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


async def test_export_chunk_rejects_missing_wrong_owner_and_out_of_range(
    hass, tmp_path
) -> None:
    with pytest.raises(backup.BackupError, match="expired or does not exist"):
        await backup_transfer._export_chunk(
            hass, "entry-1", "agent-1", {"session_id": "missing", "index": 0}
        )

    session = _empty_export_session(tmp_path)
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
        ({"index": 0}, "session is required"),
        ({"session_id": "x", "index": True}, "index is invalid"),
        ({"session_id": "x", "index": -1}, "index is invalid"),
    ],
)
async def test_export_chunk_rejects_invalid_request_fields(hass, data, match) -> None:
    with pytest.raises(backup.BackupError, match=match):
        await backup_transfer._export_chunk(hass, "entry-1", "agent-1", data)
