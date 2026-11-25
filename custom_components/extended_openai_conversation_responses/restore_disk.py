"""Bound scratch memory when writing restore-owned private Store files."""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
from typing import IO, Any

import ijson
from ijson.backends import python as ijson_python
from ijson.common import JSONError, ObjectBuilder

from homeassistant.helpers.storage import Store

from .strict_json import has_surrogate

ARCHIVE_DISK_THRESHOLD = 16 * 1024 * 1024
_SURROGATE_ESCAPE = re.compile(rb"\\u[dD][89a-fA-F][0-9a-fA-F]{2}")
RESTORE_STAGING_SUFFIX = ".restore-staging"


def large_archive_document(document: dict[str, Any]) -> bool:
    """Detect a large Archive in either generation of the full restore journal."""
    size = 0
    for side in ("target", "rollback"):
        generation = document.get(side)
        archive = generation.get("archive") if isinstance(generation, dict) else None
        if not isinstance(archive, dict) or not isinstance(archive.get("turns"), list):
            continue
        sessions = archive.get("sessions")
        if isinstance(sessions, list):
            size += 256 * len(sessions)
        size += 128 * len(archive["turns"])
        if size >= ARCHIVE_DISK_THRESHOLD:
            return True
        for turn in archive["turns"]:
            if not isinstance(turn, dict):
                continue
            for key in ("user_text", "assistant_text"):
                value = turn.get(key)
                if not isinstance(value, str):
                    continue
                size += len(value) if value.isascii() else 4 * len(value)
                if size >= ARCHIVE_DISK_THRESHOLD:
                    return True
    return False


def _check_json_round_trip(value: Any) -> None:
    """Reject types that Store read-back would otherwise change silently."""
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("Restore data contains a non-string JSON key")
            _check_json_round_trip(child)
    elif isinstance(value, list):
        for child in value:
            _check_json_round_trip(child)
    elif value is not None and not isinstance(value, (str, int, float)):
        raise ValueError("Restore data contains a value which cannot round-trip as JSON")


def read_large_journal(stream: IO[bytes]) -> Any:
    """Decode a recovery journal without first allocating a full source string."""
    return _read_journal_events(stream, _contains_surrogate_escape(stream))


def _contains_surrogate_escape(stream: IO[bytes]) -> bool:
    """Select the preserving parser for escapes YAJL can silently repair."""
    tail = b""
    try:
        while chunk := stream.read(256 * 1024):
            if _SURROGATE_ESCAPE.search(chunk) or _SURROGATE_ESCAPE.search(tail + chunk[:5]):
                return True
            tail = (tail + chunk)[-5:] if len(chunk) < 5 else chunk[-5:]
        return False
    finally:
        stream.seek(0)


def _read_journal_events(stream: IO[bytes], preserve_escapes: bool) -> Any:
    builder = ObjectBuilder()
    try:
        events = (
            ijson_python.basic_parse(stream, use_float=False)
            if preserve_escapes
            else ijson.basic_parse(stream, use_float=False)
        )
        for event, value in events:
            if event in {"map_key", "string"} and has_surrogate(value):
                raise ValueError("Invalid Unicode in restore journal")
            builder.event(event, float(value) if isinstance(value, Decimal) else value)
    except JSONError:
        raise ValueError("Invalid JSON in restore journal") from None
    return getattr(builder, "value", None)


def remove_restore_staging(path: Path) -> None:
    """Discard the unpublished generation of an exclusively owned file."""
    path.with_name(path.name + RESTORE_STAGING_SUFFIX).unlink(missing_ok=True)


def write_verified_store(
    store: Store[dict[str, Any]], data: dict[str, Any], *, max_bytes: int
) -> None:
    """Write the native Store envelope incrementally and verify it from disk."""
    if store._read_only or store._load_empty:
        raise OSError("Restore storage is read-only")
    _check_json_round_trip(data)
    payload = {
        "version": store.version,
        "minor_version": store.minor_version,
        "key": store.key,
        "data": data,
    }
    path = Path(store.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    digest = hashlib.sha256()
    remaining = max_bytes
    temporary = path.with_name(path.name + RESTORE_STAGING_SUFFIX)
    remove_restore_staging(path)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as file:
            for text in encoder.iterencode(payload):
                chunk = text.encode("utf-8")
                remaining -= len(chunk)
                if remaining < 0:
                    raise ValueError("Restore data exceeds the private storage safety limit")
                file.write(chunk)
                digest.update(chunk)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        with path.open("rb") as saved:
            if hashlib.file_digest(saved, "sha256").digest() != digest.digest():
                raise OSError("Restore storage could not be verified")
    finally:
        remove_restore_staging(path)
