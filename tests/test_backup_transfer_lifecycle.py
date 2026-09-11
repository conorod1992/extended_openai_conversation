"""Lifecycle coverage for bounded full-backup transfers."""

from __future__ import annotations

import base64
import hashlib
import os
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses import backup_transfer
from custom_components.extended_openai_conversation_responses import transfer


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

    async def materialize(_hass, actual_entry, actual_subentry, actual_prepared, *, sections):
        assert actual_entry is entry
        assert actual_subentry is subentry
        assert actual_prepared is prepared
        assert sections == ["memory"]
        return object(), {"changes": 1}

    async def restore(_hass, actual_entry, actual_subentry, actual_prepared, *, sections):
        assert actual_entry is entry
        assert actual_subentry is subentry
        assert actual_prepared is prepared
        assert sections == ["memory"]
        return {"restored": True}

    monkeypatch.setattr(backup_transfer, "_async_load_prepared_restore", load_prepared)
    monkeypatch.setattr(backup_transfer, "_resolve_agent", lambda *_args: (entry, subentry))
    monkeypatch.setattr(transfer, "async_materialize_restore", materialize)
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
    assert inspection == {"prepared": True, "preview": {"changes": 1}}
    assert session_id in backup_transfer._imports(hass)
    assert os.path.exists(staged_path)

    restored = await backup_transfer._restore_import(
        hass,
        entry,
        subentry,
        {"session_id": session_id, "sections": ["memory"]},
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

    async def fail_restore(*_args, **_kwargs):
        raise RuntimeError("restore failed")

    monkeypatch.setattr(backup_transfer, "_async_load_prepared_restore", load_prepared)
    monkeypatch.setattr(transfer, "async_restore_transfer", fail_restore)
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")

    with pytest.raises(RuntimeError, match="restore failed"):
        await backup_transfer._restore_import(
            hass,
            entry,
            subentry,
            {"session_id": session_id},
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
