"""Real Store evidence survives salvage, attempted mutation, and fresh managers."""

from copy import deepcopy
from inspect import unwrap
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    knowledge,
    management_ui,
    memory,
    request_rules,
)
from homeassistant.helpers.storage import Store
from tests.test_request_rules import local_rule


def _participants(hass, kind):
    if kind == "memory":
        store = memory.HomeAssistantMemoryStorage(hass, "entry", "agent")
        return store, memory.PersistentMemory(store), "memories"
    if kind == "knowledge":
        store = knowledge.HomeAssistantKnowledgeStorage(hass, "entry", "agent")
        return store, knowledge.KnowledgeLibrary(store), "sources"
    store = request_rules.RequestRuleStore(
        hass, request_rules.STORAGE_VERSION, "corruption.entry.agent"
    )
    return store, request_rules.RequestRules(store), "rules"


async def _mutate(manager, kind):
    if kind == "memory":
        return await manager.async_add(
            "alice", "A harmless cat fact", "pets", "explicit"
        )
    if kind == "knowledge":
        return await manager.async_create("Cats", "", "A harmless cat fact")
    return await manager.async_create(local_rule("Cats", phrases=["cats"]))


@pytest.mark.parametrize("kind", ["memory", "knowledge", "rules"])
@pytest.mark.parametrize("damage", ["malformed", "duplicate", "overflow", "container"])
async def test_corruption_cannot_be_overwritten_by_later_mutations(
    hass, monkeypatch, kind, damage
):
    storage, manager, records = _participants(hass, kind)
    await manager.async_initialize()
    await _mutate(manager, kind)
    clean = await manager.async_backup_data()
    damaged = deepcopy(clean)
    if damage == "container":
        damaged[records] = {"recoverable": deepcopy(clean[records])}
    else:
        extra = (
            {"recoverable": "DAMAGED-EVIDENCE"}
            if damage == "malformed"
            else deepcopy(damaged[records][0])
        )
        if damage == "overflow":
            identifier = {
                "memory": "memory_id",
                "knowledge": "source_id",
                "rules": "id",
            }[kind]
            extra[identifier] = "second-record"
            module, constant = {
                "memory": (memory, "MAX_MEMORIES_PER_AGENT"),
                "knowledge": (knowledge, "MAX_SOURCES_PER_AGENT"),
                "rules": (request_rules, "MAX_RULES"),
            }[kind]
            monkeypatch.setattr(module, constant, 1)
        damaged[records].append(extra)
    await storage.async_save(damaged)
    native = getattr(storage, "_store", storage)
    path = Path(native.path)
    evidence = path.read_bytes()
    for _ in range(2):
        _, fresh, _ = _participants(hass, kind)
        if damage == "container" and kind == "knowledge":
            with pytest.raises(ValueError, match="invalid structure"):
                await fresh.async_initialize()
        else:
            await fresh.async_initialize()
            with pytest.raises(ValueError, match="needs repair"):
                await fresh.async_backup_data()
            with pytest.raises(ValueError, match="needs repair"):
                if damage == "container":
                    await _mutate(fresh, kind)
                elif kind == "memory":
                    await fresh.async_clear("alice")
                elif kind == "knowledge":
                    await fresh.async_delete(clean[records][0]["source_id"])
                else:
                    await fresh.async_delete(clean[records][0]["id"])
        assert path.read_bytes() == evidence
    # Administrator repairs the original document; a fresh manager can write
    # normally. No hidden evidence copy survives an explicit later erasure.
    await storage.async_save(clean)
    _, repaired, _ = _participants(hass, kind)
    await repaired.async_initialize()
    assert await repaired.async_backup_data() == clean
    if kind == "memory":
        await repaired.async_clear("alice")
    elif kind == "knowledge":
        await repaired.async_delete(clean[records][0]["source_id"])
    else:
        await repaired.async_delete(clean[records][0]["id"])
    assert (await repaired.async_backup_data())[records] == []
    assert not list(path.parent.glob(path.name + ".*"))


