"""Regression tests for bounded chunked full-backup transfer."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import stat
from types import SimpleNamespace
import zipfile

import pytest

from custom_components.extended_openai_conversation_responses import agent_maintenance
from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses import backup_transfer
from tests.test_backup import _document


def _archive_manifest(payload: bytes) -> dict:
    return {
        "format": backup_transfer.ARCHIVE_FORMAT,
        "version": backup_transfer.ARCHIVE_VERSION,
        "payload": backup_transfer.PAYLOAD_NAME,
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write_archive(path, payload: bytes, *, payload_info=None, manifest=None, extra=None):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if payload_info is None:
            archive.writestr(backup_transfer.PAYLOAD_NAME, payload)
        else:
            archive.writestr(payload_info, payload)
        archive.writestr(
            backup_transfer.MANIFEST_NAME,
            json.dumps(manifest if manifest is not None else _archive_manifest(payload)),
        )
        if extra is not None:
            archive.writestr(extra[0], extra[1])


def test_archive_round_trip_uses_normal_backup_validation() -> None:
    result = backup_transfer._build_archive_file(_document())
    try:
        prepared = backup_transfer._load_prepared_restore(
            result["path"], "archive", "agent-new"
        )
    finally:
        backup_transfer._remove_file(result["path"])

    assert prepared.title == "Agent"
    assert prepared.summary()["persistent_memories"] == 1
    assert prepared.summary()["usage_requests"] == 1


def test_legacy_json_import_remains_supported(tmp_path) -> None:
    path = tmp_path / "old-full-backup.json"
    path.write_text(json.dumps(_document()), encoding="utf-8")

    prepared = backup_transfer._load_prepared_restore(
        str(path), "legacy_json", "agent-new"
    )

    assert prepared.title == "Agent"
    assert prepared.summary()["archive_sessions"] == 1


@pytest.mark.parametrize("attack", ["traversal", "symlink", "extra_member"])
def test_archive_rejects_unsafe_members(tmp_path, attack) -> None:
    path = tmp_path / "unsafe.zip"
    payload = json.dumps(_document()).encode()
    if attack == "traversal":
        _write_archive(path, payload, payload_info="../backup.json")
    elif attack == "symlink":
        info = zipfile.ZipInfo(backup_transfer.PAYLOAD_NAME)
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        _write_archive(path, payload, payload_info=info)
    else:
        _write_archive(path, payload, extra=("extra.txt", b"unexpected"))

    with pytest.raises(backup.BackupError, match="unexpected|unsafe"):
        backup_transfer._load_archive_document(str(path))


def test_archive_rejects_invalid_manifest(tmp_path) -> None:
    path = tmp_path / "bad-manifest.zip"
    payload = json.dumps(_document()).encode()
    _write_archive(path, payload, manifest={"format": "wrong"})

    with pytest.raises(backup.BackupError, match="manifest"):
        backup_transfer._load_archive_document(str(path))


def test_archive_rejects_uncompressed_bomb_before_extraction(tmp_path, monkeypatch) -> None:
    path = tmp_path / "bomb.zip"
    payload = b"x" * 1024
    _write_archive(path, payload)
    monkeypatch.setattr(backup_transfer, "MAX_BACKUP_UNCOMPRESSED_BYTES", 64)

    with pytest.raises(backup.BackupError, match="uncompressed"):
        backup_transfer._load_archive_document(str(path))


async def test_import_requires_ordered_complete_chunks(hass, monkeypatch) -> None:
    monkeypatch.setattr(backup_transfer, "BACKUP_CHUNK_BYTES", 4)
    session = await backup_transfer._start_import(
        hass,
        "entry-1",
        "agent-1",
        {"filename": "backup.json", "size": 8},
    )
    session_id = session["session_id"]
    try:
        first = await backup_transfer._import_chunk(
            hass,
            "entry-1",
            "agent-1",
            {"session_id": session_id, "index": 0, "data": base64.b64encode(b"abcd").decode()},
        )
        assert first["received"] == 4
        assert first["complete"] is False

        with pytest.raises(backup.BackupError, match="Expected backup chunk 1"):
            await backup_transfer._import_chunk(
                hass,
                "entry-1",
                "agent-1",
                {"session_id": session_id, "index": 2, "data": base64.b64encode(b"efgh").decode()},
            )
        with pytest.raises(backup.BackupError, match="incomplete"):
            backup_transfer._completed_import(
                hass, session_id, "entry-1", "agent-1"
            )

        second = await backup_transfer._import_chunk(
            hass,
            "entry-1",
            "agent-1",
            {"session_id": session_id, "index": 1, "data": base64.b64encode(b"efgh").decode()},
        )
        assert second["received"] == 8
        assert second["complete"] is True
        assert os.path.getsize(backup_transfer._imports(hass)[session_id].path) == 8
    finally:
        await backup_transfer._discard_import(hass, session_id)


async def test_import_rejects_quota_before_allocating_file(hass, monkeypatch) -> None:
    monkeypatch.setattr(backup_transfer, "MAX_TRANSFER_TEMP_BYTES", 4)

    with pytest.raises(backup.BackupError, match="temporary storage quota"):
        await backup_transfer._start_import(
            hass,
            "entry-1",
            "agent-1",
            {"filename": "backup.json", "size": 5},
        )

    assert backup_transfer._imports(hass) == {}


async def test_cancelled_import_chunk_discards_partial_session(hass, monkeypatch) -> None:
    session = await backup_transfer._start_import(
        hass,
        "entry-1",
        "agent-1",
        {"filename": "backup.json", "size": 4},
    )
    session_id = session["session_id"]
    path = backup_transfer._imports(hass)[session_id].path

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(backup_transfer, "_async_append_file", cancelled)

    with pytest.raises(asyncio.CancelledError):
        await backup_transfer._import_chunk(
            hass,
            "entry-1",
            "agent-1",
            {"session_id": session_id, "index": 0, "data": base64.b64encode(b"abcd").decode()},
        )

    assert session_id not in backup_transfer._imports(hass)
    assert not os.path.exists(path)


async def test_export_releases_snapshot_gate_before_compression(
    hass, monkeypatch, tmp_path
) -> None:
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")
    gate = agent_maintenance.get_agent_maintenance_gate(
        hass, entry.entry_id, subentry.subentry_id
    )
    collection_started = asyncio.Event()
    finish_collection = asyncio.Event()
    shared_entered = asyncio.Event()
    compression_started = asyncio.Event()
    archive_path = tmp_path / "export.zip"
    archive_path.write_bytes(b"archive")

    async def collect(*_args, **_kwargs):
        assert gate._writer_active is True
        collection_started.set()
        await finish_collection.wait()
        return _document()

    async def build(_hass, _snapshot):
        assert gate._writer_active is False
        compression_started.set()
        return {
            "path": str(archive_path),
            "filename": "agent.zip",
            "size": archive_path.stat().st_size,
            "sha256": "0" * 64,
            "uncompressed_size": 100,
        }

    monkeypatch.setattr(backup, "async_collect_backup_snapshot", collect)
    monkeypatch.setattr(backup_transfer, "_async_build_archive_file", build)

    async def ordinary_mutation() -> None:
        async with gate.shared():
            shared_entered.set()

    export_task = asyncio.create_task(backup_transfer._start_export(hass, entry, subentry))
    await collection_started.wait()
    mutation_task = asyncio.create_task(ordinary_mutation())
    await asyncio.sleep(0)
    assert not shared_entered.is_set()

    finish_collection.set()
    result = await export_task
    await mutation_task

    assert compression_started.is_set()
    assert shared_entered.is_set()
    assert result["chunk_count"] == 1
    await backup_transfer._discard_export(hass, result["session_id"])
