"""Automatic, expiring temporary context for conversation agents."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .memory import validate_memory_privacy

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}.temporary_memory"
MAX_ACTIVE_RECORDS = 100
MAX_CONTENT_LENGTH = 500
MAX_CATEGORY_LENGTH = 64
MAX_INJECT_RECORDS = 30
MAX_INJECT_CHARACTERS = 6_000

TEMPORARY_MEMORY_TOOL_NAMES = {
    "temporary_memory_add",
    "temporary_memory_update",
    "temporary_memory_delete",
}


@dataclass(slots=True, frozen=True)
class TemporaryMemoryRecord:
    """One concise fact that expires automatically."""

    memory_id: str
    scope_id: str
    content: str
    category: str
    source: str
    expires_at: str
    created_at: str
    updated_at: str


class TemporaryMemoryStore(Store[dict[str, Any]]):
    """Versioned private Home Assistant storage."""


class TemporaryMemory:
    """Concurrency-safe short-lived context store."""

    def __init__(self, store: TemporaryMemoryStore) -> None:
        self._store = store
        self._records: dict[str, TemporaryMemoryRecord] = {}
        self._lock = asyncio.Lock()
        self._initialized = False
        self.expired_pruned = 0

    async def async_initialize(self) -> None:
        """Load and prune records once at startup."""
        async with self._lock:
            if self._initialized:
                return
            data = await self._store.async_load()
            raw_records = data.get("records", []) if isinstance(data, Mapping) else []
            for raw in raw_records:
                try:
                    record = TemporaryMemoryRecord(**raw)
                    if _parse_expiry(record.expires_at) > dt_util.utcnow():
                        self._records[record.memory_id] = record
                    else:
                        self.expired_pruned += 1
                except TypeError, ValueError:
                    continue
            self._initialized = True
            if self.expired_pruned:
                await self._async_save_locked()

    async def async_active(self, scope_id: str) -> list[TemporaryMemoryRecord]:
        """Return bounded active context and opportunistically prune expiry."""
        async with self._lock:
            await self._async_prune_locked()
            return self._active_snapshot_locked(scope_id)

    async def async_active_snapshot(self, scope_id: str) -> list[TemporaryMemoryRecord]:
        """Return active context without pruning, saving, or changing counters."""
        async with self._lock:
            return self._active_snapshot_locked(scope_id)

    def _active_snapshot_locked(self, scope_id: str) -> list[TemporaryMemoryRecord]:
        """Select bounded, currently active records while the lock is held."""
        return self.select_active_snapshot(self._records.values(), scope_id)

    @staticmethod
    def select_active_snapshot(
        records: Iterable[TemporaryMemoryRecord], scope_id: str
    ) -> list[TemporaryMemoryRecord]:
        """Select the bounded active records for one scope without mutation."""
        now = dt_util.utcnow()
        records = [
            record
            for record in records
            if record.scope_id == scope_id and _parse_expiry(record.expires_at) > now
        ]
        records.sort(key=lambda item: (item.updated_at, item.expires_at), reverse=True)
        selected: list[TemporaryMemoryRecord] = []
        characters = 0
        for record in records:
            if len(selected) >= MAX_INJECT_RECORDS:
                break
            size = len(record.content)
            if selected and characters + size > MAX_INJECT_CHARACTERS:
                continue
            selected.append(record)
            characters += size
        return selected

    async def async_add(
        self,
        scope_id: str,
        content: str,
        expires_at: str,
        category: str = "general",
    ) -> dict[str, Any]:
        """Add an automatic fact, coalescing an exact active duplicate."""
        content = _clean(content, MAX_CONTENT_LENGTH, "content")
        category = _clean(category, MAX_CATEGORY_LENGTH, "category")
        validate_memory_privacy(content, automatic=True)
        expiry = _parse_future_expiry(expires_at)
        async with self._lock:
            await self._async_prune_locked()
            now = dt_util.utcnow().isoformat()
            for current in self._records.values():
                if (
                    current.scope_id == scope_id
                    and current.content.casefold() == content.casefold()
                ):
                    updated = TemporaryMemoryRecord(
                        current.memory_id,
                        current.scope_id,
                        content,
                        category,
                        "automatic",
                        expiry.isoformat(),
                        current.created_at,
                        now,
                    )
                    self._records[current.memory_id] = updated
                    await self._async_save_locked()
                    return {
                        "status": "updated",
                        "memory": temporary_memory_as_dict(updated),
                    }
            if len(self._records) >= MAX_ACTIVE_RECORDS:
                raise ValueError("temporary memory limit reached")
            record = TemporaryMemoryRecord(
                uuid4().hex,
                scope_id,
                content,
                category,
                "automatic",
                expiry.isoformat(),
                now,
                now,
            )
            self._records[record.memory_id] = record
            await self._async_save_locked()
            return {"status": "created", "memory": temporary_memory_as_dict(record)}

    async def async_update(
        self,
        scope_id: str,
        memory_id: str,
        content: str | None,
        expires_at: str | None,
        category: str | None,
    ) -> TemporaryMemoryRecord:
        """Update/supersede an owned temporary fact."""
        async with self._lock:
            await self._async_prune_locked()
            current = self._owned(scope_id, memory_id)
            new_content = (
                _clean(content, MAX_CONTENT_LENGTH, "content")
                if content is not None
                else current.content
            )
            validate_memory_privacy(new_content, automatic=True)
            new_expiry = (
                _parse_future_expiry(expires_at).isoformat()
                if expires_at is not None
                else current.expires_at
            )
            updated = TemporaryMemoryRecord(
                current.memory_id,
                current.scope_id,
                new_content,
                _clean(category, MAX_CATEGORY_LENGTH, "category")
                if category is not None
                else current.category,
                current.source,
                new_expiry,
                current.created_at,
                dt_util.utcnow().isoformat(),
            )
            self._records[memory_id] = updated
            await self._async_save_locked()
            return updated

    async def async_delete(self, scope_id: str, memory_ids: list[str]) -> int:
        """Delete selected records only from the current scope."""
        async with self._lock:
            deleted = 0
            for memory_id in set(memory_ids[:50]):
                record = self._records.get(memory_id)
                if record is not None and record.scope_id == scope_id:
                    del self._records[memory_id]
                    deleted += 1
            if deleted:
                await self._async_save_locked()
            return deleted

    async def async_list(self, scope_id: str) -> list[TemporaryMemoryRecord]:
        """List active records for management."""
        return await self.async_active(scope_id)

    async def async_list_all(self) -> list[TemporaryMemoryRecord]:
        """List bounded active records for administrator management."""
        async with self._lock:
            await self._async_prune_locked()
            records = sorted(
                self._records.values(), key=lambda item: item.updated_at, reverse=True
            )
            return records[:MAX_ACTIVE_RECORDS]

    def stats(self) -> dict[str, int]:
        """Return non-sensitive diagnostics."""
        return {
            "active_temporary_memory_count": len(self._records),
            "expired_temporary_memories_pruned": self.expired_pruned,
        }

    def scope_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self._records.values():
            counts[record.scope_id] = counts.get(record.scope_id, 0) + 1
        return counts

    async def async_backup_data(self) -> dict[str, Any]:
        """Return active records with their original absolute expiry."""
        async with self._lock:
            await self._async_prune_locked()
            return {"records": [asdict(record) for record in self._records.values()]}

    @staticmethod
    def validate_backup_data(data: Any) -> list[TemporaryMemoryRecord]:
        """Validate and drop records that have expired since backup creation."""
        if not isinstance(data, Mapping) or set(data) != {"records"}:
            raise ValueError("temporary memories are incomplete or corrupted")
        raw_records = data["records"]
        if not isinstance(raw_records, list) or len(raw_records) > MAX_ACTIVE_RECORDS:
            raise ValueError("temporary memory count is invalid")
        records: list[TemporaryMemoryRecord] = []
        seen: set[str] = set()
        now = dt_util.utcnow()
        for raw in raw_records:
            if not isinstance(raw, Mapping):
                raise ValueError("temporary memory record must be an object")
            try:
                record = TemporaryMemoryRecord(**raw)
            except TypeError as err:
                raise ValueError("temporary memory record is invalid") from err
            if not all(
                isinstance(value, str)
                for value in (
                    record.memory_id,
                    record.scope_id,
                    record.content,
                    record.category,
                    record.source,
                    record.expires_at,
                    record.created_at,
                    record.updated_at,
                )
            ):
                raise ValueError("temporary memory fields must be strings")
            if (
                not record.memory_id
                or len(record.memory_id) > 128
                or record.memory_id in seen
                or not record.scope_id
                or len(record.scope_id) > 128
                or record.source != "automatic"
            ):
                raise ValueError("temporary memory metadata is invalid")
            _clean(record.content, MAX_CONTENT_LENGTH, "content")
            _clean(record.category, MAX_CATEGORY_LENGTH, "category")
            expiry = _parse_expiry(record.expires_at)
            if (
                dt_util.parse_datetime(record.created_at) is None
                or dt_util.parse_datetime(record.updated_at) is None
            ):
                raise ValueError("temporary memory timestamp is invalid")
            seen.add(record.memory_id)
            if expiry > now:
                records.append(record)
        return records

    async def async_replace_backup(self, records: list[TemporaryMemoryRecord]) -> None:
        """Replace active temporary memories without changing their expiry."""
        async with self._lock:
            self._records = {record.memory_id: record for record in records}
            await self._async_save_locked()

    def _owned(self, scope_id: str, memory_id: str) -> TemporaryMemoryRecord:
        record = self._records.get(memory_id)
        if record is None or record.scope_id != scope_id:
            raise ValueError("temporary memory not found")
        return record

    async def _async_prune_locked(self) -> None:
        now = dt_util.utcnow()
        expired = [
            memory_id
            for memory_id, record in self._records.items()
            if _parse_expiry(record.expires_at) <= now
        ]
        for memory_id in expired:
            del self._records[memory_id]
        if expired:
            self.expired_pruned += len(expired)
            await self._async_save_locked()

    async def _async_save_locked(self) -> None:
        await self._store.async_save(
            {"records": [asdict(record) for record in self._records.values()]}
        )


def _parse_expiry(value: str):
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        raise ValueError("expires_at must be an ISO date-time with a timezone")
    if parsed.tzinfo is None:
        raise ValueError("expires_at must include a timezone")
    return dt_util.as_utc(parsed)


def _parse_future_expiry(value: str):
    parsed = _parse_expiry(value)
    if parsed <= dt_util.utcnow():
        raise ValueError("expires_at must be in the future")
    if parsed > dt_util.utcnow() + timedelta(days=366):
        raise ValueError("temporary memory cannot last longer than one year")
    return parsed


def _clean(value: str, limit: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = " ".join(value.split()).strip()
    if not value or len(value) > limit:
        raise ValueError(f"{field} must contain 1 to {limit} characters")
    return value


def temporary_memory_as_dict(
    record: TemporaryMemoryRecord, *, include_scope: bool = False
) -> dict[str, str]:
    result = {
        "memory_id": record.memory_id,
        "content": record.content,
        "category": record.category,
        "source": record.source,
        "expires_at": record.expires_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    if include_scope:
        result["scope_id"] = record.scope_id
    return result


def temporary_memory_tools() -> list[dict[str, Any]]:
    """Return the small model-facing maintenance surface."""
    common = {"type": "temporary_memory"}
    return [
        {
            "spec": {
                "name": "temporary_memory_add",
                "description": "Silently store one useful short-lived fact for the current context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "expires_at": {
                            "type": "string",
                            "description": "ISO date-time with timezone, inferred reasonably from ordinary language.",
                        },
                        "category": {"type": "string"},
                    },
                    "required": ["content", "expires_at"],
                    "additionalProperties": False,
                },
            },
            "function": {**common, "operation": "add"},
        },
        {
            "spec": {
                "name": "temporary_memory_update",
                "description": "Silently supersede an active temporary fact when circumstances change.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string"},
                        "content": {"type": "string"},
                        "expires_at": {"type": "string"},
                        "category": {"type": "string"},
                    },
                    "required": ["memory_id"],
                    "additionalProperties": False,
                },
            },
            "function": {**common, "operation": "update"},
        },
        {
            "spec": {
                "name": "temporary_memory_delete",
                "description": "Silently forget one or more active temporary facts for the current context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["memory_ids"],
                    "additionalProperties": False,
                },
            },
            "function": {**common, "operation": "delete"},
        },
    ]


_MANAGERS = f"{DOMAIN}.temporary_memory_managers"


def _temporary_memory_store(
    hass: Any, entry_id: str, subentry_id: str
) -> TemporaryMemoryStore:
    """Build the versioned store adapter without loading or changing manager state."""
    return TemporaryMemoryStore(
        hass,
        STORAGE_VERSION,
        f"{STORAGE_KEY_PREFIX}.{entry_id}.{subentry_id}",
        private=True,
        atomic_writes=True,
        serialize_in_event_loop=False,
    )


def get_loaded_temporary_memory(
    hass: Any, entry_id: str, subentry_id: str
) -> TemporaryMemory | None:
    """Return an already initialized manager without creating runtime state."""
    return cast(
        TemporaryMemory | None,
        hass.data.get(_MANAGERS, {}).get((entry_id, subentry_id)),
    )


async def async_read_temporary_memory_snapshot(
    hass: Any, entry_id: str, subentry_id: str, scope_id: str
) -> list[TemporaryMemoryRecord]:
    """Read active stored context without creating, pruning, or saving a manager."""
    data = await _temporary_memory_store(hass, entry_id, subentry_id).async_load()
    raw_records = data.get("records", []) if isinstance(data, Mapping) else []
    records: list[TemporaryMemoryRecord] = []
    for raw in raw_records:
        try:
            record = TemporaryMemoryRecord(**raw)
            _parse_expiry(record.expires_at)
            records.append(record)
        except TypeError, ValueError:
            continue
    return TemporaryMemory.select_active_snapshot(records, scope_id)


async def async_get_temporary_memory(
    hass: Any, entry_id: str, subentry_id: str
) -> TemporaryMemory:
    managers = hass.data.setdefault(_MANAGERS, {})
    key = (entry_id, subentry_id)
    if key not in managers:
        managers[key] = TemporaryMemory(
            _temporary_memory_store(hass, entry_id, subentry_id)
        )
    manager = managers[key]
    await manager.async_initialize()
    return cast(TemporaryMemory, manager)
