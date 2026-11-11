"""Private Memory exports retain full values without broadening setup sharing."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.memory import PersistentMemory
from custom_components.extended_openai_conversation_responses.temporary_memory import TemporaryMemory
from tests.test_backup import _document
from tests.test_memory import FakeStorage
from tests.test_transfer import _entry_and_subentry


async def test_private_full_and_memory_only_transfers_preserve_sensitive_values(hass, monkeypatch):
    source = _document()
    persistent = source["memories"]["memories"][0]
    persistent.update(
        content="The Wi-Fi password is synthetic-private-value.",
        category="password: synthetic-category", subject="medical diagnosis: synthetic-condition",
        key="sk-synthetic-transfer123", valid_from=persistent["created_at"],
    )
    temporary = source["temporary_memories"]["records"][0]
    temporary.update(content="The gate PIN is 2468.", category="password: synthetic-category", owner_scope_id="user:user-1")
    source["temporary_memories"]["records"] = [temporary]
    memory = PersistentMemory(FakeStorage(source["memories"]))
    await memory.async_initialize()
    short_term = TemporaryMemory(FakeStorage(source["temporary_memories"]))
    await short_term.async_initialize()
    entry, subentry = _entry_and_subentry(source["agent"]["config"])
    entry.data = {"api_key": "synthetic-provider-connection-value"}
    for module in (backup, transfer):
        monkeypatch.setattr(module, "async_get_memory", AsyncMock(return_value=memory))
        monkeypatch.setattr(module, "async_get_temporary_memory", AsyncMock(return_value=short_term))
    for getter, section in (
        ("async_get_knowledge", "knowledge"), ("async_get_archive", "archive"),
        ("async_get_usage", "usage"), ("async_get_request_rules", "request_rules"),
        ("async_get_guest_mode", "guest_mode"),
    ):
        payload = source.get(section, {"schedule": None})
        manager = SimpleNamespace(async_backup_data=AsyncMock(return_value=payload))
        for module in (backup, transfer):
            monkeypatch.setattr(module, getter, AsyncMock(return_value=manager))

    full = await backup.async_create_backup(hass, entry, subentry)
    prepared = backup.inspect_backup(full["document"], "destination")
    assert prepared.memories[0].content == persistent["content"]
    assert prepared.memories[0].subject == persistent["subject"]
    assert prepared.memories[0].key == persistent["key"]
    assert prepared.memories[0].source == persistent["source"]
    assert prepared.temporary_memories[0].content == temporary["content"]
    assert prepared.temporary_memories[0].expires_at == temporary["expires_at"]

    selected = [transfer.SECTION_PERSISTENT_MEMORY, transfer.SECTION_TEMPORARY_MEMORY]
    custom = await transfer.async_collect_transfer_snapshot(hass, entry, subentry, mode="custom", sections=selected)
    assert set(custom["sections"]) == set(selected)
    assert "provider_connection" not in custom
    assert "synthetic-provider-connection-value" not in str(custom)
    imported = transfer.inspect_transfer(custom, "destination")
    assert imported.memories == prepared.memories
    assert imported.temporary_memories == prepared.temporary_memories
    # Mapping still validates the destination even though content is unrestricted.
    mapped = transfer.apply_user_scope_mappings(imported, selected, {"user-1": "destination-user"})
    assert mapped.memories[0].user_id == "destination-user"
    assert mapped.temporary_memories[0].owner_scope_id == "user:destination-user"
    assert mapped.memories[0].content == persistent["content"]

    target = PersistentMemory(FakeStorage())
    await target.async_initialize()
    await target.async_replace_backup(mapped.memories)
    target_temporary = TemporaryMemory(FakeStorage())
    await target_temporary.async_replace_backup(mapped.temporary_memories)
    assert (await target.async_list("destination-user"))[0].content == persistent["content"]
    assert await target.async_list("user-1") == []
    assert (await target_temporary.async_list_owned("user:destination-user"))[0].content == temporary["content"]

    # Sharing still reads only setup sections; deliberately retained values stay private.
    shareable = await transfer.async_create_setup_export(hass, entry, subentry, reviewed=True)
    assert set(shareable["document"]["sections"]) == transfer.SETUP_SECTIONS
    assert "synthetic-private-value" not in shareable["json"]
    assert "synthetic-provider-connection-value" not in shareable["json"]

    damaged = deepcopy(custom)
    damaged["sections"][transfer.SECTION_PERSISTENT_MEMORY]["memories"][0]["key"] = "Invalid Key"
    with pytest.raises(backup.BackupError):
        transfer.inspect_transfer(damaged, "destination")
