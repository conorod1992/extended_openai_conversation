"""Backup structure is bounded before constructing or copying imported data."""

from __future__ import annotations

import asyncio
import io
import json
import math
from types import SimpleNamespace
from weakref import ref
import zipfile

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    backup_input,
    backup_transfer,
    transfer,
)
from tests.test_backup_transfer import _write_archive
from tests.test_backup_transfer_lifecycle import _completed_import_session


@pytest.mark.parametrize(
    "payload",
    [
        '{"key":"ASCII, colon: escaped quote \\" and literal \\\\u1234"}',
        '{"key":"\\u0100\\ud83d\\ude00","value":[-0.125e+03,true,null]}',
        '{"key":"é Ā 书 😀","count":123,"next":4}',
    ],
)
def test_input_budget_retains_json_token_boundaries_between_reads(payload):
    wire = payload.encode()
    baseline = backup_input.JSONInputBudget()
    baseline.feed(wire)
    for width in range(1, len(wire)):
        divided = backup_input.JSONInputBudget()
        for offset in range(0, len(wire), width):
            divided.feed(wire[offset : offset + width])
        assert divided.remaining == baseline.remaining
        assert divided.depth == 0


def test_stream_never_forwards_a_chunk_that_completes_an_oversized_token(monkeypatch):
    monkeypatch.setattr(backup_input, "MAX_TOKEN_BYTES", 4)
    reader = backup_transfer._ArchivePayloadReader(io.BytesIO(b'{"x":"short text"}'))

    assert reader.read(8) == b'{"x":"sh'
    with pytest.raises(backup.BackupError, match="oversized JSON token"):
        reader.read(4)


def test_stream_accounts_for_widening_a_single_unicode_token(monkeypatch):
    monkeypatch.setattr(backup_input, "MAX_TOKEN_BYTES", 14)
    reader = backup_transfer._ArchivePayloadReader(
        io.BytesIO('{"x":"abcd😀"}'.encode())
    )

    with pytest.raises(backup.BackupError, match="oversized JSON token"):
        reader.read(1024)


def test_decoded_tree_checks_depth_before_copy_or_schema_validation(monkeypatch):
    monkeypatch.setattr(backup_input, "MAX_DEPTH", 3)
    document = {"data": [[[]]]}

    with pytest.raises(backup.BackupError, match="nesting safety limit"):
        backup._decode_backup_document(document, backup.MAX_BACKUP_BYTES)
    with pytest.raises(backup.BackupError, match="nesting safety limit"):
        transfer._decode_transfer_input(document)


def test_serialized_legacy_and_direct_documents_reject_before_json_loads(
    tmp_path, monkeypatch
):
    path = tmp_path / "legacy.json"
    source = '{"data":' + json.dumps([{}] * 12) + "}"
    path.write_bytes(source.encode())
    monkeypatch.setattr(backup_input, "MAX_DECODED_BYTES", 500)

    def should_not_decode(*_args, **_kwargs):
        pytest.fail("JSON construction must be preceded by structural admission")

    monkeypatch.setattr(json, "loads", should_not_decode)

    with pytest.raises(backup.BackupError, match="decoded data safety limit"):
        backup_transfer._load_legacy_json_document(str(path))
    with pytest.raises(backup.BackupError, match="decoded data safety limit"):
        backup._decode_backup_document(source, backup.MAX_BACKUP_BYTES)
    with pytest.raises(backup.BackupError, match="decoded data safety limit"):
        transfer._decode_transfer_input(source)


def test_archive_applies_budget_before_building_payload_events(tmp_path, monkeypatch):
    path = tmp_path / "data.zip"
    _write_archive(path, json.dumps({"items": [{} for _ in range(30)]}).encode())
    monkeypatch.setattr(backup_input, "MAX_DECODED_BYTES", 1800)

    class UnusedBuilder:
        def event(self, _event, _value):
            pytest.fail("Over-budget JSON chunk must never reach the builder")

    monkeypatch.setattr(backup_transfer, "ObjectBuilder", UnusedBuilder)
    with pytest.raises(backup.BackupError, match="decoded data safety limit"):
        backup_transfer._load_archive_document(str(path))


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-32-be"])
def test_legacy_json_preserves_stdlib_encoding_and_number_semantics(tmp_path, encoding):
    path = tmp_path / "legacy.json"
    source = (
        '{"value":"Ā📚","integer":1208925819614629174706177,"float":1e-6,"nan":NaN}'
    )
    path.write_bytes(source.encode(encoding))

    result = backup_transfer._load_legacy_json_document(str(path))
    assert result["value"] == "Ā📚"
    assert result["integer"] == 2**80 + 1
    assert result["float"] == 1e-6
    assert math.isnan(result["nan"])