@pytest.mark.parametrize("field", ["created_at", "updated_at"])
async def test_knowledge_load_and_backup_share_timestamp_validation(hass, field):
    storage, manager, _ = _participants(hass, "knowledge")
    await manager.async_initialize()
    await _mutate(manager, "knowledge")
    data = await manager.async_backup_data()
    data["sources"][0][field] = "invalid"
    with pytest.raises(ValueError, match="timestamp is invalid"):
        manager.validate_backup_data(data)
    await storage.async_save(data)
    _, reloaded, _ = _participants(hass, "knowledge")
    await reloaded.async_initialize()
    assert reloaded.total_source_count == 0
    with pytest.raises(ValueError, match="needs repair"):
        await reloaded.async_backup_data()
    assert json.loads(Path(storage._store.path).read_text())["data"] == data


async def test_knowledge_normalization_cannot_rewrite_a_damaged_generation(hass):
    storage, manager, _ = _participants(hass, "knowledge")
    await manager.async_initialize()
    await _mutate(manager, "knowledge")
    data = await manager.async_backup_data()
    good = {**data["sources"][0], "title": "  Cats   "}
    damaged = {**good, "source_id": "damaged", "created_at": "invalid"}
    await storage.async_save({"sources": [good, damaged]})
    original = Path(storage._store.path).read_bytes()
    _, fresh, _ = _participants(hass, "knowledge")
    await fresh.async_initialize()
    assert (await fresh.async_get(good["source_id"])).title == "Cats"
    assert Path(storage._store.path).read_bytes() == original


async def test_privacy_cleanup_retains_unrelated_corruption(hass):
    storage, manager, _ = _participants(hass, "memory")
    await manager.async_initialize()
    await _mutate(manager, "memory")
    clean = await manager.async_backup_data()
    private = {
        **clean["memories"][0],
        "memory_id": "private",
        "category": "myApiKey: PRIVATE_VALUE",
    }
    damaged = {"recoverable": "original damaged fact"}
    await storage.async_save({"memories": [*clean["memories"], private, damaged]})
    _, reloaded, _ = _participants(hass, "memory")
    await reloaded.async_initialize()
    persisted = json.loads(Path(storage._store.path).read_text())["data"]
    assert persisted == {"memories": [*clean["memories"], damaged]}
    with pytest.raises(ValueError, match="needs repair"):
        await reloaded.async_clear("alice")


@pytest.mark.parametrize(
    "section,value",
    [
        ("defaults", {"fuzzy": "broken"}),
        ("groups", [{"id": "broken"}]),
        ("wording_groups", "broken"),
        ("revision_generation", -1),
    ],
)
async def test_rule_metadata_damage_survives_normalization_and_mutation(
    hass, section, value
):
    storage, manager, _ = _participants(hass, "rules")
    await manager.async_initialize()
    await _mutate(manager, "rules")
    damaged = await manager.async_backup_data()
    damaged[section] = value
    await storage.async_save(damaged)
    evidence = Path(storage.path).read_bytes()
    _, fresh, _ = _participants(hass, "rules")
    await fresh.async_initialize()
    with pytest.raises(ValueError, match="needs repair"):
        await fresh.async_delete(damaged["rules"][0]["id"])
    assert Path(storage.path).read_bytes() == evidence


@pytest.mark.parametrize("kind", ["memory", "knowledge", "rules"])
async def test_legacy_migration_cannot_discard_malformed_payloads(hass, kind):
    storage, _, records = _participants(hass, kind)
    native = getattr(storage, "_store", storage)
    legacy = Store(hass, 1, native.key, private=True, atomic_writes=True)
    damaged = {records: "legacy damaged material"}
    if kind == "rules":
        damaged = {"rules": [], "wording_groups": "legacy damaged material"}
    await legacy.async_save(damaged)
    before = Path(native.path).read_bytes()
    _, fresh, _ = _participants(hass, kind)
    if kind == "knowledge":
        with pytest.raises(ValueError, match="needs repair"):
            await fresh.async_initialize()
        assert Path(native.path).read_bytes() == before
    else:
        await fresh.async_initialize()
        with pytest.raises(ValueError, match="needs repair"):
            await fresh.async_backup_data()
        assert json.loads(Path(native.path).read_text())["data"] == damaged


