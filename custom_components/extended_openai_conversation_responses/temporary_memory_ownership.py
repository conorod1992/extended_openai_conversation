"""Ownership boundaries for retained Temporary Memory."""

from __future__ import annotations

import asyncio
from collections import Counter
from contextvars import ContextVar
from dataclasses import replace
from functools import wraps
import logging
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .scope import SHARED_HOUSEHOLD_SCOPE_ID
from .temporary_memory import (
    MAX_ACTIVE_RECORDS,
    MAX_DELETE_RECORDS,
    TemporaryMemory,
    TemporaryMemoryRecord,
    temporary_memory_as_dict,
)

_LOGGER = logging.getLogger(__name__)
_INSTALLED = False
_ACTIVE_OWNER_SCOPE_ID: ContextVar[str | None] = ContextVar(
    "extended_openai_temporary_memory_owner_scope_id", default=None
)
_FRONTEND_MODULE = "management-temporary-memory.js"


def _valid_owner_scope_id(value: Any) -> str | None:
    """Return one canonical retained owner, or None when it is not valid."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value == SHARED_HOUSEHOLD_SCOPE_ID:
        return value
    if value.startswith("user:"):
        user_id = value[5:]
        if user_id and len(value) <= 128:
            return value
    return None


def _require_owner_scope_id(value: Any = None) -> str:
    """Resolve and validate the retained owner for one operation."""
    owner = value if value is not None else _ACTIVE_OWNER_SCOPE_ID.get()
    valid = _valid_owner_scope_id(owner)
    if valid is None:
        raise ValueError(
            "Temporary Memory requires a resolved Personal or Shared owner"
        )
    return valid


def _owner_from_resolved_scope(scope: Any) -> str | None:
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
    """Preserve proven ownership and conservatively migrate only canonical legacy data."""
    owner = _valid_owner_scope_id(record.owner_scope_id)
    if owner is not None:
        return record if owner == record.owner_scope_id else replace(
            record, owner_scope_id=owner
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
            normalized = sorted(
                normalized, key=_owner_record_sort_key, reverse=True
            )[:MAX_ACTIVE_RECORDS]

        replacement = {record.memory_id: record for record in normalized}
        changed = replacement != original
        if not changed:
            return

        manager._records = replacement
        try:
            await manager._async_save_locked()
        except BaseException:
            manager._records = original
            raise

        manager.invalid_owners_pruned = (
            getattr(manager, "invalid_owners_pruned", 0) + invalid
        )
        manager.overflow_pruned = getattr(manager, "overflow_pruned", 0) + overflow
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
    """Harden the effective TemporaryMemory methods after persistence/read wrappers."""
    from . import temporary_memory as temporary_module

    # Make owner matching authoritative. Continuity remains useful record metadata,
    # but is not an authorization or ownership boundary.
    def matches_owner(
        record: TemporaryMemoryRecord,
        _scope_id: str,
        owner_scope_id: str | None,
    ) -> bool:
        owner = _valid_owner_scope_id(owner_scope_id)
        return owner is not None and record.owner_scope_id == owner

    def clean_owner(owner_scope_id: str | None) -> str:
        return _require_owner_scope_id(owner_scope_id)

    temporary_module._matches_owner = matches_owner
    temporary_module._clean_owner_scope_id = clean_owner

    current_initialize = TemporaryMemory.async_initialize

    @wraps(current_initialize)
    async def async_initialize(manager: TemporaryMemory, *args: Any, **kwargs: Any) -> Any:
        result = await current_initialize(manager, *args, **kwargs)
        await _normalize_loaded_records(manager)
        return result

    TemporaryMemory.async_initialize = async_initialize  # type: ignore[method-assign]

    def wrap_owner_method(name: str, *, read: bool = False) -> None:
        current = getattr(TemporaryMemory, name)

        @wraps(current)
        async def wrapped(
            manager: TemporaryMemory,
            *args: Any,
            owner_scope_id: str | None = None,
            **kwargs: Any,
        ) -> Any:
            owner = _valid_owner_scope_id(
                owner_scope_id if owner_scope_id is not None else _ACTIVE_OWNER_SCOPE_ID.get()
            )
            if owner is None:
                if read:
                    return []
                raise ValueError(
                    "Temporary Memory requires a resolved Personal or Shared owner"
                )
            return await current(
                manager, *args, owner_scope_id=owner, **kwargs
            )

        setattr(TemporaryMemory, name, wrapped)

    wrap_owner_method("async_active", read=True)
    wrap_owner_method("async_add")
    wrap_owner_method("async_update")
    wrap_owner_method("async_delete")

    current_snapshot = TemporaryMemory.async_active_snapshot

    @wraps(current_snapshot)
    def async_active_snapshot(
        manager: TemporaryMemory,
        scope_id: str,
        owner_scope_id: str | None = None,
    ) -> list[TemporaryMemoryRecord]:
        owner = _valid_owner_scope_id(
            owner_scope_id if owner_scope_id is not None else _ACTIVE_OWNER_SCOPE_ID.get()
        )
        if owner is None:
            return []
        return current_snapshot(manager, scope_id, owner)

    TemporaryMemory.async_active_snapshot = async_active_snapshot  # type: ignore[method-assign]

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
            raise ValueError(
                f"memory_ids must contain 1 to {MAX_DELETE_RECORDS} IDs"
            )
        return await manager.async_delete(
            owner, memory_ids, owner_scope_id=owner
        )

    def owner_counts(manager: TemporaryMemory) -> dict[str, int]:
        now = dt_util.utcnow()
        counts: Counter[str] = Counter()
        for record in manager._records.values():
            owner = _valid_owner_scope_id(record.owner_scope_id)
            expiry = dt_util.parse_datetime(record.expires_at)
            if owner is not None and expiry is not None and expiry > now:
                counts[owner] += 1
        return dict(counts)

    TemporaryMemory.async_list_owned = async_list_owned  # type: ignore[attr-defined]
    TemporaryMemory.async_update_owned = async_update_owned  # type: ignore[attr-defined]
    TemporaryMemory.async_delete_owned = async_delete_owned  # type: ignore[attr-defined]
    TemporaryMemory.owner_counts = owner_counts  # type: ignore[attr-defined]

    # Existing list methods are management-facing. Make them owner-scoped and
    # deliberately uncapped instead of reusing the model injection selector.
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

    TemporaryMemory.async_list = async_list  # type: ignore[method-assign]
    TemporaryMemory.async_list_all = async_list_all  # type: ignore[method-assign]

    current_stats = TemporaryMemory.stats

    @wraps(current_stats)
    def stats(manager: TemporaryMemory) -> dict[str, int]:
        result = current_stats(manager)
        result["invalid_owner_records_pruned"] = getattr(
            manager, "invalid_owners_pruned", 0
        )
        result["startup_overflow_records_pruned"] = getattr(
            manager, "overflow_pruned", 0
        )
        return result

    TemporaryMemory.stats = stats  # type: ignore[method-assign]

    current_validate_backup = TemporaryMemory.validate_backup_data

    def validate_backup_data(payload: Any) -> list[TemporaryMemoryRecord]:
        records = current_validate_backup(payload)
        return [
            migrated
            for record in records
            if (migrated := _normalize_record_owner(record)) is not None
        ]

    TemporaryMemory.validate_backup_data = staticmethod(validate_backup_data)  # type: ignore[method-assign]

    current_replace_backup = TemporaryMemory.async_replace_backup

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
            normalized = sorted(
                normalized, key=_owner_record_sort_key, reverse=True
            )[:MAX_ACTIVE_RECORDS]
        await current_replace_backup(manager, normalized)

    TemporaryMemory.async_replace_backup = async_replace_backup  # type: ignore[method-assign]


def _install_snapshot_contract() -> None:
    """Require owner context on the cold/read-only snapshot path as well."""
    from . import temporary_memory as temporary_module

    current = temporary_module.async_read_temporary_memory_snapshot

    @wraps(current)
    async def read_snapshot(
        hass: Any,
        entry_id: str,
        subentry_id: str,
        scope_id: str,
        owner_scope_id: str | None = None,
    ) -> list[TemporaryMemoryRecord]:
        owner = _valid_owner_scope_id(
            owner_scope_id if owner_scope_id is not None else _ACTIVE_OWNER_SCOPE_ID.get()
        )
        if owner is None:
            return []
        return await current(
            hass, entry_id, subentry_id, scope_id, owner_scope_id=owner
        )

    temporary_module.async_read_temporary_memory_snapshot = read_snapshot

    # Replace imported aliases that the management preview module captured.
    from . import management_ui

    if getattr(management_ui, "async_read_temporary_memory_snapshot", None) is current:
        management_ui.async_read_temporary_memory_snapshot = read_snapshot


def _install_conversation_contract() -> None:
    """Bind the resolved Personal/Shared owner around effective live runtime calls."""
    from . import conversation

    current_retrieve = conversation.ExtendedOpenAIAgentEntity._async_retrieve_temporary_memories
    current_tool = conversation.ExtendedOpenAIAgentEntity._async_execute_temporary_memory_tool

    def request_owner() -> str | None:
        if conversation._ACTIVE_TEMPORARY_SCOPE.get() is None:
            return None
        return _owner_from_resolved_scope(conversation._ACTIVE_SCOPE.get())

    @wraps(current_retrieve)
    async def retrieve(entity: Any) -> list[TemporaryMemoryRecord]:
        owner = request_owner()
        if owner is None:
            return []
        token = _ACTIVE_OWNER_SCOPE_ID.set(owner)
        try:
            return await current_retrieve(entity)
        finally:
            _ACTIVE_OWNER_SCOPE_ID.reset(token)

    @wraps(current_tool)
    async def execute_tool(
        entity: Any, operation: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        owner = request_owner()
        if owner is None:
            raise RuntimeError(
                "temporary memory is unavailable for this retained-data scope"
            )
        token = _ACTIVE_OWNER_SCOPE_ID.set(owner)
        try:
            return await current_tool(entity, operation, arguments)
        finally:
            _ACTIVE_OWNER_SCOPE_ID.reset(token)

    conversation.ExtendedOpenAIAgentEntity._async_retrieve_temporary_memories = (  # type: ignore[method-assign]
        retrieve
    )
    conversation.ExtendedOpenAIAgentEntity._async_execute_temporary_memory_tool = (  # type: ignore[method-assign]
        execute_tool
    )


def _install_management_contract() -> None:
    """Expose owner-scoped complete Temporary Memory management."""
    from . import management_ui

    modules = tuple(
        dict.fromkeys((*management_ui.MANAGEMENT_FRONTEND_MODULES, _FRONTEND_MODULE))
    )
    management_ui.MANAGEMENT_FRONTEND_MODULES = modules

    current_preview = management_ui._async_preview_effective_request

    @wraps(current_preview)
    async def preview(
        hass: Any,
        entry: Any,
        subentry: Any,
        candidate: dict[str, Any],
        user_id: str,
    ) -> dict[str, Any]:
        token = _ACTIVE_OWNER_SCOPE_ID.set(f"user:{user_id}")
        try:
            return await current_preview(hass, entry, subentry, candidate, user_id)
        finally:
            _ACTIVE_OWNER_SCOPE_ID.reset(token)

    management_ui._async_preview_effective_request = preview
    if getattr(management_ui, "_async_preview_effective_prompt", None) is current_preview:
        management_ui._async_preview_effective_prompt = preview

    current_command = management_ui.async_management_command

    async def command(
        hass: Any,
        user_id: str,
        is_admin: bool,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        section = message.get("section")
        action = message.get("action")
        if section == "memories" and action in {
            "temporary_list",
            "temporary_update",
            "temporary_delete",
        }:
            entry, subentry = management_ui.entry_and_agent(
                hass, message.get("entry_id"), message.get("subentry_id")
            )
            selected = management_ui._selected_scope(
                hass, user_id, is_admin, message.get("scope_id")
            )
            owner = _valid_owner_scope_id(selected.scope_id)
            if owner is None or selected.scope_type not in {"user", "shared"}:
                raise HomeAssistantError(
                    "Temporary Memory can only be managed in Personal or Shared scopes"
                )
            manager = await management_ui.async_get_temporary_memory(
                hass, entry.entry_id, subentry.subentry_id
            )
            if action == "temporary_list":
                records = await manager.async_list_owned(owner)
                return {
                    "records": [
                        temporary_memory_as_dict(record, include_scope=True)
                        | {"owner_scope_id": record.owner_scope_id}
                        for record in records
                    ],
                    "owner_scope_id": owner,
                    "stats": manager.stats(),
                }
            memory_id = message.get("memory_id")
            if not isinstance(memory_id, str) or not memory_id:
                raise HomeAssistantError("memory_id is required")
            if action == "temporary_delete":
                deleted = await manager.async_delete_owned(owner, [memory_id])
                return {"deleted": deleted}
            content = message.get("content")
            category = message.get("category")
            expires_at = message.get("expires_at")
            if (
                content is not None and not isinstance(content, str)
            ) or (
                category is not None and not isinstance(category, str)
            ) or (
                expires_at is not None and not isinstance(expires_at, str)
            ):
                raise HomeAssistantError(
                    "content, category, and expires_at must be strings when supplied"
                )
            if content is None and category is None and expires_at is None:
                raise HomeAssistantError("at least one Temporary Memory field is required")
            try:
                record = await manager.async_update_owned(
                    owner, memory_id, content, expires_at, category
                )
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
            return {
                "memory": temporary_memory_as_dict(record, include_scope=True)
                | {"owner_scope_id": record.owner_scope_id}
            }

        result = await current_command(hass, user_id, is_admin, message)
        if action == "scopes" or action == "agents":
            scopes = result.get("scopes") if isinstance(result, dict) else None
            if isinstance(scopes, list):
                entry_id = message.get("entry_id")
                subentry_id = message.get("subentry_id")
                try:
                    entry, subentry = management_ui.entry_and_agent(
                        hass, entry_id, subentry_id
                    )
                except HomeAssistantError:
                    return result
                manager = await management_ui.async_get_temporary_memory(
                    hass, entry.entry_id, subentry.subentry_id
                )
                counts = manager.owner_counts()
                for scope in scopes:
                    if isinstance(scope, dict):
                        scope["temporary_memory_count"] = counts.get(
                            str(scope.get("scope_id") or ""), 0
                        )
        return result

    management_ui.async_management_command = command  # type: ignore[assignment]


def install_temporary_memory_ownership() -> None:
    """Install owner-safe Temporary Memory after existing effective wrappers."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_manager_contract()
    _install_snapshot_contract()
    _install_conversation_contract()
    _install_management_contract()
    _INSTALLED = True
