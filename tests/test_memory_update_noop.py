"""Validated no-op updates do not rewrite authoritative facts."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.memory import memory_revision
from custom_components.extended_openai_conversation_responses.persistence_hardening import StorageRepairRequired
from tests.test_memory_manual_duplicate_metadata import DATE, seeded


@pytest.mark.parametrize("model_origin", [False, True])
@pytest.mark.parametrize("stored_source", ["explicit", "implicit"])
async def test_noop_preserves_record_indexes_cache_revision_and_skips_failed_store(model_origin, stored_source):
    memory, storage, [record] = await seeded({"source": stored_source, "key": "drink.preference", "subject": "Alice", "valid_from": DATE})
    provider = AsyncMock(return_value=[[1.0, 0.0]])
    memory.set_embedding_provider(provider, "model")
    await memory.async_prepare_hybrid(["alice"], "query")
    before = (dict(memory._key_index), dict(memory._embedding_cache), memory_revision(record))
    storage.async_save = AsyncMock(side_effect=OSError("disk unavailable"))
    result = await memory.async_update(
        "alice", record.memory_id, content=record.content, category=record.category,
        subject=record.subject, key=record.key, valid_from=record.valid_from,
        source=("implicit" if stored_source == "explicit" else "explicit") if model_origin else stored_source,
        expected_revision=memory_revision(record), model_origin=model_origin,
    )
    assert result is memory._memories[record.memory_id] and result == record
    assert (memory._key_index, memory._embedding_cache, memory_revision(result)) == before
    assert not memory._embedding_cache_dirty
    storage.async_save.assert_not_called()
    # A real change still reaches the failing store and rolls back.
    with pytest.raises(OSError):
        await memory.async_update("alice", record.memory_id, content="Likes coffee.")
    assert (await memory.async_list("alice"))[0] == record


@pytest.mark.parametrize("arguments,match", [
    ({"user_id": "bob"}, "not found"),
    ({"expected_revision": "0" * 64}, "changed since"),
    ({"content": ""}, "content"),
    ({"allow_explicit_edit": False}, "explicit memories"),
    ({"model_origin": True}, "requires expected_revision"),
    ({"clear_fields": ["source"]}, "clear_fields"),
])
async def test_noop_never_bypasses_ownership_validation_revision_or_explicit_guards(arguments, match):
    memory, storage, [record] = await seeded()
    saves = storage.save_count
    with pytest.raises(ValueError, match=match):
        await memory.async_update(**(dict(user_id="alice", memory_id=record.memory_id) | arguments))
    assert storage.save_count == saves


async def test_noop_never_bypasses_repair_barrier():
    memory, storage, [record] = await seeded()
    memory._repair_required = True
    saves = storage.save_count
    with pytest.raises(StorageRepairRequired):
        await memory.async_update("alice", record.memory_id)
    assert storage.save_count == saves


async def test_noop_still_persists_dirty_embedding_cache_without_authoritative_write():
    memory, storage, [record] = await seeded()
    cache = AsyncMock()
    cache.async_save.side_effect = OSError("cache unavailable")
    memory._embedding_cache_storage = cache
    memory.set_embedding_provider(AsyncMock(return_value=[[1.0, 0.0]]), "model")
    await memory.async_prepare_hybrid(["alice"], "query")
    assert memory._embedding_cache_dirty
    saves, before = storage.save_count, deepcopy(storage.data)
    await memory.async_update("alice", record.memory_id)
    assert cache.async_save.call_count == 1  # Still in the retry cooldown.
    memory._embedding_cache_retry_at = 0
    cache.async_save.side_effect = None
    await memory.async_update("alice", record.memory_id)
    assert cache.async_save.call_count == 2 and not memory._embedding_cache_dirty
    assert storage.save_count == saves and storage.data == before
