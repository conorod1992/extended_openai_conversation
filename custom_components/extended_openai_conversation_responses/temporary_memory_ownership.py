"""Ownership boundaries for retained Temporary Memory."""

from __future__ import annotations

from collections import Counter
from contextvars import ContextVar
from dataclasses import replace
from functools import wraps
import logging
from typing import Any, cast

from homeassistant.util import dt as dt_util

from .scope import SHARED_HOUSEHOLD_SCOPE_ID
from .temporary_memory import (
    MAX_ACTIVE_RECORDS,
    MAX_DELETE_RECORDS,
    TemporaryMemory,
    TemporaryMemoryRecord,
)

_LOGGER = logging.getLogger(__name__)
_INSTALLED = False
_ACTIVE_OWNER_SCOPE_ID: ContextVar[str | None] = ContextVar(
    "extended_openai_temporary_memory_owner_scope_id", default=None
)


def _valid_owner_scope_id(value: object) -> str | None:
    """Return one canonical retained owner, or None when it is not valid."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate == SHARED_HOUSEHOLD_SCOPE_ID:
        return candidate
    if candidate.startswith("user:"):
        user_id = candidate[5:]
        if user_id and len(candidate) <= 128:
            return candidate
    return None


def _require_owner_scope_id(value: object = None) -> str:
    """Resolve and validate the retained owner for one operation."""
    owner = value if value is not None else _ACTIVE_OWNER_SCOPE_ID.get()
    valid = _valid_owner_scope_id(owner)
    if valid is None:
        raise ValueError(
            "Temporary Memory requires a resolved Personal or Shared owner"
        )
    return valid


def _owner_from_resolved_scope(scope: object) -> str | None:
    """Translate only the established data-scope contract into a retained owner."""
    if scope is None:
        return None
    scope_type = getattr(scope, "scope_type", None)
    if scope_type == "user":
        user_id = getattr(scope, "user_id", None)
        return _valid_owner_scope_id(f"user:{user_id}") if user_id else None
    if scope_type == "shared":
        return SHARED_HOUSEHOLD_SCOPE_ID
    return None


def _normalize_record_owner(
    record: TemporaryMemoryRecord,
) -> TemporaryMemoryRecord | None:
    """Preserve proven ownership and conservatively migrate canonical legacy data."""
    if record.owner_scope_id is not None:
        owner = _valid_owner_scope_id(record.owner_scope_id)
        if owner is None:
            return None
        return (
            record
            if owner == record.owner_scope_id
            else replace(record, owner_scope_id=owner)
        )

    # Pre-owner records whose scope itself was a canonical retained scope are safe
    # to migrate. Device/conversation continuity keys cannot establish ownership.
    inferred = _valid_owner_scope_id(record.scope_id)
    if inferred is None:
        return None
    return replace(record, owner_scope_id=inferred)


def _owner_record_sort_key(record: TemporaryMemoryRecord) -> tuple[Any, ...]:
    """Keep the newest bounded records deterministically during startup recovery."""

    def parsed(value: str) -> float:
        parsed_value = dt_util.parse_datetime(value)
        return parsed_value.timestamp() if parsed_value is not None else 0.0

    return (
        parsed(record.updated_at),
        parsed(record.expires_at),
        parsed(record.created_at),
        record.memory_id,
    )


async def _normalize_loaded_records(manager: TemporaryMemory) -> None:
    """Persist canonical ownership and enforce the existing runtime ceiling."""
    async with manager._lock:
        original = manager._records
        normalized: list[TemporaryMemoryRecord] = []
        invalid = 0
        for record in original.values():
            migrated = _normalize_record_owner(record)
            if migrated is None:
                invalid += 1
                continue
            normalized.append(migrated)

        overflow = max(0, len(normalized) - MAX_ACTIVE_RECORDS)
        if overflow:
            normalized = sorted(normalized, key=_owner_record_sort_key, reverse=True)[
                :MAX_ACTIVE_RECORDS
            ]

        replacement = {record.memory_id: record for record in normalized}
        if replacement == original:
            return

        manager._records = replacement
        try:
            await manager._async_save_locked()
        except BaseException:
            manager._records = original
            raise

        manager_any: Any = manager
        manager_any.invalid_owners_pruned = (
            getattr(manager, "invalid_owners_pruned", 0) + invalid
        )
        manager_any.overflow_pruned = getattr(manager, "overflow_pruned", 0) + overflow
        if invalid:
            _LOGGER.warning(
                "Removed %s Temporary Memory record(s) without a valid retained owner",
                invalid,
            )
        if overflow:
            _LOGGER.warning(
                "Temporary Memory exceeded the %s-record ceiling at startup; "
                "kept the newest %s records and removed %s",
                MAX_ACTIVE_RECORDS,
                MAX_ACTIVE_RECORDS,
                overflow,
            )


def _records_for_owner(
    manager: TemporaryMemory, owner_scope_id: str
) -> list[TemporaryMemoryRecord]:
    """Return every active record belonging to an owner without injection limits."""
    owner = _require_owner_scope_id(owner_scope_id)
    now = dt_util.utcnow()
    records = [
        record
        for record in manager._records.values()
        if record.owner_scope_id == owner
        and (dt_util.parse_datetime(record.expires_at) or now) > now
    ]
    records.sort(key=lambda record: (record.expires_at, record.memory_id))
    return records


def _install_manager_contract() -> None:
    """Harden effective TemporaryMemory methods after persistence/read wrappers."""
    from . import temporary_memory as temporary_module

    temporary_any: Any = temporary_module
    memory_cls: Any = TemporaryMemory

    # Continuity remains useful record metadata, but never authorizes retained data.
    def matches_owner(
        record: TemporaryMemoryRecord,
        _scope_id: str,
        owner_scope_id: str | None,
    ) -> bool:
        owner = _valid_owner_scope_id(owner_scope_id)
        return owner is not None and record.owner_scope_id == owner

    def clean_owner(owner_scope_id: str | None) -> str:
        return _require_owner_scope_id(owner_scope_id)

    temporary_any._matches_owner = matches_owner
    temporary_any._clean_owner_scope_id = clean_owner

    current_initialize = memory_cls.async_initialize

    @wraps(current_initialize)
    async def async_initialize(
        manager: TemporaryMemory, *args: Any, **kwargs: Any
    ) -> None:
        await current_initialize(manager, *args, **kwargs)
        await _normalize_loaded_records(manager)

    memory_cls.async_initialize = async_initialize

    def wrap_owner_method(name: str, *, read: bool = False) -> None:
        current = getattr(memory_cls, name)

        @wraps(current)
        async def wrapped(
            manager: TemporaryMemory,
            *args: Any,
            owner_scope_id: str | None = None,
            **kwargs: Any,
        ) -> Any:
            owner = _valid_owner_scope_id(
                owner_scope_id
                if owner_scope_id is not None
                else _ACTIVE_OWNER_SCOPE_ID.get()
            )
            if owner is None:
                if read:
                    return []
                raise ValueError(
                    "Temporary Memory requires a resolved Personal or Shared owner"
                )
            return await current(manager, *args, owner_scope_id=owner, **kwargs)

        setattr(memory_cls, name, wrapped)

    wrap_owner_method("async_active", read=True)
    wrap_owner_method("async_add")
    wrap_owner_method("async_update")
    wrap_owner_method("async_delete")

    current_snapshot = memory_cls.async_active_snapshot

    @wraps(current_snapshot)
    async def async_active_snapshot(
        manager: TemporaryMemory,
        scope_id: str,
        owner_scope_id: str | None = None,
    ) -> list[TemporaryMemoryRecord]:
        owner = _valid_owner_scope_id(
            owner_scope_id
            if owner_scope_id is not None
            else _ACTIVE_OWNER_SCOPE_ID.get()
        )
        if owner is None:
            return []
        return cast(
            list[TemporaryMemoryRecord],
            await current_snapshot(manager, scope_id, owner_scope_id=owner),
        )

    memory_cls.async_active_snapshot = async_active_snapshot

    async def async_list_owned(
        manager: TemporaryMemory, owner_scope_id: str
    ) -> list[TemporaryMemoryRecord]:
        await manager.async_initialize()
        async with manager._lock:
            await manager._async_prune_locked()
            return list(_records_for_owner(manager, owner_scope_id))

    async def async_update_owned(
        manager: TemporaryMemory,
        owner_scope_id: str,
        memory_id: str,
        content: str | None,
        expires_at: str | None,
        category: str | None,
    ) -> TemporaryMemoryRecord:
        owner = _require_owner_scope_id(owner_scope_id)
        return await manager.async_update(
            owner,
            memory_id,
            content,
            expires_at,
            category,
            owner_scope_id=owner,
        )

    async def async_delete_owned(
        manager: TemporaryMemory,
        owner_scope_id: str,
        memory_ids: list[str],
    ) -> int:
        owner = _require_owner_scope_id(owner_scope_id)
        if not memory_ids or len(memory_ids) > MAX_DELETE_RECORDS:
            raise ValueError(f"memory_ids must contain 1 to {MAX_DELETE_RECORDS} IDs")
        return await manager.async_delete(owner, memory_ids, owner_scope_id=owner)

    def owner_counts(manager: TemporaryMemory) -> dict[str, int]:
        now = dt_util.utcnow()
        counts: Counter[str] = Counter()
        for record in manager._records.values():
            owner = _valid_owner_scope_id(record.owner_scope_id)
            expiry = dt_util.parse_datetime(record.expires_at)
            if owner is not None and expiry is not None and expiry > now:
                counts[owner] += 1
        return dict(counts)

    memory_cls.async_list_owned = async_list_owned
    memory_cls.async_update_owned = async_update_owned
    memory_cls.async_delete_owned = async_delete_owned
    memory_cls.owner_counts = owner_counts

    # Management listing deliberately ignores model-injection record/character caps.
    async def async_list(
        manager: TemporaryMemory,
        _scope_id: str | None = None,
        owner_scope_id: str | None = None,
    ) -> list[TemporaryMemoryRecord]:
        owner = _require_owner_scope_id(owner_scope_id)
        return await async_list_owned(manager, owner)

    async def async_list_all(
        manager: TemporaryMemory,
        owner_scope_id: str | None = None,
    ) -> list[TemporaryMemoryRecord]:
        owner = _require_owner_scope_id(owner_scope_id)
        return await async_list_owned(manager, owner)

    memory_cls.async_list = async_list
    memory_cls.async_list_all = async_list_all

    current_stats = memory_cls.stats

    @wraps(current_stats)
    def stats(manager: TemporaryMemory) -> dict[str, int]:
        result = cast(dict[str, int], current_stats(manager))
        result["invalid_owner_records_pruned"] = getattr(
            manager, "invalid_owners_pruned", 0
        )
        result["startup_overflow_records_pruned"] = getattr(
            manager, "overflow_pruned", 0
        )
        return result

    memory_cls.stats = stats

    current_validate_backup = memory_cls.validate_backup_data

    def validate_backup_data(payload: Any) -> list[TemporaryMemoryRecord]:
        records = current_validate_backup(payload)
        return [
            migrated
            for record in records
            if (migrated := _normalize_record_owner(record)) is not None
        ]

    memory_cls.validate_backup_data = staticmethod(validate_backup_data)

    current_replace_backup = memory_cls.async_replace_backup

    @wraps(current_replace_backup)
    async def async_replace_backup(
        manager: TemporaryMemory, records: list[TemporaryMemoryRecord]
    ) -> None:
        normalized = [
            migrated
            for record in records
            if (migrated := _normalize_record_owner(record)) is not None
        ]
        if len(normalized) > MAX_ACTIVE_RECORDS:
            normalized = sorted(normalized, key=_owner_record_sort_key, reverse=True)[
                :MAX_ACTIVE_RECORDS
            ]
        await current_replace_backup(manager, normalized)

    memory_cls.async_replace_backup = async_replace_backup


def _install_snapshot_contract() -> None:
    """Require owner context on the cold/read-only snapshot path as well."""
    from . import management_ui, temporary_memory as temporary_module

    temporary_any: Any = temporary_module
    management: Any = management_ui
    current = temporary_any.async_read_temporary_memory_snapshot

    @wraps(current)
    async def read_snapshot(
        hass: Any,
        entry_id: str,
        subentry_id: str,
        scope_id: str,
        owner_scope_id: str | None = None,
    ) -> list[TemporaryMemoryRecord]:
        owner = _valid_owner_scope_id(
            owner_scope_id
            if owner_scope_id is not None
            else _ACTIVE_OWNER_SCOPE_ID.get()
        )
        if owner is None:
            return []
        return cast(
            list[TemporaryMemoryRecord],
            await current(
                hass,
                entry_id,
                subentry_id,
                scope_id,
                owner_scope_id=owner,
            ),
        )

    temporary_any.async_read_temporary_memory_snapshot = read_snapshot

    # Replace imported aliases captured before this effective-path wrapper installs.
    if getattr(management, "async_read_temporary_memory_snapshot", None) is current:
        management.async_read_temporary_memory_snapshot = read_snapshot


def install_temporary_memory_ownership() -> None:
    """Install owner-safe Temporary Memory after existing effective wrappers."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_manager_contract()
    _install_snapshot_contract()
    _INSTALLED = True
