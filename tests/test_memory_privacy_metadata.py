"""Content-agnostic metadata acceptance preserves independent provenance guards."""

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


@pytest.mark.parametrize("operation", ["add", "propose", "update"])
@pytest.mark.parametrize("field", ["category", "subject", "key"])
@pytest.mark.parametrize("source", ["implicit", "explicit"])
async def test_persistent_metadata_secrets_are_retained(
    operation, field, source
):
    storage = FakeStorage()
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    ident = None
    if operation == "update":
        created = await memory.async_add("owner", "Prefers tea", "general", source)
        ident = created["memory"]["memory_id"]
    values = {field: "sk-syntheticauditsecret123"}
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
    records = await memory.async_list("owner")
    assert getattr(records[0], field) == values[field]
    fresh = PersistentMemory(storage)
    await fresh.async_initialize()
    assert await fresh.async_list("owner") == records


@pytest.mark.parametrize("operation", ["add", "propose", "update"])
@pytest.mark.parametrize("field", ["category", "subject"])
@pytest.mark.parametrize("source", ["implicit", "explicit"])
async def test_sensitive_metadata_is_permitted_for_both_sources(
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

    await write()
    assert getattr((await memory.async_list("owner"))[0], field) == values[field]



@pytest.mark.parametrize("operation", ["add", "update"])
@pytest.mark.parametrize("source", ["automatic", "manual"])
async def test_temporary_category_secrets_are_retained(
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
    [record] = await memory.async_list_owned("user:owner")
    assert record.category == "password: synthetic-audit-secret"
    assert record.source == source


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
