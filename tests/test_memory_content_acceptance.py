"""Content-agnostic Memory across writes, startup, restore and failed commits."""

from copy import deepcopy
from datetime import timedelta

import pytest

from custom_components.extended_openai_conversation_responses import temporary_memory
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
    memory_revision,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import TemporaryMemory
from homeassistant.util import dt as dt_util
from tests.test_memory import FakeStorage

# Synthetic examples only, including a Luhn-valid test card and example IBAN.
CONTENTS = [
    "The Wi-Fi password is on the fridge.",
    "The guest Wi-Fi password is Welcome123.",
    "The side gate PIN is 2468.",
    "The API key is sk-synthetic-memory-example123.",
    "The access token is synthetic-access-value.",
    "The card number is 4111 1111 1111 1111; CVV is 123.",
    "Bank account number: 12345678; routing number: 123456789; sort code: 12-34-56.",
    "IBAN: GB82 WEST 1234 5698 7654 32.",
    "The vet's phone number is +353 1 234 5678.",
    "My medical diagnosis is synthetic-condition X.",
    "My religion is synthetic-belief X; political affiliation is synthetic-party X.",
    "My sexual orientation is synthetic-example X.",
]
CATEGORY = "password: synthetic-category"
SUBJECT = "security code: synthetic-subject"
KEY = "sk-synthetic-memory-example123"


def expiry():
    return (dt_util.utcnow() + timedelta(hours=1)).isoformat()


@pytest.mark.parametrize("content", CONTENTS)
@pytest.mark.parametrize("source", ["implicit", "explicit"])
@pytest.mark.parametrize("operation", ["add", "upsert", "propose", "update"])
async def test_persistent_content_and_metadata_round_trip(content, source, operation):
    storage = FakeStorage()
    manager = PersistentMemory(storage)
    await manager.async_initialize()
    values = dict(content=content, category=CATEGORY, subject=SUBJECT, key=KEY)
    if operation == "update":
        made = await manager.async_add("alice", "Initial fact", "general", source)
        record = (await manager.async_list("alice"))[0]
        await manager.async_update(
            "alice", made["memory"]["memory_id"], **values,
            source=source, expected_revision=memory_revision(record), model_origin=True,
            allow_explicit_edit=True,
        )
    else:
        result = await getattr(manager, "async_" + operation)("alice", source=source, **values)
        assert result["status"] == "created"
    [record] = await manager.async_list("alice", CATEGORY)
    assert (record.content, record.category, record.subject, record.key) == (
        content, CATEGORY, SUBJECT, KEY,
    )
    assert await manager.async_list("bob") == []
    # A partial model update validates inherited metadata structurally, without a content veto.
    unchanged = await manager.async_update(
        "alice", record.memory_id, expected_revision=memory_revision(record),
        model_origin=True, allow_explicit_edit=True,
    )
    assert unchanged == record
    backup = await manager.async_backup_data()
    saves = storage.save_count
    fresh = PersistentMemory(storage)
    await fresh.async_initialize()
    assert await fresh.async_list("alice") == [record]
    assert storage.save_count == saves
    restored = PersistentMemory(FakeStorage())
    await restored.async_initialize()
    await restored.async_replace_backup(PersistentMemory.validate_backup_data(backup))
    assert await restored.async_backup_data() == backup


@pytest.mark.parametrize("content", CONTENTS)
@pytest.mark.parametrize("source", ["automatic", "manual"])
@pytest.mark.parametrize("operation", ["add", "update"])
async def test_temporary_content_and_metadata_round_trip(content, source, operation):
    storage = FakeStorage()
    manager = TemporaryMemory(storage)
    if operation == "add":
        result = await manager.async_add(
            "device:continuity", content, expiry(), CATEGORY,
            source=source, owner_scope_id="user:alice",
        )
        ident = result["memory"]["memory_id"]
    else:
        result = await manager.async_add(
            "device:continuity", "Initial fact", expiry(),
            source=source, owner_scope_id="user:alice",
        )
        ident = result["memory"]["memory_id"]
        await manager.async_update(
            "device:new-continuity", ident, content, None, CATEGORY,
            source=source, owner_scope_id="user:alice",
        )
    [record] = await manager.async_active("device:other", owner_scope_id="user:alice")
    assert (record.content, record.category, record.source) == (content, CATEGORY, source)
    partial = await manager.async_update(
        "device:other", ident, None, None, None, source=source, owner_scope_id="user:alice",
    )
    assert partial.content == content and partial.category == CATEGORY
    assert await manager.async_active("device:continuity", owner_scope_id="user:bob") == []
    backup = await manager.async_backup_data()
    saves = storage.save_count
    fresh = TemporaryMemory(storage)
    await fresh.async_initialize()
    assert await fresh.async_backup_data() == backup
    assert storage.save_count == saves
    restored = TemporaryMemory(FakeStorage())
    await restored.async_replace_backup(TemporaryMemory.validate_backup_data(backup))
    assert await restored.async_backup_data() == backup


