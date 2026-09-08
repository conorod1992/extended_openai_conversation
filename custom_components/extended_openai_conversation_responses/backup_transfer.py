"""Bounded chunked transport for complete per-agent full backups."""

from __future__ import annotations

import asyncio
import base64
import binascii
from contextlib import suppress
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import PurePosixPath
import re
import stat
import tempfile
import time
from typing import Any, cast
from uuid import uuid4
import zipfile

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from . import backup, transfer
from .agent_maintenance import get_agent_maintenance_gate
from .const import DOMAIN

WS_BACKUP_TRANSFER = f"{DOMAIN}/management/backup_transfer"
ARCHIVE_FORMAT = "extended_openai_conversation_backup_archive"
ARCHIVE_VERSION = 1
PAYLOAD_NAME = "backup.json"
MANIFEST_NAME = "manifest.json"
BACKUP_CHUNK_BYTES = 512 * 1024
MAX_BACKUP_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_BACKUP_UNCOMPRESSED_BYTES = backup.MAX_BACKUP_BYTES
MAX_BACKUP_ARCHIVE_MEMBERS = 2
MAX_MANIFEST_BYTES = 64 * 1024
MAX_TRANSFER_SESSIONS = 4
MAX_TRANSFER_TEMP_BYTES = 256 * 1024 * 1024
TRANSFER_TTL_SECONDS = 15 * 60
_MAX_BASE64_CHUNK_CHARS = 4 * math.ceil(BACKUP_CHUNK_BYTES / 3) + 4
_EXPORTS_KEY = f"{DOMAIN}.backup_transfer_exports"
_IMPORTS_KEY = f"{DOMAIN}.backup_transfer_imports"
_REGISTRY_LOCK_KEY = f"{DOMAIN}.backup_transfer_registry_lock"
_START_LOCK_KEY = f"{DOMAIN}.backup_transfer_start_lock"
_WS_SETUP_KEY = f"{DOMAIN}.backup_transfer_ws_setup"
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(slots=True)
class ExportSession:
    """One bounded server-side archive awaiting browser download."""

    session_id: str
    entry_id: str
    subentry_id: str
    path: str
    filename: str
    size: int
    sha256: str
    expires_at: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(slots=True)
class ImportSession:
    """One ordered browser upload held in a private temporary file."""

    session_id: str
    entry_id: str
    subentry_id: str
    path: str
    filename: str
    expected_size: int
    kind: str
    expires_at: float
    received: int = 0
    next_index: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _exports(hass: HomeAssistant) -> dict[str, ExportSession]:
    return cast(dict[str, ExportSession], hass.data.setdefault(_EXPORTS_KEY, {}))


def _imports(hass: HomeAssistant) -> dict[str, ImportSession]:
    return cast(dict[str, ImportSession], hass.data.setdefault(_IMPORTS_KEY, {}))


def _registry_lock(hass: HomeAssistant) -> asyncio.Lock:
    return cast(asyncio.Lock, hass.data.setdefault(_REGISTRY_LOCK_KEY, asyncio.Lock()))


def _start_lock(hass: HomeAssistant) -> asyncio.Lock:
    """Serialize transfer starts so unregistered temp files are quota-accounted."""
    return cast(asyncio.Lock, hass.data.setdefault(_START_LOCK_KEY, asyncio.Lock()))


def _remove_file(path: str) -> None:
    with suppress(FileNotFoundError):
        os.unlink(path)


async def _async_remove_path(hass: HomeAssistant, path: str) -> None:
    """Finish deleting a private temp file before propagating caller cancellation."""
    task = asyncio.ensure_future(hass.async_add_executor_job(_remove_file, path))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(BaseException):
            await task
        raise


async def _async_delete_session_file(
    hass: HomeAssistant, session: ExportSession | ImportSession
) -> None:
    async with session.lock:
        await _async_remove_path(hass, session.path)