def test_legacy_json_bounds_allocating_the_whole_decoded_source(tmp_path, monkeypatch):
    path = tmp_path / "legacy.json"
    payload = json.dumps({"left": "a" * 30, "right": "😀"}, ensure_ascii=False).encode()
    path.write_bytes(payload)
    monkeypatch.setattr(backup_transfer, "MAX_LEGACY_JSON_IMPORT_BYTES", len(payload))

    with pytest.raises(backup.BackupError, match="source text exceeds"):
        backup_transfer._load_legacy_json_document(str(path))


@pytest.mark.parametrize("limit", ["span", "actual entries"])
def test_archive_index_is_admitted_before_constructing_zipfile(
    tmp_path, monkeypatch, limit
):
    path = tmp_path / "data.zip"
    _write_archive(path, b"{}", extra=("other", b"x"))
    if limit == "span":
        monkeypatch.setattr(backup_transfer, "_MAX_CENTRAL_DIRECTORY_BYTES", 2)
        message = "central directory exceeds"
    else:
        read_end_record = zipfile._EndRecData

        def undercount_actual_entries(archive_file):
            record = read_end_record(archive_file)
            record[zipfile._ECD_ENTRIES_TOTAL] = 2
            record[zipfile._ECD_ENTRIES_THIS_DISK] = 2
            return record

        monkeypatch.setattr(zipfile, "_EndRecData", undercount_actual_entries)
        message = "unexpected files"

    def should_not_construct(*_args, **_kwargs):
        pytest.fail("ZipFile must follow index admission")

    monkeypatch.setattr(zipfile, "ZipFile", should_not_construct)
    with pytest.raises(backup.BackupError, match=message):
        backup_transfer._load_archive_document(str(path))


async def test_backup_import_admission_retains_detached_preparation_slot():
    hass = SimpleNamespace(data={})

    preparation = asyncio.get_running_loop().create_future()
    async with backup.async_backup_import_admission(hass) as permit:
        permit.retain_until(preparation)

    with pytest.raises(backup.BackupError, match="retry shortly"):
        async with backup.async_backup_import_admission(hass):
            pytest.fail("A detached worker still owns the sole permit")

    preparation.set_result(None)
    await asyncio.sleep(0)
    async with backup.async_backup_import_admission(hass):
        pass


async def test_failed_timed_out_inspection_keeps_error_without_decoded_locals(
    hass, tmp_path, monkeypatch
):
    path = tmp_path / "upload.json"
    path.write_bytes(b"{}")
    session = _completed_import_session(str(path))
    backup_transfer._imports(hass)[session.session_id] = session
    release = asyncio.Event()
    observed = None

    class DecodedTree:
        pass

    async def fail_inspection(*_args):
        nonlocal observed
        tree = DecodedTree()
        observed = ref(tree)
        await release.wait()
        raise backup.BackupError("Unable to inspect the backup")

    monkeypatch.setattr(backup_transfer, "_inspect_import_admitted", fail_inspection)
    monkeypatch.setattr(backup_transfer, "IMPORT_INSPECTION_TIMEOUT_SECONDS", 0)
    data = {"session_id": session.session_id}
    try:
        with pytest.raises(backup.BackupError, match="inspection is still running"):
            await backup_transfer._inspect_import(
                hass, session.entry_id, session.subentry_id, data
            )
        inspection = session.inspection
        assert inspection is not None
        assert observed is not None and observed() is not None
        release.set()
        await asyncio.wait({inspection})
        assert observed() is None
        with pytest.raises(backup.BackupError, match="Unable to inspect the backup"):
            await backup_transfer._inspect_import(
                hass, session.entry_id, session.subentry_id, data
            )
    finally:
        release.set()
        await backup_transfer._discard_import(hass, session.session_id)
