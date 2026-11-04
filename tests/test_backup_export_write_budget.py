"""The ZIP staging file stays inside the quota throughout compression and close."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    backup_transfer as transfer,
)
from tests.test_backup import _document


def _observe_writes(monkeypatch):
    sizes = []

    class RecordingFile(transfer._BoundedArchiveFile):
        def write(self, data):
            result = super().write(data)
            self.flush()
            sizes.append(os.fstat(self.fileno()).st_size)
            return result

    monkeypatch.setattr(transfer, "_BoundedArchiveFile", RecordingFile)
    return sizes


async def test_incompressible_export_never_exceeds_its_reservation(
    hass, monkeypatch, tmp_path
):
    budget = 16 * 1024
    monkeypatch.setattr(transfer, "MAX_BACKUP_ARCHIVE_BYTES", budget)
    monkeypatch.setattr(transfer, "MAX_TRANSFER_TEMP_BYTES", 4 * budget)
    monkeypatch.setattr(transfer, "MAX_BACKUP_UNCOMPRESSED_BYTES", 8 * budget)
    sizes = _observe_writes(monkeypatch)
    snapshot = _document()
    snapshot["knowledge"]["sources"][0]["content"] = os.urandom(48 * 1024).hex()
    monkeypatch.setattr(
        backup, "async_collect_backup_snapshot", AsyncMock(return_value=snapshot)
    )

    async def build(_hass, document):
        return transfer._build_archive_file(document, str(tmp_path))

    monkeypatch.setattr(transfer, "_async_build_archive_file", build)
    imports = []
    try:
        for index in range(3):
            imports.append(
                await transfer._start_import(
                    hass,
                    "entry",
                    "agent",
                    {"filename": f"import-{index}.zip", "size": budget},
                )
            )
        assert transfer._temporary_bytes(hass) == 3 * budget
        with pytest.raises(backup.BackupError, match="compressed safety limit"):
            await transfer._start_export(
                hass,
                SimpleNamespace(entry_id="entry"),
                SimpleNamespace(subentry_id="agent"),
            )
        assert sizes and max(sizes) <= budget
        assert 3 * budget + max(sizes) <= transfer.MAX_TRANSFER_TEMP_BYTES
        assert list(tmp_path.glob("extended-openai-backup-*.zip")) == []
        assert not transfer._exports(hass)
    finally:
        for item in imports:
            await transfer._discard_import(hass, item["session_id"])


@pytest.mark.parametrize("delta", [0, -1, -50, -200])
def test_zip_headers_and_directory_obey_exact_ceiling(monkeypatch, tmp_path, delta):
    snapshot = _document()
    initial = transfer._build_archive_file(snapshot, str(tmp_path))
    size = initial["size"]
    transfer._remove_file(initial["path"])
    monkeypatch.setattr(transfer, "MAX_BACKUP_ARCHIVE_BYTES", size + delta)
    sizes = _observe_writes(monkeypatch)
    if delta == 0:
        result = transfer._build_archive_file(snapshot, str(tmp_path))
        assert result["size"] == size
        transfer._remove_file(result["path"])
    else:
        with pytest.raises(backup.BackupError, match="compressed safety limit"):
            transfer._build_archive_file(snapshot, str(tmp_path))
    assert max(sizes) <= size + delta
    assert list(tmp_path.glob("extended-openai-backup-*.zip")) == []
