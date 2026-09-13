"""Residual lifecycle and validation coverage for Temporary Memory."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import temporary_memory
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    MAX_ACTIVE_RECORDS,
    MAX_CONTENT_LENGTH,
    MAX_INJECT_CHARACTERS,
    TemporaryMemory,
    TemporaryMemoryRecord,
)
from homeassistant.util import dt as dt_util


class Storage:
    """Small detached storage double."""

    def __init__(self, data: Any = None) -> None:
        self.data = deepcopy(data)
        self.save_count = 0

    async def async_load(self) -> Any:
        return deepcopy(self.data)

    async def async_save(self, data: Any) -> None:
        self.data = deepcopy(data)
        self.save_count += 1


def _future(*, days: int = 0, hours: int = 1) -> str:
    return (dt_util.utcnow() + timedelta(days=days, hours=hours)).isoformat()


def _record(
    memory_id: str = "memory-1",
    *,
    content: str = "A useful temporary fact",
    scope_id: str = "conversation:kitchen",
    owner_scope_id: str | None = "user:alice",
    expires_at: str | None = None,
) -> dict[str, Any]:
    now = dt_util.utcnow().isoformat()
    return {
        "memory_id": memory_id,
        "scope_id": scope_id,
        "owner_scope_id": owner_scope_id,
        "content": content,
        "category": "general",
        "source": "automatic",
        "expires_at": expires_at or _future(),
        "created_at": now,
        "updated_at": now,
    }


def _model_record(
    memory_id: str, content: str, *, updated_offset: int = 0
) -> TemporaryMemoryRecord:
    raw = _record(memory_id, content=content)
    raw["updated_at"] = (
        dt_util.utcnow() + timedelta(seconds=updated_offset)
    ).isoformat()
    return TemporaryMemoryRecord(**raw)


async def test_initialize_skips_malformed_records_and_keeps_valid_data() -> None:
    storage = Storage({"records": [None, {"memory_id": "broken"}, _record()]})
    manager = TemporaryMemory(storage)  # type: ignore[arg-type]

    await manager.async_initialize()

    assert [item.memory_id for item in manager._records.values()] == ["memory-1"]


async def test_add_coalesces_duplicate_for_same_owner_and_persists_update() -> None:
    storage = Storage()
    manager = TemporaryMemory(storage)  # type: ignore[arg-type]
    await manager.async_initialize()
    created = await manager.async_add(
        "conversation:first",
        "Parcel arrives tomorrow",
        _future(),
        "delivery",
        owner_scope_id="user:alice",
    )

    updated = await manager.async_add(
        "conversation:first",
        "PARCEL ARRIVES TOMORROW",
        _future(hours=2),
        "schedule",
        owner_scope_id="user:alice",
    )

    assert updated["status"] == "updated"
    assert updated["memory"]["memory_id"] == created["memory"]["memory_id"]
    assert updated["memory"]["category"] == "schedule"
    assert len(manager._records) == 1
    assert storage.save_count == 2


def test_injection_budget_skips_large_later_record_but_keeps_smaller_one() -> None:
    records = [
        _model_record("first", "a" * (MAX_INJECT_CHARACTERS - 5), updated_offset=3),
        _model_record("too-large", "b" * 10, updated_offset=2),
        _model_record("fits", "c" * 5, updated_offset=1),
    ]

    selected = TemporaryMemory.select_active_snapshot(
        records, "conversation:kitchen", owner_scope_id="user:alice"
    )

    assert [record.memory_id for record in selected] == ["first", "fits"]


async def test_delete_owned_records_deduplicates_ids_and_saves_once() -> None:
    storage = Storage({"records": [_record()]})
    manager = TemporaryMemory(storage)  # type: ignore[arg-type]
    await manager.async_initialize()

    deleted = await manager.async_delete(
        "conversation:kitchen",
        ["memory-1", "memory-1", "missing"],
        owner_scope_id="user:alice",
    )

    assert deleted == 1
    assert storage.data == {"records": []}
    assert storage.save_count == 1


def test_scope_counts_reports_records_by_continuity_scope() -> None:
    manager = TemporaryMemory(Storage())  # type: ignore[arg-type]
    manager._records = {
        "one": TemporaryMemoryRecord(**_record("one", scope_id="conversation:one")),
        "two": TemporaryMemoryRecord(**_record("two", scope_id="conversation:one")),
        "three": TemporaryMemoryRecord(**_record("three", scope_id="conversation:two")),
    }

    assert manager.scope_counts() == {"conversation:one": 2, "conversation:two": 1}


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (None, "incomplete or corrupted"),
        ({"unexpected": []}, "incomplete or corrupted"),
        ({"records": ()}, "count is invalid"),
        ({"records": [None]}, "record must be an object"),
        ({"records": [{"memory_id": "incomplete"}]}, "record is invalid"),
    ],
)
def test_backup_validation_rejects_invalid_container_shapes(payload, message) -> None:
    with pytest.raises(ValueError, match=message):
        TemporaryMemory.validate_backup_data(payload)


def test_backup_validation_rejects_too_many_records() -> None:
    with pytest.raises(ValueError, match="count is invalid"):
        TemporaryMemory.validate_backup_data(
            {
                "records": [
                    _record(str(index)) for index in range(MAX_ACTIVE_RECORDS + 1)
                ]
            }
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("content", 1, "fields must be strings"),
        ("owner_scope_id", 1, "owner must be a string"),
        ("memory_id", "", "metadata is invalid"),
        ("memory_id", "x" * 129, "metadata is invalid"),
        ("scope_id", "", "metadata is invalid"),
        ("scope_id", "x" * 129, "metadata is invalid"),
        ("owner_scope_id", "", "metadata is invalid"),
        ("owner_scope_id", "x" * 129, "metadata is invalid"),
        ("source", "explicit", "metadata is invalid"),
        ("content", "", "content must contain"),
        ("content", "x" * (MAX_CONTENT_LENGTH + 1), "content must contain"),
        ("category", "", "category must contain"),
        ("created_at", "invalid", "timestamp is invalid"),
        ("updated_at", "invalid", "timestamp is invalid"),
    ],
)
def test_backup_validation_rejects_invalid_record_fields(field, value, message) -> None:
    raw = _record()
    raw[field] = value

    with pytest.raises(ValueError, match=message):
        TemporaryMemory.validate_backup_data({"records": [raw]})


def test_backup_validation_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="metadata is invalid"):
        TemporaryMemory.validate_backup_data(
            {"records": [_record(), _record(content="Different fact")]}
        )


def test_backup_validation_drops_expired_record() -> None:
    expired = _record(expires_at=(dt_util.utcnow() - timedelta(seconds=1)).isoformat())

    assert TemporaryMemory.validate_backup_data({"records": [expired]}) == []


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("not-a-date", "ISO date-time"),
        ("2026-09-13T12:00:00", "include a timezone"),
    ],
)
def test_parse_expiry_requires_valid_aware_datetime(value, message) -> None:
    with pytest.raises(ValueError, match=message):
        temporary_memory._parse_expiry(value)


def test_future_expiry_rejects_past_and_overlong_retention() -> None:
    with pytest.raises(ValueError, match="must be in the future"):
        temporary_memory._parse_future_expiry(
            (dt_util.utcnow() - timedelta(seconds=1)).isoformat()
        )
    with pytest.raises(ValueError, match="longer than one year"):
        temporary_memory._parse_future_expiry(_future(days=367))


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (1, "must be a string"),
        ("   ", "must contain"),
        ("too long", "must contain"),
    ],
)
def test_clean_rejects_invalid_values(value, message) -> None:
    with pytest.raises(ValueError, match=message):
        temporary_memory._clean(value, 3, "field")


def test_serialization_optionally_includes_continuity_scope() -> None:
    record = TemporaryMemoryRecord(**_record())

    assert "scope_id" not in temporary_memory.temporary_memory_as_dict(record)
    assert (
        temporary_memory.temporary_memory_as_dict(record, include_scope=True)[
            "scope_id"
        ]
        == record.scope_id
    )


def test_store_factory_uses_private_atomic_non_loop_serialization(monkeypatch) -> None:
    captured = SimpleNamespace(args=None, kwargs=None)

    class FakeStore:
        def __init__(self, *args, **kwargs) -> None:
            captured.args = args
            captured.kwargs = kwargs

    monkeypatch.setattr(temporary_memory, "TemporaryMemoryStore", FakeStore)
    hass = SimpleNamespace()

    result = temporary_memory._temporary_memory_store(hass, "entry", "agent")

    assert isinstance(result, FakeStore)
    assert captured.args == (
        hass,
        temporary_memory.STORAGE_VERSION,
        f"{temporary_memory.STORAGE_KEY_PREFIX}.entry.agent",
    )
    assert captured.kwargs == {
        "private": True,
        "atomic_writes": True,
        "serialize_in_event_loop": False,
    }


async def test_cold_snapshot_skips_corruption_without_creating_manager(
    monkeypatch,
) -> None:
    store = Storage(
        {
            "records": [
                None,
                _record("bad-expiry", expires_at="invalid"),
                _record("other", owner_scope_id="user:bob"),
                _record("visible"),
            ]
        }
    )
    monkeypatch.setattr(
        temporary_memory,
        "_temporary_memory_store",
        lambda _hass, _entry, _agent: store,
    )
    hass = SimpleNamespace(data={})

    result = await temporary_memory.async_read_temporary_memory_snapshot(
        hass,
        "entry",
        "agent",
        "conversation:kitchen",
        owner_scope_id="user:alice",
    )

    assert [record.memory_id for record in result] == ["visible"]
    assert temporary_memory.get_loaded_temporary_memory(hass, "entry", "agent") is None
    assert store.save_count == 0


async def test_get_temporary_memory_caches_and_initializes_manager(monkeypatch) -> None:
    created = []

    class FakeManager:
        def __init__(self, store) -> None:
            self.store = store
            self.async_initialize = AsyncMock()
            created.append(self)

    store = object()
    monkeypatch.setattr(temporary_memory, "TemporaryMemory", FakeManager)
    monkeypatch.setattr(
        temporary_memory,
        "_temporary_memory_store",
        lambda _hass, _entry, _agent: store,
    )
    hass = SimpleNamespace(data={})

    first = await temporary_memory.async_get_temporary_memory(hass, "entry", "agent")
    second = await temporary_memory.async_get_temporary_memory(hass, "entry", "agent")

    assert first is second is created[0]
    assert len(created) == 1
    assert first.store is store
    assert first.async_initialize.await_count == 2
