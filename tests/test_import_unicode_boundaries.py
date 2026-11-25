"""External Unicode validation and safe admission to HA's native date parser."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    backup_transfer,
    private_backup,
    record_timestamps,
    transfer,
)


def test_malformed_timestamp_is_rejected_in_subprocess():
    script = """
import json
from custom_components.extended_openai_conversation_responses import backup, record_timestamps
bad = chr(0xD800)
assert record_timestamps.parse_datetime(bad) is None
try:
    backup._decode_backup_document(json.dumps({'created_at': bad}), backup.MAX_BACKUP_BYTES)
except backup.BackupError:
    pass
else:
    raise AssertionError('malformed timestamp reached backup inspection')
"""
    run = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        timeout=30,
    )
    assert run.returncode == 0, run.stderr.decode(errors="replace")


async def test_already_decoded_transfer_and_private_yaml_validate_keys_and_values():
    with pytest.raises(backup.BackupError, match="malformed Unicode"):
        await transfer.async_inspect_decoded_transfer({chr(0xDFFF): "text"}, "agent")
    wire = {"encoding": "yaml-safe-v1", "content": r'{"\uD83D\uDE00": "value"}'}
    assert private_backup.decode_field(wire) == {"😀": "value"}
    with pytest.raises(ValueError, match="malformed Unicode"):
        private_backup.decode_field({**wire, "content": r'{key: "\uDFFF"}'})


@pytest.mark.parametrize("valid", [True, False])
def test_archive_rejects_malformed_escapes_without_rejecting_valid_pairs(
    tmp_path, valid
):
    text = r'{"created_at":"\ud83d\ude00"}' if valid else r'{"created_at":"\ud800"}'
    payload = text.encode()
    archive_path = tmp_path / "transfer.zip"
    manifest = {
        "format": backup_transfer.ARCHIVE_FORMAT,
        "version": backup_transfer.ARCHIVE_VERSION,
        "payload": backup_transfer.PAYLOAD_NAME,
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(backup_transfer.PAYLOAD_NAME, payload)
        archive.writestr(backup_transfer.MANIFEST_NAME, json.dumps(manifest))
    if valid:
        assert backup_transfer._load_archive_document(str(archive_path)) == {
            "created_at": "😀"
        }
    else:
        with pytest.raises(backup.BackupError, match="not valid JSON"):
            backup_transfer._load_archive_document(str(archive_path))


def test_native_timestamp_formats_are_unchanged():
    for value in ("2026-11-21T00:12:34Z", "20261121T001234+0000", "2026-11-21 00:12"):
        assert record_timestamps.parse_datetime(
            value
        ) == record_timestamps.dt_util.parse_datetime(value)
