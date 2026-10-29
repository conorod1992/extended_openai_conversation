"""Temporary Memory validates record fields at every persistence boundary."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
    TemporaryMemoryStore,
)
from homeassistant.util import dt as dt_util
from tests.test_temporary_memory import Storage, _record


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
