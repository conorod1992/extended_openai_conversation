"""Backup budgets apply before oversized JSON allocations and publication."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import zipfile

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    backup_transfer,
    bounded_json,
    transfer,
)
from tests.test_backup import _document
from tests.test_transfer import _portable_document


@pytest.mark.parametrize("indent", [None, 2])
@pytest.mark.parametrize(
    "value",
    [
        {"unicode": 'é🙂\n\t"\\\x00', "empty": ""},
        {1: (True, False, None, -1, 1.25), None: {}},
        [[], {}, "x" * 5000, "\n" * 5000],
        {"numbers": [float("nan"), float("inf"), float("-inf")]},
    ],
)
def test_encoding_matches_existing_json_and_exact_utf8_boundary(value, indent):
    expected = json.dumps(
        value,
        ensure_ascii=False,
        indent=indent,
        separators=(",", ":") if indent is None else None,
    ).encode("utf-8")
    assert (
        b"".join(bounded_json.iter_bounded_json(value, len(expected), indent=indent))
        == expected
    )
    with pytest.raises(bounded_json.JSONBudgetExceeded):
        b"".join(
            bounded_json.iter_bounded_json(value, len(expected) - 1, indent=indent)
        )


@pytest.mark.parametrize(
    "scalar",
    ["🙂" * 10000, "\n" * 10000, "x" * 1000000],
    ids=["multibyte", "escaped", "huge"],
)
@pytest.mark.parametrize("as_key", [False, True])
def test_oversized_scalar_never_reaches_whole_token_encoder(
    monkeypatch, scalar, as_key
):
    document = {scalar: 1} if as_key else {"value": scalar}
    encode = Mock(side_effect=AssertionError("Whole-token encoding must not begin"))
    monkeypatch.setattr(json.JSONEncoder, "iterencode", encode)
    # json.dumps for bounded string slices uses JSONEncoder.encode's scalar path.
    with pytest.raises(bounded_json.JSONBudgetExceeded):
        list(bounded_json.iter_bounded_json(document, 8192))
    encode.assert_not_called()


def test_container_overflow_stops_before_later_invalid_value():
    # The string preflight cannot detect numeric/formatting overflow. The stream
    # must enforce the full budget and stop before touching the invalid tail.
    chunks = bounded_json.iter_bounded_json([list(range(1000)), object()], 64)
    published = 0
    with pytest.raises(bounded_json.JSONBudgetExceeded):
        for chunk in chunks:
            published += len(chunk)
    assert published <= 64


def test_numeric_preflight_stops_before_unvisited_tail():
    class Unvisited(list):
        def __iter__(self):
            raise AssertionError(
                "Oversized prefix must stop before traversing the tail"
            )

    with pytest.raises(bounded_json.JSONBudgetExceeded):
        list(bounded_json.iter_bounded_json([[0] * 1000, Unvisited()], 64))


def test_shared_numeric_graph_has_budget_bounded_processing():
    # Forty small distinct lists would expand into over a trillion leaves.
    # This must terminate by budget, including before archive redaction copies it.
    value = [0]
    for _ in range(40):
        value = [value, value]
    with pytest.raises(bounded_json.JSONBudgetExceeded):
        list(bounded_json.iter_bounded_json(value, 64))


@pytest.mark.parametrize("kind", ["legacy", "setup", "archive"])
def test_export_rejects_huge_surviving_key_before_redaction(
    monkeypatch, tmp_path, kind
):
    import tracemalloc

    huge_key = "aA" * 200000
    if kind == "setup":
        document = _portable_document()
        document["sections"][transfer.SECTION_CONFIGURATION][huge_key] = 0
    else:
        document = _document()
        document["agent"]["config"][huge_key] = 0
    monkeypatch.setattr(backup, "MAX_LEGACY_EXPORT_BYTES", 8192)
    monkeypatch.setattr(backup_transfer, "MAX_BACKUP_UNCOMPRESSED_BYTES", 8192)
    tracemalloc.start()
    try:
        with pytest.raises(backup.BackupError):
            if kind == "legacy":
                backup.finalize_backup_snapshot(document)
            elif kind == "setup":
                transfer.finalize_setup_export(document)
            else:
                backup_transfer._build_archive_file(document, str(tmp_path))
        assert tracemalloc.get_traced_memory()[1] < 2 * 1024 * 1024
        assert not list(tmp_path.iterdir())
    finally:
        tracemalloc.stop()


def test_pre_redaction_budget_allows_large_credentials_to_shrink(monkeypatch):
    document = _document()
    document["agent"]["config"]["api_key"] = "private" * 100000
    monkeypatch.setattr(backup, "MAX_LEGACY_EXPORT_BYTES", 16384)
    result = backup.finalize_backup_snapshot(document)
    assert "private" not in result["json"]
    assert len(result["json"].encode()) < 16384


def test_shared_values_count_each_occurrence_and_cycles_are_rejected():
    shared = ["🙂" * 10]
    value = [shared, shared]
    expected = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    assert b"".join(bounded_json.iter_bounded_json(value, len(expected))) == expected
    with pytest.raises(bounded_json.JSONBudgetExceeded):
        list(bounded_json.iter_bounded_json(value, len(expected) // 2))
    shared.append(shared)
    with pytest.raises(ValueError, match="Circular reference"):
        list(bounded_json.iter_bounded_json(shared, 8192))


@pytest.mark.parametrize("limit", [True, 0, -1, 1.5])
def test_invalid_budget_is_rejected(limit):
    with pytest.raises(ValueError, match="positive integer"):
        list(bounded_json.iter_bounded_json({}, limit))


def test_legacy_export_preserves_format_redaction_and_boundary(monkeypatch):
    snapshot = _document()
    snapshot["agent"]["config"]["api_key"] = "private-api-key"
    original = deepcopy(snapshot)
    document = backup_transfer._redacted_document(snapshot)
    expected = json.dumps(document, ensure_ascii=False, indent=2)
    monkeypatch.setattr(backup, "MAX_LEGACY_EXPORT_BYTES", len(expected.encode()))
    result = backup.finalize_backup_snapshot(snapshot)
    assert result["json"] == expected
    assert result["document"] == document
    assert (
        result["filename"] == f"jarvis-full-backup-{snapshot['created_at'][:10]}.json"
    )
    assert "private-api-key" not in result["json"]
    assert snapshot == original
    monkeypatch.setattr(backup, "MAX_LEGACY_EXPORT_BYTES", len(expected.encode()) - 1)
    with pytest.raises(backup.BackupError, match="legacy one-message"):
        backup.finalize_backup_snapshot(snapshot)


def test_legacy_rejects_enormous_scalar_before_full_serialization(monkeypatch):
    snapshot = _document()
    snapshot["padding"] = "x" * 1000000
    monkeypatch.setattr(backup, "MAX_LEGACY_EXPORT_BYTES", 8192)
    encode = Mock(side_effect=AssertionError("Unbounded serialization"))
    monkeypatch.setattr(json.JSONEncoder, "iterencode", encode)
    with pytest.raises(backup.BackupError, match="legacy one-message"):
        backup.finalize_backup_snapshot(snapshot)
    encode.assert_not_called()


@pytest.mark.parametrize("as_text", [False, True])
def test_import_uses_exact_budget_and_keeps_invalid_contract(as_text):
    document = {"value": "🙂\n" * 100}
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    value = encoded if as_text else document
    assert backup._decode_backup_document(value, len(encoded.encode())) == document
    with pytest.raises(backup.BackupError, match="safety limit"):
        backup._decode_backup_document(value, len(encoded.encode()) - 1)
    with pytest.raises(backup.BackupError, match="incomplete or corrupted"):
        backup._decode_backup_document("{" if as_text else {"bad": object()}, 8192)


def test_text_size_check_never_encodes_whole_input():
    class GuardedText(str):
        def encode(self, *args, **kwargs):
            raise AssertionError("Whole input must not be encoded")

    with pytest.raises(backup.BackupError, match="safety limit"):
        backup._decode_backup_document(GuardedText("🙂" * 1000000), 8192)


def test_object_import_rejects_huge_scalar_before_encoding(monkeypatch):
    encode = Mock(side_effect=AssertionError("Unbounded import serialization"))
    monkeypatch.setattr(json.JSONEncoder, "iterencode", encode)
    with pytest.raises(backup.BackupError, match="safety limit"):
        backup._decode_backup_document({"padding": "\n" * 1000000}, 8192)
    encode.assert_not_called()


def test_archive_rejects_huge_scalar_before_encoding_and_cleans_file(
    monkeypatch, tmp_path
):
    snapshot = _document()
    snapshot["padding"] = "\n" * 1000000
    monkeypatch.setattr(backup_transfer, "MAX_BACKUP_UNCOMPRESSED_BYTES", 8192)
    encode = Mock(side_effect=AssertionError("Unbounded archive serialization"))
    monkeypatch.setattr(json.JSONEncoder, "iterencode", encode)
    with pytest.raises(backup.BackupError, match="uncompressed safety limit"):
        backup_transfer._build_archive_file(snapshot, str(tmp_path))
    encode.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("custom", [False, True])
def test_archive_preserves_payload_integrity_and_independent_limit(
    monkeypatch, tmp_path, custom
):
    snapshot = _portable_document() if custom else _document()
    original = deepcopy(snapshot)
    document = backup_transfer._redacted_document(snapshot)
    expected = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    monkeypatch.setattr(backup, "MAX_LEGACY_EXPORT_BYTES", 1)
    monkeypatch.setattr(backup_transfer, "MAX_BACKUP_UNCOMPRESSED_BYTES", len(expected))
    result = backup_transfer._build_archive_file(snapshot, str(tmp_path))
    try:
        with zipfile.ZipFile(result["path"]) as archive:
            payload = archive.read(backup_transfer.PAYLOAD_NAME)
            manifest = json.loads(archive.read(backup_transfer.MANIFEST_NAME))
        assert payload == expected
        assert manifest["payload_bytes"] == result["uncompressed_size"] == len(payload)
        assert manifest["payload_sha256"] == hashlib.sha256(payload).hexdigest()
        assert (
            result["sha256"]
            == hashlib.sha256(Path(result["path"]).read_bytes()).hexdigest()
        )
        assert snapshot == original
    finally:
        Path(result["path"]).unlink()


@pytest.mark.parametrize("huge_scalar", [False, True])
def test_archive_limit_failure_removes_partial_file(monkeypatch, tmp_path, huge_scalar):
    snapshot = _document()
    snapshot["padding"] = "\n" * 1000000 if huge_scalar else list(range(10000))
    monkeypatch.setattr(backup_transfer, "MAX_BACKUP_UNCOMPRESSED_BYTES", 8192)
    with pytest.raises(backup.BackupError, match="uncompressed safety limit"):
        backup_transfer._build_archive_file(snapshot, str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_setup_export_preserves_format_redaction_and_boundary(monkeypatch):
    snapshot = _portable_document(mode="setup")
    original = deepcopy(snapshot)
    document = transfer.redact_transfer_document(snapshot)
    expected = json.dumps(document, ensure_ascii=False, indent=2)
    monkeypatch.setattr(backup, "MAX_LEGACY_EXPORT_BYTES", len(expected.encode()))
    result = transfer.finalize_setup_export(snapshot)
    assert result["json"] == expected
    assert result["document"] == document
    assert result["filename"] == "coverage-agent-shareable-setup-2026-09-13.json"
    assert result["mode"] == "setup"
    assert snapshot == original
    monkeypatch.setattr(backup, "MAX_LEGACY_EXPORT_BYTES", len(expected.encode()) - 1)
    with pytest.raises(backup.BackupError, match="16 MB safety limit"):
        transfer.finalize_setup_export(snapshot)


def test_setup_rejects_huge_scalar_before_encoding(monkeypatch):
    snapshot = _portable_document(mode="setup")
    snapshot["padding"] = "x" * 1000000
    monkeypatch.setattr(backup, "MAX_LEGACY_EXPORT_BYTES", 8192)
    encode = Mock(side_effect=AssertionError("Unbounded setup serialization"))
    monkeypatch.setattr(json.JSONEncoder, "iterencode", encode)
    with pytest.raises(backup.BackupError, match="16 MB safety limit"):
        transfer.finalize_setup_export(snapshot)
    encode.assert_not_called()


async def test_async_setup_serialization_runs_in_worker(hass, monkeypatch):
    snapshot = _portable_document(mode="setup")
    event_loop_thread = threading.get_ident()
    finalize = transfer.finalize_setup_export
    worker_threads = []

    def checked_finalize(value):
        worker_threads.append(threading.get_ident())
        assert threading.get_ident() != event_loop_thread
        return finalize(value)

    monkeypatch.setattr(
        transfer, "async_collect_transfer_snapshot", AsyncMock(return_value=snapshot)
    )
    monkeypatch.setattr(transfer, "finalize_setup_export", checked_finalize)
    result = await transfer.async_create_setup_export(
        hass, SimpleNamespace(entry_id="entry"), SimpleNamespace(subentry_id="agent")
    )
    assert worker_threads
    assert json.loads(result["json"]) == transfer.redact_transfer_document(snapshot)


def test_transfer_text_size_check_never_encodes_whole_input(monkeypatch):
    class GuardedText(str):
        def encode(self, *args, **kwargs):
            raise AssertionError("Whole input must not be encoded")

    monkeypatch.setattr(backup, "MAX_BACKUP_BYTES", 8192)
    with pytest.raises(backup.BackupError, match="128 MB safety limit"):
        transfer.inspect_transfer(GuardedText("🙂" * 1000000), "agent")
