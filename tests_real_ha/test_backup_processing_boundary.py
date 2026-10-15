"""Large uploads retain worker processing and genuine HA validation context."""

import hashlib
import json
import threading
import zipfile

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    backup_transfer,
)
from tests.test_backup import _document


@pytest.mark.parametrize("kind", ["legacy_json", "archive"])
async def test_uploaded_numeric_backup_does_not_reencode_on_loop(
    hass, monkeypatch, tmp_path, kind
):
    document = _document()
    document["agent"]["config"]["future_numeric_payload"] = [0] * 100000
    payload = json.dumps(document, separators=(",", ":")).encode()
    path = tmp_path / ("upload.json" if kind == "legacy_json" else "upload.zip")
    if kind == "legacy_json":
        path.write_bytes(payload)
    else:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(backup_transfer.PAYLOAD_NAME, payload)
            archive.writestr(
                backup_transfer.MANIFEST_NAME,
                json.dumps(
                    {
                        "format": backup_transfer.ARCHIVE_FORMAT,
                        "version": backup_transfer.ARCHIVE_VERSION,
                        "payload": backup_transfer.PAYLOAD_NAME,
                        "payload_bytes": len(payload),
                        "payload_sha256": hashlib.sha256(payload).hexdigest(),
                    }
                ),
            )
    loop_thread = threading.get_ident()
    reader = backup_transfer._load_uploaded_document
    reads = []

    def observed_reader(*args):
        reads.append(threading.get_ident())
        return reader(*args)

    monkeypatch.setattr(backup_transfer, "_load_uploaded_document", observed_reader)

    def no_reencoding(*args, **kwargs):
        raise AssertionError("An already bounded upload must not be reserialized")

    monkeypatch.setattr(backup, "iter_bounded_json", no_reencoding)
    validate = backup._inspect_decoded_backup
    validations = []

    def observed_validation(*args, **kwargs):
        validations.append(threading.get_ident())
        return validate(*args, **kwargs)

    monkeypatch.setattr(backup, "_inspect_decoded_backup", observed_validation)
    prepared = await backup_transfer._async_load_prepared_restore(
        hass, str(path), kind, "target", inspect_only=True
    )
    assert prepared.source_kind == "full_backup"
    assert prepared.title == document["agent"]["title"]
    assert len(reads) == 1 and reads[0] != loop_thread
    assert validations == [loop_thread]