async def test_sensitive_proposal_conflicts_remain_no_write_and_cannot_promote():
    storage = FakeStorage()
    manager = PersistentMemory(storage)
    await manager.async_initialize()
    await manager.async_add("alice", CONTENTS[1], CATEGORY, "implicit", key=KEY)
    before = deepcopy(storage.data)
    saves = storage.save_count
    conflict = await manager.async_propose("alice", CONTENTS[2], CATEGORY, "explicit", key=KEY)
    assert conflict["status"] == "needs_resolution"
    # An exact fact with a forged source label must not promote existing provenance.
    await manager.async_propose("alice", CONTENTS[1], CATEGORY, "explicit", key=KEY)
    assert storage.data == before and storage.save_count == saves
    assert (await manager.async_list("alice"))[0].source == "implicit"
    trusted = await manager.async_upsert("alice", CONTENTS[1], CATEGORY, "explicit", key=KEY)
    assert trusted["memory"]["source"] == "explicit"


@pytest.mark.parametrize("kind", ["persistent", "temporary"])
@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("reload_fails", [False, True])
async def test_sensitive_records_survive_transaction_settlement(kind, committed, reload_fails):
    class FaultStorage(FakeStorage):
        fault = False

        async def async_load(self):
            if self.fault and reload_fails:
                raise OSError("reload unavailable")
            return await super().async_load()

        async def async_save(self, data):
            if not self.fault or committed:
                await super().async_save(data)
            if self.fault:
                raise OSError("write acknowledgement unavailable")

    storage = FaultStorage()
    manager = PersistentMemory(storage) if kind == "persistent" else TemporaryMemory(storage)
    await manager.async_initialize()

    async def add(content):
        if kind == "persistent":
            return await manager.async_add("alice", content, CATEGORY, "implicit")
        return await manager.async_add("device:one", content, expiry(), CATEGORY, owner_scope_id="user:alice")

    await add(CONTENTS[1])
    storage.fault = True
    with pytest.raises(OSError):
        await add(CONTENTS[2])
    assert manager.initialized is not reload_fails
    storage.fault = False
    await manager.async_initialize()
    records = await manager.async_list("alice") if kind == "persistent" else await manager.async_list_owned("user:alice")
    assert {r.content for r in records} == {CONTENTS[1]} | ({CONTENTS[2]} if committed else set())
    durable = deepcopy(storage.data)
    fresh = PersistentMemory(storage) if kind == "persistent" else TemporaryMemory(storage)
    await fresh.async_initialize()
    assert await fresh.async_backup_data() == durable


@pytest.mark.parametrize("kind", ["persistent", "temporary"])
async def test_sensitive_content_does_not_hide_malformed_metadata(kind):
    storage = FakeStorage()
    manager = PersistentMemory(storage) if kind == "persistent" else TemporaryMemory(storage)
    await manager.async_initialize()
    if kind == "persistent":
        await manager.async_add("alice", CONTENTS[1], CATEGORY, "implicit", subject=SUBJECT, key=KEY)
        section = "memories"
    else:
        await manager.async_add("device:one", CONTENTS[1], expiry(), CATEGORY, owner_scope_id="user:alice")
        section = "records"
    valid = deepcopy(storage.data[section][0])
    damaged = {**valid, "memory_id": "damaged", "updated_at": "invalid"}
    storage.data = {section: [valid, damaged], "opaque": "repair evidence"}
    before = deepcopy(storage.data)
    saves = storage.save_count
    for _ in range(2):
        fresh = PersistentMemory(storage) if kind == "persistent" else TemporaryMemory(storage)
        if kind == "persistent":
            await fresh.async_initialize()
            assert len(await fresh.async_list("alice")) == 1
        else:
            with pytest.raises(ValueError, match="needs repair"):
                await fresh.async_initialize()
        with pytest.raises(ValueError, match="needs repair"):
            await fresh.async_backup_data()
        assert storage.data == before and storage.save_count == saves
    with pytest.raises(ValueError):
        manager.validate_backup_data({section: [damaged]})


async def test_temporary_cold_snapshot_retains_content_without_writing(hass, monkeypatch):
    storage = FakeStorage()
    manager = TemporaryMemory(storage)
    result = await manager.async_add_owned("user:alice", CONTENTS[1], expiry(), CATEGORY)
    await manager.async_update_owned("user:alice", result["memory"]["memory_id"], CONTENTS[2], None, None)
    before = deepcopy(storage.data)
    saves = storage.save_count
    monkeypatch.setattr(temporary_memory, "_temporary_memory_store", lambda *_: storage)
    assert not hass.data.get(temporary_memory._MANAGERS)
    records = await temporary_memory.async_read_temporary_memory_snapshot(
        hass, "entry", "agent", "different:continuity", "user:alice",
    )
    assert [r.content for r in records] == [CONTENTS[2]]
    assert await temporary_memory.async_read_temporary_memory_snapshot(
        hass, "entry", "agent", "different:continuity", "user:bob",
    ) == []
    assert storage.data == before and storage.save_count == saves
    assert not hass.data.get(temporary_memory._MANAGERS)
