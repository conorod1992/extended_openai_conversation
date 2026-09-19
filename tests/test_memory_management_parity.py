"""Unified Management parity and shared Memory store safety regressions."""

from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.const import (
    CONF_SHARED_MEMORY_MODE,
    SHARED_MEMORY_DISABLED,
    SHARED_MEMORY_EXPLICIT,
)
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
    memory_revision,
)
from homeassistant.exceptions import HomeAssistantError


class _Storage:
    """Small detached store for optimistic-concurrency tests."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = deepcopy(data)
        self.save_count = 0

    async def async_load(self) -> dict[str, Any] | None:
        return deepcopy(self.data)

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = deepcopy(data)
        self.save_count += 1


@pytest.fixture
async def store(monkeypatch):
    memory = PersistentMemory(_Storage())
    await memory.async_initialize()
    monkeypatch.setattr(
        management_ui, "async_get_memory", AsyncMock(return_value=memory)
    )
    return memory


async def command(action, *, admin=False, config=None, **payload):
    request = management_ui._ManagementRequest(
        SimpleNamespace(),
        "user-7",
        admin,
        {"section": "memories", "action": action, **payload},
        "entry-1",
        "agent-1",
        SimpleNamespace(entry_id="entry-1"),
        SimpleNamespace(
            subentry_id="agent-1",
            data=config or {CONF_SHARED_MEMORY_MODE: SHARED_MEMORY_EXPLICIT},
        ),
    )
    return await management_ui.async_memories_command(request)


async def test_rich_metadata_revision_and_clear_fields(store):
    await command(
        "add",
        content="Bins go out Friday",
        category="home",
        importance="high",
        subject="Bins",
        key="bins.day",
        valid_from="2026-09-01T00:00:00+00:00",
    )
    record = (await command("list"))["memories"][0]
    assert record["importance"] == "high"
    assert record["subject"] == "Bins"
    assert record["key"] == "bins.day"
    assert record["valid_from"] == "2026-09-01T00:00:00+00:00"
    assert len(record["revision"]) == 64
    assert "scope_id" not in record
    result = await command(
        "update",
        memory_id=record["memory_id"],
        expected_revision=record["revision"],
        content="Bins go out Thursday",
        category="chores",
        importance="low",
        clear_fields=["subject", "key", "valid_from"],
    )
    updated = result["memory"]
    assert updated["revision"] != record["revision"]
    assert updated["category"] == "chores"
    assert updated["importance"] == "low"
    assert all(updated[field] is None for field in ("subject", "key", "valid_from"))
    assert updated["last_confirmed_at"] == record["last_confirmed_at"]
    with pytest.raises(ValueError, match="changed since it was loaded"):
        await command(
            "update",
            memory_id=record["memory_id"],
            expected_revision=record["revision"],
            content="Stale",
        )
    fresh = (await command("search", query="Thursday"))["memories"][0]
    assert fresh["revision"] == updated["revision"]
    await command(
        "update",
        memory_id=fresh["memory_id"],
        expected_revision=fresh["revision"],
        content="Reopened",
    )
    assert len(await store.async_list("user-7")) == 1


@pytest.mark.parametrize(
    "source,target",
    [("user:user-7", "shared:household"), ("shared:household", "user:user-7")],
)
async def test_atomic_owner_move_and_stale_move_rejection(store, source, target):
    await command("add", admin=True, scope_id=source, content="Family fact")
    record = (await command("list", admin=True, scope_id=source))["memories"][0]
    await command(
        "update",
        admin=True,
        scope_id=source,
        memory_id=record["memory_id"],
        content="Newer fact",
        expected_revision=record["revision"],
    )
    with pytest.raises(ValueError, match="changed since"):
        await command(
            "update",
            admin=True,
            scope_id=source,
            target_scope_id=target,
            memory_id=record["memory_id"],
            expected_revision=record["revision"],
        )
    assert not (await command("list", admin=True, scope_id=target))["memories"]
    fresh = (await command("list", admin=True, scope_id=source))["memories"][0]
    await command(
        "update",
        admin=True,
        scope_id=source,
        target_scope_id=target,
        memory_id=fresh["memory_id"],
        expected_revision=fresh["revision"],
    )
    assert not (await command("list", admin=True, scope_id=source))["memories"]
    moved = (await command("list", admin=True, scope_id=target))["memories"]
    assert len(moved) == 1 and moved[0]["memory_id"] == record["memory_id"]


async def test_failed_durable_move_preserves_owner(store):
    await command("add", content="Family fact")
    original = (await command("list"))["memories"][0]
    store._storage.async_save = AsyncMock(side_effect=OSError("disk full"))
    with pytest.raises(OSError):
        await command(
            "update",
            admin=True,
            memory_id=original["memory_id"],
            target_scope_id="shared:household",
            expected_revision=original["revision"],
        )
    assert (await command("list"))["memories"] == [original]
    assert not (await command("list", admin=True, scope_id="shared:household"))[
        "memories"
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"scope_id": "shared:household"},
        {"target_scope_id": "shared:household"},
        {"scope_id": "user:other"},
        {"target_scope_id": "user:other"},
    ],
)
async def test_nonadmin_cannot_spoof_source_or_target(store, payload):
    await command("add", content="Private fact")
    record = (await command("list"))["memories"][0]
    with pytest.raises(HomeAssistantError, match="not available"):
        await command("update", memory_id=record["memory_id"], **payload)
    assert (await command("list"))["memories"] == [record]


async def test_disabled_shared_destination_and_invalid_controls(store):
    await command("add", content="Private fact")
    record = (await command("list"))["memories"][0]
    with pytest.raises(HomeAssistantError, match="disabled"):
        await command(
            "update",
            admin=True,
            config={CONF_SHARED_MEMORY_MODE: SHARED_MEMORY_DISABLED},
            target_scope_id="shared:household",
            memory_id=record["memory_id"],
        )
    for values, error in [
        ({"refresh_confirmation": "yes"}, HomeAssistantError),
        ({"clear_fields": "subject"}, ValueError),
        ({"expected_revision": "bad"}, ValueError),
    ]:
        with pytest.raises(error):
            await command("update", memory_id=record["memory_id"], **values)
    assert (await command("list"))["memories"] == [record]


async def test_temporary_clear_requires_confirmation_and_batches_authorized_owner(
    monkeypatch,
):
    records = [SimpleNamespace(memory_id=f"m-{i}") for i in range(55)]
    manager = SimpleNamespace(
        async_list_owned=AsyncMock(return_value=records),
        async_delete_owned=AsyncMock(side_effect=[50, 5]),
    )
    get = AsyncMock(return_value=manager)
    monkeypatch.setattr(management_ui, "async_get_temporary_memory", get)
    with pytest.raises(HomeAssistantError, match="confirmation"):
        await command("temporary_clear")
    manager.async_list_owned.assert_not_awaited()
    with pytest.raises(HomeAssistantError, match="not available"):
        await command("temporary_clear", scope_id="user:other", confirm=True)
    assert await command("temporary_clear", confirm=True) == {"deleted": 55}
    manager.async_list_owned.assert_awaited_once_with("user:user-7")
    assert [args.args for args in manager.async_delete_owned.await_args_list] == [
        ("user:user-7", [r.memory_id for r in records[:50]]),
        ("user:user-7", [r.memory_id for r in records[50:]]),
    ]
    assert get.await_args.args[1:] == ("entry-1", "agent-1")


async def test_temporary_delete_uses_authorized_scope(monkeypatch):
    manager = SimpleNamespace(async_delete_owned=AsyncMock(return_value=1))
    monkeypatch.setattr(
        management_ui, "async_get_temporary_memory", AsyncMock(return_value=manager)
    )
    await command("temporary_delete", memory_id="mine")
    manager.async_delete_owned.assert_awaited_once_with("user:user-7", ["mine"])
    with pytest.raises(HomeAssistantError, match="not available"):
        await command("temporary_delete", memory_id="other", scope_id="user:other")


async def test_persistent_revision_rejects_stale_save_without_mutation() -> None:
    """A stale editor cannot overwrite a newer substantive memory state."""
    storage = _Storage()
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    created = await memory.async_add(
        "user-7",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        subject="Oscar",
        key="pet.oscar.breed",
    )
    assert "revision" not in created["memory"]
    memory_id = created["memory"]["memory_id"]
    original = (await memory.async_list("user-7"))[0]
    first_revision = memory_revision(original)

    updated = await memory.async_update(
        "user-7",
        memory_id,
        content="Oscar is a Cavachon dog.",
        clear_fields=["subject"],
        expected_revision=first_revision,
        refresh_confirmation=False,
    )
    second_revision = memory_revision(updated)
    assert second_revision != first_revision
    assert updated.subject is None

    with pytest.raises(ValueError, match="changed since it was loaded"):
        await memory.async_update(
            "user-7",
            memory_id,
            content="Stale overwrite.",
            expected_revision=first_revision,
        )

    current = (await memory.async_list("user-7"))[0]
    assert current.content == "Oscar is a Cavachon dog."
    assert current.subject is None
    assert memory_revision(current) == second_revision


async def test_persistent_blank_update_is_rejected_without_mutation() -> None:
    """Backend validation independently prevents a blank editor save."""
    memory = PersistentMemory(_Storage())
    await memory.async_initialize()
    created = await memory.async_add(
        "user-7", "Driving lessons are one hour.", "work", "explicit"
    )
    memory_id = created["memory"]["memory_id"]
    original = (await memory.async_list("user-7"))[0]
    revision = memory_revision(original)

    with pytest.raises(ValueError, match="content must be 1 to"):
        await memory.async_update(
            "user-7", memory_id, content="   ", expected_revision=revision
        )

    current = (await memory.async_list("user-7"))[0]
    assert current.content == "Driving lessons are one hour."
    assert memory_revision(current) == revision