async def test_memory_migration_preserves_malformed_record_fields(hass):
    storage, _, _ = _participants(hass, "memory")
    legacy = Store(hass, 1, storage._store.key, private=True, atomic_writes=True)
    damaged = {"memories": [{"content": "Recoverable fact", "importance": "normal"}]}
    await legacy.async_save(damaged)
    _, fresh, _ = _participants(hass, "memory")
    await fresh.async_initialize()
    assert json.loads(Path(storage._store.path).read_text())["data"] == damaged
    with pytest.raises(ValueError, match="needs repair"):
        await fresh.async_backup_data()


@pytest.mark.parametrize("kind", ["memory", "knowledge", "rules"])
async def test_validated_replacement_clears_barrier_only_after_persistence(
    hass, monkeypatch, kind
):
    storage, manager, records = _participants(hass, kind)
    await manager.async_initialize()
    await _mutate(manager, kind)
    clean = await manager.async_backup_data()
    await storage.async_save(
        {**clean, records: [*clean[records], {"recoverable": "damaged sibling"}]}
    )
    _, fresh, _ = _participants(hass, kind)
    await fresh.async_initialize()
    native = getattr(storage, "_store", storage)
    path = Path(native.path)
    evidence = path.read_bytes()
    replacement = fresh.validate_backup_data(clean)
    writer = getattr(getattr(fresh, "_storage", None), "_store", None) or fresh._store

    def failed_write(*_args):
        raise OSError("replacement write failed")

    with monkeypatch.context() as fault:
        fault.setattr(
            writer,
            "_write_prepared_data"
            if writer._serialize_in_event_loop
            else "_write_data",
            failed_write,
        )
        with pytest.raises(OSError, match="replacement write failed"):
            await fresh.async_replace_backup(replacement)
    assert path.read_bytes() == evidence
    with pytest.raises(ValueError, match="needs repair"):
        await fresh.async_backup_data()
    # Existing journal recovery uses complete validated replacements, and must
    # not be stranded by an ordinary-write barrier on a readable damaged store.
    await fresh.async_replace_backup(replacement)
    assert await fresh.async_backup_data() == clean
    assert json.loads(path.read_text())["data"][records] == clean[records]


@pytest.mark.parametrize("kind", ["memory", "knowledge", "rules"])
async def test_corruption_rejection_reaches_management_caller(hass, monkeypatch, kind):
    storage, manager, records = _participants(hass, kind)
    await manager.async_initialize()
    await _mutate(manager, kind)
    clean = await manager.async_backup_data()
    await storage.async_save(
        {**clean, records: [*clean[records], {"recoverable": "damaged sibling"}]}
    )
    native = getattr(storage, "_store", storage)
    original = Path(native.path).read_bytes()
    _, fresh, _ = _participants(hass, kind)
    await fresh.async_initialize()

    async def command(*_):
        if kind == "memory":
            return await fresh.async_clear("alice")
        if kind == "knowledge":
            return await fresh.async_delete(clean[records][0]["source_id"])
        return await fresh.async_delete(clean[records][0]["id"])

    monkeypatch.setattr(management_ui, "async_management_command", command)
    connection = SimpleNamespace(
        user=SimpleNamespace(id="admin", is_admin=True),
        send_error=Mock(),
        send_result=Mock(),
    )
    await unwrap(management_ui.websocket_management)(hass, connection, {"id": 1})
    assert connection.send_error.call_args.args[:2] == (1, "eoai_mutation_rejected")
    assert "needs repair" in connection.send_error.call_args.args[2]
    connection.send_result.assert_not_called()
    assert Path(native.path).read_bytes() == original
