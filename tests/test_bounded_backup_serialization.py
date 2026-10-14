"""Backup budgets apply before oversized JSON allocations and publication."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock
import zipfile

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    backup_transfer,
    bounded_json,
)
from tests.test_backup import _document


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
    assert 0 < published <= 64


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


def test_archive_preserves_payload_integrity_and_independent_limit(
    monkeypatch, tmp_path
):
    snapshot = _document()
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