async def _async_delete_sessions(
    hass: HomeAssistant, sessions: list[ExportSession | ImportSession]
) -> None:
    """Delete every detached session even if cleanup itself is cancelled."""
    cancellation: asyncio.CancelledError | None = None
    for session in sessions:
        try:
            await _async_delete_session_file(hass, session)
        except asyncio.CancelledError as err:
            if cancellation is None:
                cancellation = err
    if cancellation is not None:
        raise cancellation


async def _async_cleanup_expired(hass: HomeAssistant) -> None:
    """Discard abandoned transfer files without a perpetual cleanup task."""
    now = time.monotonic()
    expired: list[ExportSession | ImportSession] = []
    async with _registry_lock(hass):
        exports = _exports(hass)
        for export_session_id, export_session in tuple(exports.items()):
            if export_session.expires_at <= now:
                expired.append(export_session)
                exports.pop(export_session_id, None)
        imports = _imports(hass)
        for import_session_id, import_session in tuple(imports.items()):
            if import_session.expires_at <= now:
                expired.append(import_session)
                imports.pop(import_session_id, None)
    await _async_delete_sessions(hass, expired)


def _temporary_bytes(hass: HomeAssistant) -> int:
    return sum(session.size for session in _exports(hass).values()) + sum(
        session.expected_size for session in _imports(hass).values()
    )


def _ensure_session_capacity(hass: HomeAssistant, requested_bytes: int = 0) -> None:
    session_count = len(_exports(hass)) + len(_imports(hass))
    if session_count >= MAX_TRANSFER_SESSIONS:
        raise backup.BackupError(
            "Too many full backup transfers are already pending; finish or cancel one first"
        )
    if (
        requested_bytes < 0
        or _temporary_bytes(hass) + requested_bytes > MAX_TRANSFER_TEMP_BYTES
    ):
        raise backup.BackupError(
            "Full backup transfers would exceed the temporary storage quota"
        )


