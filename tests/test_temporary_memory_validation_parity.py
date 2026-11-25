"""Temporary Memory validates record fields at every persistence boundary."""

from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.temporary_memory import (
    STORAGE_KEY_PREFIX,
    TemporaryMemory,
    TemporaryMemoryStore,
    async_get_temporary_memory,
    async_read_temporary_memory_snapshot,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from tests.test_temporary_memory import Storage, _record


@pytest.mark.parametrize("owner_field", ["omitted", "null", "canonical"])
async def test_v1_snapshot_is_read_only_until_guarded_owner_migration(
    hass, owner_field
):
    store = Store(hass, 1, f"{STORAGE_KEY_PREFIX}.entry.agent", private=True)
    record = _record(scope_id="user:alice", content="Visitors arrive tomorrow")
    if owner_field == "omitted":
        record.pop("owner_scope_id")
    elif owner_field == "null":
        record["owner_scope_id"] = None
    await store.async_save({"records": [record]})
    original = Path(store.path).read_bytes()
    snapshot = await async_read_temporary_memory_snapshot(
        hass, "entry", "agent", "user:alice", "user:alice"
    )
    assert [item.content for item in snapshot] == ["Visitors arrive tomorrow"]
    assert Path(store.path).read_bytes() == original

    memory = await async_get_temporary_memory(hass, "entry", "agent")
    assert [item.content for item in await memory.async_active("user:alice", "user:alice")] == [
        "Visitors arrive tomorrow"
    ]
    upgraded = Path(store.path).read_bytes()
    assert json.loads(upgraded)["version"] == 2
    with pytest.raises((HomeAssistantError, NotImplementedError)):
        await store.async_load()
    assert Path(store.path).read_bytes() == upgraded


async def test_damaged_generation_never_attempts_a_content_purge(hass, monkeypatch):
    store = TemporaryMemoryStore(hass, 1, "temporary.no-purge", private=True, atomic_writes=True)
    payload = {"records": [None, {**_record(), "content": "password: synthetic-private-value"}]}
    await store.async_save(payload)
    original = Path(store.path).read_bytes()
    manager = TemporaryMemory(store)
    monkeypatch.setattr(store, "async_save", AsyncMock(side_effect=OSError("write failed")))
    for _ in range(2):
        with pytest.raises(ValueError, match="needs repair"):
            await manager.async_initialize()
        assert not manager.initialized
        assert Path(store.path).read_bytes() == original
    store.async_save.assert_not_awaited()


@pytest.mark.parametrize(
    "damaged", [None, {"recoverable": "fact"}, {**_record(), "expires_at": "invalid"}]
)
@pytest.mark.parametrize("sensitive_sibling", [False, True])
async def test_malformed_generation_preserves_all_evidence_with_sensitive_siblings(
    hass, damaged, sensitive_sibling
):
    store = TemporaryMemoryStore(
        hass, 1, "temporary.malformed", private=True, atomic_writes=True
    )
    records = [_record("valid"), damaged]
    if sensitive_sibling:
        records.append(
            {**_record("private"), "content": "password: synthetic-private-value"}
        )
    await store.async_save({"records": records, "opaque": "preserve"})
    evidence = Path(store.path).read_bytes()
    for _ in range(2):
        manager = TemporaryMemory(store)
        with pytest.raises(ValueError, match="needs repair"):
            await manager.async_initialize()
        assert not manager.initialized
        persisted = json.loads(Path(store.path).read_text())["data"]
        assert persisted == {"records": records, "opaque": "preserve"}
        assert Path(store.path).read_bytes() == evidence
        with pytest.raises(ValueError, match="needs repair"):
            await manager._async_reconcile_failed_save()


@pytest.mark.parametrize("conflict", ["content", "owner", "expired"])
async def test_duplicate_ids_preserve_native_evidence_at_every_load(hass, conflict):
    store = TemporaryMemoryStore(
        hass, 1, "temporary.duplicates", private=True, atomic_writes=True
    )
    first = _record("duplicate")
    second = {**first, "content": "Different fact"}
    if conflict == "owner":
        second["owner_scope_id"] = "user:bob"
    elif conflict == "expired":
        first["expires_at"] = (dt_util.utcnow() - timedelta(days=1)).isoformat()
    payload = {"records": [first, second]}
    with pytest.raises(ValueError, match="metadata is invalid"):
        TemporaryMemory.validate_backup_data(payload)
    await store.async_save(payload)
    evidence = Path(store.path).read_bytes()
    for _ in range(2):
        manager = TemporaryMemory(store)
        with pytest.raises(ValueError, match="needs repair"):
            await manager.async_initialize()
        assert not manager.initialized
        with pytest.raises(ValueError, match="needs repair"):
            await manager.async_backup_data()
        assert Path(store.path).read_bytes() == evidence
    with pytest.raises(ValueError, match="needs repair"):
        await manager._async_reconcile_failed_save()
    assert Path(store.path).read_bytes() == evidence


@pytest.mark.parametrize(
    "field,value",
    [
        ("memory_id", ""),
        ("memory_id", "x" * 129),
        ("memory_id", 42),
        ("source", "unknown"),
        ("source", None),
        ("created_at", "invalid"),
        ("updated_at", "invalid"),
        ("created_at", None),
        ("expires_at", 42),
    ],
)
async def test_invalid_record_cannot_enter_startup_or_reconciled_state(field, value):
    valid = _record(memory_id="valid")
    damaged = {**valid, "memory_id": "damaged", field: value}
    with pytest.raises(ValueError):
        TemporaryMemory.validate_backup_data({"records": [damaged]})

    original = {"records": [valid, damaged]}
    storage = Storage(deepcopy(original))
    manager = TemporaryMemory(storage)
    with pytest.raises(ValueError, match="needs repair"):
        await manager.async_initialize()
    assert not manager.initialized
    assert storage.data == original

    # Failed-write readback must also reject this generation, allowing the
    # transaction helper to invalidate it instead of publishing partial data.
    with pytest.raises(ValueError, match="needs repair"):
        await manager._async_reconcile_failed_save()
    assert storage.data == original


async def test_invalid_metadata_preserves_native_store_even_with_expired_sibling(hass):
    store = TemporaryMemoryStore(
        hass, 1, "temporary.metadata", private=True, atomic_writes=True
    )
    expired = _record(
        "expired", expires_at=(dt_util.utcnow() - timedelta(days=1)).isoformat()
    )
    damaged = {**_record("damaged"), "updated_at": "invalid"}
    await store.async_save({"records": [expired, damaged]})
    original = Path(store.path).read_bytes()
    for _ in range(2):
        manager = TemporaryMemory(store)
        with pytest.raises(ValueError, match="needs repair"):
            await manager.async_initialize()
        assert not manager.initialized
        assert Path(store.path).read_bytes() == original
