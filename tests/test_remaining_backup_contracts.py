"""Backup transfer races must reject cancelled sessions and stale previews."""

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    backup_transfer as bt,
    transfer,
)


@dataclass
class _Snapshot:
    config: dict


def _import(hass, tmp_path):
    path = tmp_path / "backup.json"
    path.write_bytes(b"{}")
    session = bt.ImportSession(
        "session",
        "entry",
        "agent",
        str(path),
        "backup.json",
        2,
        "legacy_json",
        10**12,
        received=2,
        preview_token="preview",
        preview_revision=bt._snapshot_revision(_Snapshot({"prompt": "original"})),
        preview_sections=(transfer.SECTION_CONFIGURATION,),
        preview_user_scope_mappings=(),
    )
    bt._imports(hass)[session.session_id] = session
    bt._latest_previews(hass)[("entry", "agent")] = (session.session_id, "preview")
    return session


@pytest.mark.parametrize("operation", ["export", "upload", "restore"])
async def test_transfer_cancellation_while_waiting_for_session_lock(
    hass, tmp_path, monkeypatch, operation
):
    if operation == "export":
        path = tmp_path / "export.zip"
        path.write_bytes(b"ab")
        session = bt.ExportSession(
            "session", "entry", "agent", str(path), "export.zip", 2, "0" * 64, 10**12
        )
        registry = bt._exports(hass)
        registry["session"] = session

        async def run():
            return await bt._export_chunk(
                hass, "entry", "agent", {"session_id": "session", "index": 0}
            )
    else:
        session = _import(hass, tmp_path)
        registry = bt._imports(hass)
        if operation == "upload":
            session.received = 0

            async def run():
                return await bt._import_chunk(
                    hass,
                    "entry",
                    "agent",
                    {"session_id": "session", "index": 0, "data": "e30="},
                )
        else:

            async def run():
                return await bt._restore_import(
                    hass,
                    SimpleNamespace(entry_id="entry"),
                    SimpleNamespace(subentry_id="agent"),
                    {"session_id": "session", "preview_token": "preview"},
                )

    reached = asyncio.Event()
    original = bt._require_session_identity

    def identity(*args):
        original(*args)
        reached.set()

    monkeypatch.setattr(bt, "_require_session_identity", identity)
    load = AsyncMock()
    monkeypatch.setattr(bt, "_async_load_prepared_restore", load)
    await session.lock.acquire()
    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(reached.wait(), 2)
        registry.pop("session")
    finally:
        session.lock.release()
    with pytest.raises(backup.BackupError, match="expired or was cancelled"):
        await task
    load.assert_not_awaited()
    assert (
        tmp_path / ("export.zip" if operation == "export" else "backup.json")
    ).read_bytes() == (b"ab" if operation == "export" else b"{}")


async def test_export_preserves_bounded_read_error(hass, tmp_path, monkeypatch):
    session = bt.ExportSession(
        "session",
        "entry",
        "agent",
        str(tmp_path / "export.zip"),
        "export.zip",
        2,
        "0" * 64,
        10**12,
    )
    bt._exports(hass)["session"] = session
    error = backup.BackupError("staged archive changed during read")
    monkeypatch.setattr(bt, "_read_file_chunk_base64", Mock(side_effect=error))
    with pytest.raises(backup.BackupError) as caught:
        await bt._export_chunk(
            hass, "entry", "agent", {"session_id": "session", "index": 0}
        )
    assert caught.value is error


@pytest.mark.parametrize(
    "change,message",
    [
        ("sections", "restore sections changed"),
        ("section-type", "restore sections changed"),
        ("cancel-before-validation", "expired or was cancelled"),
        ("token", "preview is stale"),
        ("revision", "preview is stale"),
        ("latest", "preview is stale"),
        ("target", "target changed after preview"),
        ("cancel-during-snapshot", "expired or was cancelled"),
    ],
)
async def test_restore_revalidates_preview_before_writing(
    hass, tmp_path, monkeypatch, change, message
):
    session = _import(hass, tmp_path)
    prepared = object()
    monkeypatch.setattr(
        bt, "_async_load_prepared_restore", AsyncMock(return_value=prepared)
    )
    monkeypatch.setattr(transfer, "apply_user_scope_mappings", lambda value, *_: value)

    async def snapshot(*_):
        if change == "cancel-during-snapshot":
            bt._imports(hass).pop("session")
        return _Snapshot({"prompt": "changed" if change == "target" else "original"})

    monkeypatch.setattr(transfer, "_current_snapshot", snapshot)
    writes = Mock()

    async def restore(*_args, precondition, **_kwargs):
        if change == "cancel-before-validation":
            bt._imports(hass).pop("session")
        elif change == "token":
            session.preview_token = "new"
        elif change == "revision":
            session.preview_revision = None
        elif change == "latest":
            bt._latest_previews(hass)[("entry", "agent")] = (
                "new-session",
                "new-preview",
            )
        await precondition()
        writes()

    monkeypatch.setattr(transfer, "async_restore_transfer", restore)
    data = {"session_id": "session", "preview_token": "preview"}
    if change == "sections":
        data["sections"] = [transfer.SECTION_PERSISTENT_MEMORY]
    if change == "section-type":
        data["sections"] = [1]
    with pytest.raises(backup.BackupError, match=message):
        await bt._restore_import(
            hass,
            SimpleNamespace(entry_id="entry"),
            SimpleNamespace(subentry_id="agent"),
            data,
        )
    writes.assert_not_called()
    if change.startswith("cancel-"):
        assert not (tmp_path / "backup.json").exists()
    else:
        assert (tmp_path / "backup.json").read_bytes() == b"{}"


@pytest.mark.parametrize("missing_mapping", [False, True])
async def test_cancelled_import_cannot_publish_new_preview(
    hass, tmp_path, monkeypatch, missing_mapping
):
    session = _import(hass, tmp_path)
    prepared = SimpleNamespace(
        available_sections=(transfer.SECTION_CONFIGURATION,),
        summary=lambda: {"summary": "prepared"},
    )
    entry, subentry = (
        SimpleNamespace(entry_id="entry"),
        SimpleNamespace(subentry_id="agent"),
    )
    monkeypatch.setattr(bt, "_resolve_agent", lambda *_: (entry, subentry))
    monkeypatch.setattr(
        bt, "_async_load_prepared_restore", AsyncMock(return_value=prepared)
    )
    monkeypatch.setattr(
        transfer,
        "async_user_scope_mapping_plan",
        AsyncMock(
            return_value={
                "missing_source_user_ids": ["missing"] if missing_mapping else [],
                "resolved": {},
            }
        ),
    )
    monkeypatch.setattr(transfer, "apply_user_scope_mappings", lambda *_: prepared)
    monkeypatch.setattr(
        transfer,
        "async_materialize_restore",
        AsyncMock(
            return_value=(
                object(),
                {"selected_sections": [transfer.SECTION_CONFIGURATION]},
            )
        ),
    )

    async def snapshot(*_):
        bt._imports(hass).pop("session")
        return _Snapshot({"prompt": "original"})

    monkeypatch.setattr(transfer, "_current_snapshot", snapshot)
    with pytest.raises(backup.BackupError, match="expired or was cancelled"):
        await bt._inspect_import(hass, "entry", "agent", {"session_id": "session"})
    assert session.preview_token == "preview"
    assert bt._latest_previews(hass)[("entry", "agent")] == ("session", "preview")