def _resolve_agent(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> tuple[Any, Any]:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise backup.BackupError("Integration entry not found")
    subentry = entry.subentries.get(subentry_id)
    if subentry is None or subentry.subentry_type != "conversation":
        raise backup.BackupError("Conversation agent not found")
    return entry, subentry


def _redacted_document(snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot.get("format") == transfer.TRANSFER_FORMAT:
        return transfer.redact_transfer_document(snapshot)
    return {
        **snapshot,
        "agent": {
            **snapshot["agent"],
            "config": backup._safe_configuration(snapshot["agent"]["config"]),
        },
        "request_rules": backup._safe_configuration(snapshot["request_rules"]),
    }


def _safe_export_filename(document: dict[str, Any]) -> str:
    title = str(document["agent"]["title"])
    safe_title = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
    date = str(document["created_at"])[:10]
    label = (
        "custom-backup"
        if document.get("format") == transfer.TRANSFER_FORMAT
        else "full-backup"
    )
    return f"{safe_title or 'conversation-agent'}-{label}-{date}.zip"


def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file_handle:
        while chunk := file_handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _build_archive_file(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Serialize and compress one detached snapshot with explicit byte ceilings."""
    document = _redacted_document(snapshot)
    fd, path = tempfile.mkstemp(prefix="extended-openai-backup-", suffix=".zip")
    os.close(fd)
    try:
        payload_hash = hashlib.sha256()
        payload_bytes = 0
        encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"))
        with open(path, "w+b") as archive_file:
            with zipfile.ZipFile(
                archive_file,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                allowZip64=True,
            ) as archive:
                with archive.open(PAYLOAD_NAME, "w", force_zip64=True) as payload:
                    for text in encoder.iterencode(document):
                        encoded = text.encode("utf-8")
                        payload_bytes += len(encoded)
                        if payload_bytes > MAX_BACKUP_UNCOMPRESSED_BYTES:
                            raise backup.BackupError(
                                "This full backup exceeds the 128 MB uncompressed safety limit"
                            )
                        payload_hash.update(encoded)
                        payload.write(encoded)
                manifest = {
                    "format": ARCHIVE_FORMAT,
                    "version": ARCHIVE_VERSION,
                    "payload": PAYLOAD_NAME,
                    "payload_bytes": payload_bytes,
                    "payload_sha256": payload_hash.hexdigest(),
                }
                archive.writestr(
                    MANIFEST_NAME,
                    json.dumps(manifest, ensure_ascii=True, separators=(",", ":")),
                    compress_type=zipfile.ZIP_STORED,
                )
            archive_file.flush()
            size = archive_file.tell()
        if size > MAX_BACKUP_ARCHIVE_BYTES:
            raise backup.BackupError(
                "This full backup exceeds the 64 MB compressed safety limit"
            )
        return {
            "path": path,
            "filename": _safe_export_filename(document),
            "size": size,
            "sha256": _hash_file(path),
            "uncompressed_size": payload_bytes,
        }
    except BaseException:
        _remove_file(path)
        raise


async def _async_build_archive_file(
    hass: HomeAssistant, snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Finish executor work before propagating cancellation so no file is orphaned."""
    task = asyncio.ensure_future(
        hass.async_add_executor_job(_build_archive_file, snapshot)
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            result = await task
        except BaseException:
            pass
        else:
            await _async_remove_path(hass, result["path"])
        raise


def _read_file_chunk_base64(path: str, offset: int, length: int) -> str:
    with open(path, "rb") as file_handle:
        file_handle.seek(offset)
        data = file_handle.read(length)
    if len(data) != length:
        raise backup.BackupError("The staged backup archive is incomplete")
    return base64.b64encode(data).decode("ascii")


def _create_upload_file() -> str:
    fd, path = tempfile.mkstemp(prefix="extended-openai-backup-upload-")
    os.close(fd)
    return path


async def _async_create_upload_file(hass: HomeAssistant) -> str:
    """Never orphan a newly-created upload file when the request is cancelled."""
    task = asyncio.ensure_future(hass.async_add_executor_job(_create_upload_file))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            path = await task
        except BaseException:
            pass
        else:
            await _async_remove_path(hass, path)
        raise


def _decode_append_file(path: str, encoded: str, expected_length: int) -> int:
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as err:
        raise backup.BackupError(
            "The uploaded backup chunk is not valid base64"
        ) from err
    if len(data) != expected_length:
        raise backup.BackupError("The uploaded backup chunk has an unexpected length")
    with open(path, "ab") as file_handle:
        file_handle.write(data)
        file_handle.flush()
    return len(data)


async def _async_append_file(
    hass: HomeAssistant, path: str, encoded: str, expected_length: int
) -> int:
    task = asyncio.ensure_future(
        hass.async_add_executor_job(_decode_append_file, path, encoded, expected_length)
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(BaseException):
            await task
        raise


def _member_is_safe(info: zipfile.ZipInfo) -> bool:
    name = info.filename
    path = PurePosixPath(name)
    mode = info.external_attr >> 16
    return bool(
        name
        and "\\" not in name
        and "\x00" not in name
        and not path.is_absolute()
        and ".." not in path.parts
        and not info.is_dir()
        and not stat.S_ISLNK(mode)
        and not info.flag_bits & 0x1
    )


def _read_archive_member_bounded(
    archive: zipfile.ZipFile,
    name: str,
    limit: int,
    overflow_message: str,
    corruption_message: str,
) -> bytes:
    """Read one member while enforcing the actual decompressed byte count."""
    data = bytearray()
    try:
        with archive.open(name, "r") as member:
            while True:
                chunk = member.read(min(1024 * 1024, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > limit:
                    raise backup.BackupError(overflow_message)
    except backup.BackupError:
        raise
    except (RuntimeError, zipfile.BadZipFile, OSError, EOFError) as err:
        raise backup.BackupError(corruption_message) from err
    return bytes(data)


def _load_archive_document(path: str) -> dict[str, Any]:
    try:
        size = os.path.getsize(path)
    except OSError as err:
        raise backup.BackupError("The staged backup archive is unavailable") from err
    if size > MAX_BACKUP_ARCHIVE_BYTES:
        raise backup.BackupError("The compressed backup exceeds the 64 MB safety limit")
    try:
        archive = zipfile.ZipFile(path, "r")
    except (zipfile.BadZipFile, OSError) as err:
        raise backup.BackupError(
            "The backup archive is incomplete or corrupted"
        ) from err
    with archive:
        members = archive.infolist()
        if len(members) != MAX_BACKUP_ARCHIVE_MEMBERS:
            raise backup.BackupError("The backup archive contains unexpected files")
        names = [item.filename for item in members]
        if len(set(names)) != len(names) or set(names) != {PAYLOAD_NAME, MANIFEST_NAME}:
            raise backup.BackupError("The backup archive contains unexpected files")
        if any(not _member_is_safe(item) for item in members):
            raise backup.BackupError("The backup archive contains an unsafe file entry")
        if any(
            item.compress_type not in {zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED}
            for item in members
        ):
            raise backup.BackupError("The backup archive uses unsupported compression")
        manifest_info = archive.getinfo(MANIFEST_NAME)
        payload_info = archive.getinfo(PAYLOAD_NAME)
        if manifest_info.file_size > MAX_MANIFEST_BYTES:
            raise backup.BackupError("The backup archive manifest is too large")
        if payload_info.file_size > MAX_BACKUP_UNCOMPRESSED_BYTES:
            raise backup.BackupError(
                "The backup archive exceeds the uncompressed safety limit"
            )
        total_uncompressed = sum(item.file_size for item in members)
        if total_uncompressed > MAX_BACKUP_UNCOMPRESSED_BYTES + MAX_MANIFEST_BYTES:
            raise backup.BackupError(
                "The backup archive exceeds the uncompressed safety limit"
            )
        manifest_payload = _read_archive_member_bounded(
            archive,
            MANIFEST_NAME,
            MAX_MANIFEST_BYTES,
            "The backup archive manifest is too large",
            "The backup archive manifest is corrupted",
        )
        try:
            manifest = json.loads(manifest_payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise backup.BackupError("The backup archive manifest is invalid") from err
        if not isinstance(manifest, dict) or set(manifest) != {
            "format",
            "version",
            "payload",
            "payload_bytes",
            "payload_sha256",
        }:
            raise backup.BackupError("The backup archive manifest is invalid")
        payload_bytes = manifest.get("payload_bytes")
        payload_sha256 = manifest.get("payload_sha256")
        if (
            manifest.get("format") != ARCHIVE_FORMAT
            or manifest.get("version") != ARCHIVE_VERSION
            or manifest.get("payload") != PAYLOAD_NAME
            or isinstance(payload_bytes, bool)
            or not isinstance(payload_bytes, int)
            or payload_bytes < 1
            or payload_bytes > MAX_BACKUP_UNCOMPRESSED_BYTES
            or payload_info.file_size != payload_bytes
            or not isinstance(payload_sha256, str)
            or _HEX_SHA256.fullmatch(payload_sha256) is None
        ):
            raise backup.BackupError("The backup archive manifest is invalid")
        payload = _read_archive_member_bounded(
            archive,
            PAYLOAD_NAME,
            MAX_BACKUP_UNCOMPRESSED_BYTES,
            "The backup archive exceeds the uncompressed safety limit",
            "The backup archive payload is corrupted",
        )
        if len(payload) != payload_bytes:
            raise backup.BackupError("The backup archive payload is incomplete")
        if hashlib.sha256(payload).hexdigest() != payload_sha256:
            raise backup.BackupError(
                "The backup archive payload checksum does not match"
            )
        try:
            document = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise backup.BackupError(
                "The backup archive payload is not valid JSON"
            ) from err
    if not isinstance(document, dict):
        raise backup.BackupError("The backup archive payload is invalid")
    return document


def _load_legacy_json_document(path: str) -> dict[str, Any]:
    try:
        size = os.path.getsize(path)
        if size > MAX_BACKUP_UNCOMPRESSED_BYTES:
            raise backup.BackupError("The JSON backup exceeds the 128 MB safety limit")
        with open(path, "rb") as file_handle:
            payload = file_handle.read(MAX_BACKUP_UNCOMPRESSED_BYTES + 1)
    except backup.BackupError:
        raise
    except OSError as err:
        raise backup.BackupError("The staged JSON backup is unavailable") from err
    if len(payload) > MAX_BACKUP_UNCOMPRESSED_BYTES:
        raise backup.BackupError("The JSON backup exceeds the 128 MB safety limit")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise backup.BackupError("The JSON backup is incomplete or corrupted") from err
    if not isinstance(document, dict):
        raise backup.BackupError(
            "This file is not an Extended OpenAI Conversation backup"
        )
    return document


def _load_uploaded_document(path: str, kind: str) -> dict[str, Any]:
    try:
        with open(path, "rb") as file_handle:
            signature = file_handle.read(4)
    except OSError as err:
        raise backup.BackupError("The staged backup upload is unavailable") from err
    is_zip = signature.startswith(b"PK")
    if kind == "archive" and not is_zip:
        raise backup.BackupError("The selected ZIP backup is incomplete or corrupted")
    if kind == "legacy_json" and is_zip:
        raise backup.BackupError(
            "The selected JSON backup unexpectedly contains a ZIP archive"
        )
    return _load_archive_document(path) if is_zip else _load_legacy_json_document(path)


def _load_prepared_restore(
    path: str, kind: str, target_subentry_id: str
) -> transfer.PreparedTransfer:
    document = _load_uploaded_document(path, kind)
    return transfer.inspect_transfer(document, target_subentry_id)


async def _discard_export(hass: HomeAssistant, session_id: str) -> bool:
    async with _registry_lock(hass):
        session = _exports(hass).pop(session_id, None)
    if session is None:
        return False
    await _async_delete_session_file(hass, session)
    return True


async def _discard_import(hass: HomeAssistant, session_id: str) -> bool:
    async with _registry_lock(hass):
        session = _imports(hass).pop(session_id, None)
    if session is None:
        return False
    await _async_delete_session_file(hass, session)
    return True


def _require_session_identity(
    session: ExportSession | ImportSession, entry_id: str, subentry_id: str
) -> None:
    if session.entry_id != entry_id or session.subentry_id != subentry_id:
        raise backup.BackupError("The backup transfer does not belong to this agent")


async def _start_export(
    hass: HomeAssistant, entry: Any, subentry: Any, data: dict[str, Any]
) -> dict[str, Any]:
    async with _start_lock(hass):
        await _async_cleanup_expired(hass)
        # Reserve the worst-case archive footprint before doing any expensive work.
        # Serializing starts means no second unregistered temp file can bypass this
        # quota while the first archive is being built.
        async with _registry_lock(hass):
            _ensure_session_capacity(hass, MAX_BACKUP_ARCHIVE_BYTES)

        mode = data.get("mode", "full")
        if mode not in {"full", "custom"}:
            raise backup.BackupError("Export mode must be full or custom")
        gate = get_agent_maintenance_gate(hass, entry.entry_id, subentry.subentry_id)
        async with gate.exclusive():
            snapshot = (
                await backup.async_collect_backup_snapshot(hass, entry, subentry)
                if mode == "full"
                else await transfer.async_collect_transfer_snapshot(
                    hass,
                    entry,
                    subentry,
                    mode="custom",
                    sections=data.get("sections"),
                )
            )
        try:
            result = await _async_build_archive_file(hass, snapshot)
        except backup.BackupError:
            raise
        except Exception as err:
            raise backup.BackupError(
                "The full backup archive could not be created safely"
            ) from err

        session_id = uuid4().hex
        session = ExportSession(
            session_id=session_id,
            entry_id=entry.entry_id,
            subentry_id=subentry.subentry_id,
            path=result["path"],
            filename=result["filename"],
            size=result["size"],
            sha256=result["sha256"],
            expires_at=time.monotonic() + TRANSFER_TTL_SECONDS,
        )
        try:
            async with _registry_lock(hass):
                # Existing sessions can only disappear while _start_lock is held;
                # the earlier worst-case reservation therefore remains sufficient.
                _exports(hass)[session_id] = session
        except BaseException:
            await _async_remove_path(hass, session.path)
            raise

    return {
        "session_id": session_id,
        "filename": session.filename,
        "mode": mode,
        "content_type": "application/zip",
        "size": session.size,
        "sha256": session.sha256,
        "chunk_size": BACKUP_CHUNK_BYTES,
        "chunk_count": math.ceil(session.size / BACKUP_CHUNK_BYTES),
        "uncompressed_size": result["uncompressed_size"],
    }


async def _export_chunk(
    hass: HomeAssistant,
    entry_id: str,
    subentry_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    await _async_cleanup_expired(hass)
    session_id = data.get("session_id")
    index = data.get("index")
    if not isinstance(session_id, str) or not session_id:
        raise backup.BackupError("A backup transfer session is required")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise backup.BackupError("The backup chunk index is invalid")
    session = _exports(hass).get(session_id)
    if session is None:
        raise backup.BackupError("The backup transfer has expired or does not exist")
    _require_session_identity(session, entry_id, subentry_id)
    async with session.lock:
        if _exports(hass).get(session_id) is not session:
            raise backup.BackupError("The backup transfer has expired or was cancelled")
        session.expires_at = time.monotonic() + TRANSFER_TTL_SECONDS
        chunk_count = math.ceil(session.size / BACKUP_CHUNK_BYTES)
        if index >= chunk_count:
            raise backup.BackupError("The backup chunk index is out of range")
        offset = index * BACKUP_CHUNK_BYTES
        length = min(BACKUP_CHUNK_BYTES, session.size - offset)
        try:
            encoded = await hass.async_add_executor_job(
                _read_file_chunk_base64, session.path, offset, length
            )
        except backup.BackupError:
            raise
        except OSError as err:
            raise backup.BackupError(
                "The staged backup archive is unavailable"
            ) from err
    return {
        "session_id": session_id,
        "index": index,
        "offset": offset,
        "data": encoded,
        "bytes": length,
        "final": index == chunk_count - 1,
    }


async def _start_import(
    hass: HomeAssistant,
    entry_id: str,
    subentry_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    filename = data.get("filename")
    size = data.get("size")
    if not isinstance(filename, str) or not filename.strip() or len(filename) > 255:
        raise backup.BackupError("The backup filename is invalid")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise backup.BackupError("The backup file size is invalid")
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.casefold()
    kind = (
        "archive"
        if suffix == ".zip"
        else "legacy_json"
        if suffix == ".json"
        else "auto"
    )
    limit = (
        MAX_BACKUP_ARCHIVE_BYTES if kind == "archive" else MAX_BACKUP_UNCOMPRESSED_BYTES
    )
    if size > limit:
        label = "compressed" if kind == "archive" else "uploaded"
        raise backup.BackupError(
            f"The {label} backup exceeds the {limit // (1024 * 1024)} MB safety limit"
        )

    async with _start_lock(hass):
        await _async_cleanup_expired(hass)
        async with _registry_lock(hass):
            _ensure_session_capacity(hass, size)

        try:
            path = await _async_create_upload_file(hass)
        except OSError as err:
            raise backup.BackupError(
                "A temporary backup upload file could not be created"
            ) from err

        session_id = uuid4().hex
        session = ImportSession(
            session_id=session_id,
            entry_id=entry_id,
            subentry_id=subentry_id,
            path=path,
            filename=filename,
            expected_size=size,
            kind=kind,
            expires_at=time.monotonic() + TRANSFER_TTL_SECONDS,
        )
        try:
            async with _registry_lock(hass):
                _imports(hass)[session_id] = session
        except BaseException:
            await _async_remove_path(hass, path)
            raise

    return {
        "session_id": session_id,
        "chunk_size": BACKUP_CHUNK_BYTES,
        "chunk_count": math.ceil(size / BACKUP_CHUNK_BYTES),
        "max_archive_bytes": MAX_BACKUP_ARCHIVE_BYTES,
        "max_uncompressed_bytes": MAX_BACKUP_UNCOMPRESSED_BYTES,
    }


async def _import_chunk(
    hass: HomeAssistant,
    entry_id: str,
    subentry_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    await _async_cleanup_expired(hass)
    session_id = data.get("session_id")
    index = data.get("index")
    encoded = data.get("data")
    if not isinstance(session_id, str) or not session_id:
        raise backup.BackupError("A backup upload session is required")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise backup.BackupError("The backup chunk index is invalid")
    if (
        not isinstance(encoded, str)
        or not encoded
        or len(encoded) > _MAX_BASE64_CHUNK_CHARS
    ):
        raise backup.BackupError("The uploaded backup chunk is invalid")
    session = _imports(hass).get(session_id)
    if session is None:
        raise backup.BackupError("The backup upload has expired or does not exist")
    _require_session_identity(session, entry_id, subentry_id)
    try:
        async with session.lock:
            if _imports(hass).get(session_id) is not session:
                raise backup.BackupError(
                    "The backup upload has expired or was cancelled"
                )
            session.expires_at = time.monotonic() + TRANSFER_TTL_SECONDS
            if index != session.next_index:
                raise backup.BackupError(
                    f"Expected backup chunk {session.next_index}, received {index}"
                )
            remaining = session.expected_size - session.received
            if remaining <= 0:
                raise backup.BackupError("The backup upload is already complete")
            expected_length = min(BACKUP_CHUNK_BYTES, remaining)
            written = await _async_append_file(
                hass, session.path, encoded, expected_length
            )
            session.received += written
            session.next_index += 1
    except asyncio.CancelledError:
        await _discard_import(hass, session_id)
        raise
    except OSError as err:
        await _discard_import(hass, session_id)
        raise backup.BackupError(
            "The backup upload could not be stored safely"
        ) from err
    return {
        "session_id": session_id,
        "received": session.received,
        "next_index": session.next_index,
        "complete": session.received == session.expected_size,
    }


def _completed_import(
    hass: HomeAssistant,
    session_id: Any,
    entry_id: str,
    subentry_id: str,
) -> ImportSession:
    if not isinstance(session_id, str) or not session_id:
        raise backup.BackupError("A backup upload session is required")
    session = _imports(hass).get(session_id)
    if session is None:
        raise backup.BackupError("The backup upload has expired or does not exist")
    _require_session_identity(session, entry_id, subentry_id)
    if session.received != session.expected_size:
        raise backup.BackupError("The backup upload is incomplete")
    return session


async def _inspect_import(
    hass: HomeAssistant,
    entry_id: str,
    subentry_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    await _async_cleanup_expired(hass)
    session = _completed_import(hass, data.get("session_id"), entry_id, subentry_id)
    async with session.lock:
        if _imports(hass).get(session.session_id) is not session:
            raise backup.BackupError("The backup upload has expired or was cancelled")
        session.expires_at = time.monotonic() + TRANSFER_TTL_SECONDS
        prepared = await hass.async_add_executor_job(
            _load_prepared_restore, session.path, session.kind, subentry_id
        )
    entry, subentry = _resolve_agent(hass, entry_id, subentry_id)
    _target, preview = await transfer.async_materialize_restore(
        hass, entry, subentry, prepared, sections=data.get("sections")
    )
    return {
        **transfer.inspection_for_frontend(prepared),
        "preview": preview,
    }


async def _take_completed_import(
    hass: HomeAssistant,
    session_id: Any,
    entry_id: str,
    subentry_id: str,
) -> ImportSession:
    session = _completed_import(hass, session_id, entry_id, subentry_id)
    async with session.lock, _registry_lock(hass):
        if _imports(hass).get(session.session_id) is not session:
            raise backup.BackupError("The backup upload has expired or was cancelled")
        if session.received != session.expected_size:
            raise backup.BackupError("The backup upload is incomplete")
        _imports(hass).pop(session.session_id, None)
    return session


async def _restore_import(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    data: dict[str, Any],
) -> dict[str, Any]:
    await _async_cleanup_expired(hass)
    session = await _take_completed_import(
        hass, data.get("session_id"), entry.entry_id, subentry.subentry_id
    )
    try:
        prepared = await hass.async_add_executor_job(
            _load_prepared_restore,
            session.path,
            session.kind,
            subentry.subentry_id,
        )
        # Selective restore is materialized into one complete target snapshot, then
        # delegated to the existing restart-safe full transaction.
        return await transfer.async_restore_transfer(
            hass, entry, subentry, prepared, sections=data.get("sections")
        )
    finally:
        await _async_remove_path(hass, session.path)


async def async_backup_transfer_command(
    hass: HomeAssistant, message: dict[str, Any]
) -> dict[str, Any]:
    """Execute one bounded transfer step for an already-authenticated administrator."""
    entry_id = message["entry_id"]
    subentry_id = message["subentry_id"]
    entry, subentry = _resolve_agent(hass, entry_id, subentry_id)
    action = message["action"]
    data = message.get("data") or {}
    if not isinstance(data, dict):
        raise backup.BackupError("Backup transfer data must be an object")

    if action == "setup_export":
        return await transfer.async_create_setup_export(hass, entry, subentry)
    if action == "export_start":
        return await _start_export(hass, entry, subentry, data)
    if action == "export_chunk":
        return await _export_chunk(hass, entry_id, subentry_id, data)
    if action == "export_cancel":
        session_id = data.get("session_id")
        if isinstance(session_id, str):
            export_session = _exports(hass).get(session_id)
            if export_session is not None:
                _require_session_identity(export_session, entry_id, subentry_id)
            return {"cancelled": await _discard_export(hass, session_id)}
        return {"cancelled": False}
    if action == "import_start":
        return await _start_import(hass, entry_id, subentry_id, data)
    if action == "import_chunk":
        return await _import_chunk(hass, entry_id, subentry_id, data)
    if action == "import_inspect":
        return await _inspect_import(hass, entry_id, subentry_id, data)
    if action == "import_restore":
        return await _restore_import(hass, entry, subentry, data)
    if action == "import_cancel":
        session_id = data.get("session_id")
        if isinstance(session_id, str):
            import_session = _imports(hass).get(session_id)
            if import_session is not None:
                _require_session_identity(import_session, entry_id, subentry_id)
            return {"cancelled": await _discard_import(hass, session_id)}
        return {"cancelled": False}
    raise backup.BackupError("Unsupported export/import transfer action")


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_BACKUP_TRANSFER,
        vol.Required("action"): str,
        vol.Required("entry_id"): str,
        vol.Required("subentry_id"): str,
        vol.Optional("data", default=dict): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_backup_transfer(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Transfer bounded authenticated export/import files."""
    try:
        result = await async_backup_transfer_command(hass, msg)
    except (HomeAssistantError, RuntimeError, ValueError) as err:
        connection.send_error(msg["id"], "invalid_request", str(err))
        return
    connection.send_result(msg["id"], result)


async def _async_cleanup_all(hass: HomeAssistant) -> None:
    async with _registry_lock(hass):
        sessions: list[ExportSession | ImportSession] = [
            *_exports(hass).values(),
            *_imports(hass).values(),
        ]
        _exports(hass).clear()
        _imports(hass).clear()
    await _async_delete_sessions(hass, sessions)


def setup_backup_transfer_websocket(hass: HomeAssistant) -> bool:
    """Register the narrow admin-only full-backup transport exactly once."""
    if hass.data.get(_WS_SETUP_KEY):
        return False
    websocket_api.async_register_command(hass, websocket_backup_transfer)

    @callback
    def cleanup_on_stop(_event: Any) -> None:
        hass.async_create_task(
            _async_cleanup_all(hass), "clean up full backup transfers"
        )

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, cleanup_on_stop)
    hass.data[_WS_SETUP_KEY] = True
    return True
