"""Privacy invariants apply to every retained text field and operation."""

from copy import deepcopy
from datetime import timedelta

import pytest

from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
)
from homeassistant.util import dt as dt_util
from tests.test_memory import FakeStorage


@pytest.mark.parametrize("operation", ["add", "upsert", "update"])
@pytest.mark.parametrize("field", ["category", "subject", "key"])
@pytest.mark.parametrize("source", ["implicit", "explicit"])
async def test_persistent_metadata_secrets_are_rejected_without_mutation(
    operation, field, source
):
    storage = FakeStorage()
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    ident = None
    if operation == "update":
        created = await memory.async_add("owner", "Prefers tea", "general", source)
        ident = created["memory"]["memory_id"]
    before = deepcopy(storage.data)
    values = {field: "sk-syntheticauditsecret123"}
    with pytest.raises(ValueError, match="secret"):
        if operation == "update":
            await memory.async_update("owner", ident, source=source, **values)
        else:
            args = {
                "content": "Prefers tea",
                "category": "general",
                "source": source,
                **values,
            }
            await getattr(memory, f"async_{operation}")("owner", **args)
    assert storage.data == before
    records = await memory.async_list("owner")
    assert all("syntheticauditsecret" not in str(record) for record in records)


@pytest.mark.parametrize("operation", ["add", "upsert", "update"])
@pytest.mark.parametrize("field", ["category", "subject"])
@pytest.mark.parametrize("source", ["implicit", "explicit"])
async def test_sensitive_metadata_requires_current_explicit_operation(
    operation, field, source
):
    storage = FakeStorage()
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    ident = None
    if operation == "update":
        created = await memory.async_add("owner", "Prefers tea", "general", source)
        ident = created["memory"]["memory_id"]
    values = {field: "medical diagnosis: synthetic-condition"}

    async def write():
        if operation == "update":
            return await memory.async_update("owner", ident, source=source, **values)
        return await getattr(memory, f"async_{operation}")(
            "owner",
            **{
                "content": "Prefers tea",
                "category": "general",
                "source": source,
                **values,
            },
        )

    if source == "implicit":
        before = deepcopy(storage.data)
        with pytest.raises(ValueError, match="explicit user request"):
            await write()
        assert storage.data == before
    else:
        await write()
        assert getattr((await memory.async_list("owner"))[0], field) == values[field]


@pytest.mark.parametrize("operation", ["add", "update"])
@pytest.mark.parametrize("source", ["automatic", "manual"])
async def test_temporary_category_secrets_are_rejected_without_mutation(
    operation, source
):
    storage = FakeStorage()
    memory = TemporaryMemory(storage)
    await memory.async_initialize()
    expiry = (dt_util.utcnow() + timedelta(hours=1)).isoformat()
    ident = None
    if operation == "update":
        created = await memory.async_add(
            "user:owner",
            "Prefers tea",
            expiry,
            owner_scope_id="user:owner",
            source=source,
        )
        ident = created["memory"]["memory_id"]
    before = deepcopy(storage.data)
    with pytest.raises(ValueError, match="secret"):
        if operation == "update":
            await memory.async_update(
                "user:owner",
                ident,
                None,
                None,
                "password: synthetic-audit-secret",
                owner_scope_id="user:owner",
                source=source,
            )
        else:
            await memory.async_add(
                "user:owner",
                "Prefers tea",
                expiry,
                "password: synthetic-audit-secret",
                owner_scope_id="user:owner",
                source=source,
            )
    assert storage.data == before


async def test_implicit_update_cannot_reclassify_an_explicit_fact():
    storage = FakeStorage()
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    created = await memory.async_add("owner", "Prefers tea", "preference", "explicit")
    before = deepcopy(storage.data)
    with pytest.raises(ValueError, match="explicit"):
        await memory.async_update(
            "owner", created["memory"]["memory_id"], "Prefers coffee", source="implicit"
        )
    assert storage.data == before
