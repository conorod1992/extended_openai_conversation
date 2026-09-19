"""Focused residual coverage for Temporary Memory ownership boundaries."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    temporary_memory as ownership,
    temporary_memory as temporary_module,
)
from custom_components.extended_openai_conversation_responses.scope import (
    SHARED_HOUSEHOLD_SCOPE_ID,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    MAX_DELETE_RECORDS,
    TemporaryMemory,
    TemporaryMemoryRecord,
)
from homeassistant.exceptions import HomeAssistantError


def _record(
    memory_id: str,
    *,
    owner: str | None,
    scope_id: str = "conversation:test",
    expires_delta: timedelta = timedelta(hours=1),
    updated_delta: timedelta = timedelta(),
) -> TemporaryMemoryRecord:
    now = datetime.now(UTC)
    return TemporaryMemoryRecord(
        memory_id=memory_id,
        scope_id=scope_id,
        content=f"content-{memory_id}",
        category="general",
        source="automatic",
        expires_at=(now + expires_delta).isoformat(),
        created_at=(now - timedelta(minutes=1)).isoformat(),
        updated_at=(now + updated_delta).isoformat(),
        owner_scope_id=owner,
    )


@pytest.mark.asyncio
async def test_conversation_contract_binds_and_resets_owner_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Conversation reads/tools inherit only the resolved retained-data owner."""
    from custom_components.extended_openai_conversation_responses import conversation

    seen: list[tuple[str, str | None]] = []

    async def active(_scope):
        seen.append(("retrieve", ownership._ACTIVE_OWNER_SCOPE_ID.get()))
        return [_record("one", owner=ownership._ACTIVE_OWNER_SCOPE_ID.get())]

    async def add(*_args):
        seen.append(("add", ownership._ACTIVE_OWNER_SCOPE_ID.get()))
        return {"ok": True}

    entity_cls = conversation.ExtendedOpenAIAgentEntity
    entity = object.__new__(entity_cls)
    entity.subentry = SimpleNamespace(data={"temporary_memory": "enabled"})
    entity._effective_guest_policy = lambda: SimpleNamespace(temporary_memory=True)
    entity._temporary_memory = SimpleNamespace(async_active=active, async_add=add)
    assert await entity._async_retrieve_temporary_memories() == []
    with pytest.raises(RuntimeError, match="temporary memory is unavailable"):
        await entity._async_execute_temporary_memory_tool("add", {})

    scope_token = conversation._ACTIVE_SCOPE.set(
        SimpleNamespace(scope_type="user", user_id="alice")
    )
    temporary_token = conversation._ACTIVE_TEMPORARY_SCOPE.set("conversation:one")
    try:
        records = await entity_cls._async_retrieve_temporary_memories(entity)
        result = await entity_cls._async_execute_temporary_memory_tool(
            entity, "add", {"content": "fact", "expires_at": "later"}
        )
    finally:
        conversation._ACTIVE_TEMPORARY_SCOPE.reset(temporary_token)
        conversation._ACTIVE_SCOPE.reset(scope_token)

    assert [record.memory_id for record in records] == ["one"]
    assert result == {"ok": True}
    assert seen == [("retrieve", "user:alice"), ("add", "user:alice")]
    assert ownership._ACTIVE_OWNER_SCOPE_ID.get() is None


@pytest.mark.asyncio
async def test_management_contract_validates_and_enriches_owner_operations(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Management commands cannot escape Personal/Shared ownership boundaries."""
    from custom_components.extended_openai_conversation_responses import (
        management_loading_performance as loading,
        management_ui,
    )

    fallback_results = []

    async def fallback(*_args):
        return fallback_results.pop(0)

    monkeypatch.setattr(loading, "async_scope_catalog", fallback)

    class Manager:
        def stats(self) -> dict[str, int]:
            return {"active": 1}

        async def async_list_owned(self, owner: str) -> list[TemporaryMemoryRecord]:
            return [_record("owned", owner=owner, scope_id=owner)]

        async def async_delete_owned(self, owner: str, ids: list[str]) -> int:
            assert owner == "user:alice"
            assert ids == ["owned"]
            return 1

        async def async_update_owned(
            self,
            owner: str,
            memory_id: str,
            content: str | None,
            expires_at: str | None,
            category: str | None,
        ) -> TemporaryMemoryRecord:
            if content == "bad":
                raise ValueError("invalid update")
            return _record(memory_id, owner=owner, scope_id=owner)

        def owner_counts(self) -> dict[str, int]:
            return {"user:alice": 2, SHARED_HOUSEHOLD_SCOPE_ID: 4}

    manager = Manager()
    entry = SimpleNamespace(entry_id="entry")
    subentry = SimpleNamespace(subentry_id="sub")

    monkeypatch.setattr(
        management_ui,
        "entry_and_agent",
        lambda _hass, _entry_id, _subentry_id: (entry, subentry),
    )
    monkeypatch.setattr(
        management_ui,
        "_selected_scope",
        lambda _user_id, _is_admin, requested: requested or "user:alice",
    )

    async def get_manager(*_args: Any) -> Manager:
        return manager

    monkeypatch.setattr(management_ui, "async_get_temporary_memory", get_manager)

    base = {
        "section": "memories",
        "entry_id": "entry",
        "subentry_id": "sub",
        "scope_id": "user:alice",
    }
    listed = await management_ui.async_management_command(
        hass, "alice", False, base | {"action": "temporary_list"}
    )
    assert listed["scope_id"] == "user:alice"
    assert listed["memories"][0]["owner_scope_id"] == "user:alice"

    with pytest.raises(HomeAssistantError, match="memory_id is required"):
        await management_ui.async_management_command(
            hass, "alice", False, base | {"action": "temporary_delete"}
        )

    deleted = await management_ui.async_management_command(
        hass,
        "alice",
        False,
        base | {"action": "temporary_delete", "memory_id": "owned"},
    )
    assert deleted == {"deleted": 1}

    with pytest.raises(HomeAssistantError, match="must be strings when supplied"):
        await management_ui.async_management_command(
            hass,
            "alice",
            False,
            base
            | {
                "action": "temporary_update",
                "memory_id": "owned",
                "content": 123,
            },
        )

    with pytest.raises(HomeAssistantError, match="at least one Temporary Memory field"):
        await management_ui.async_management_command(
            hass,
            "alice",
            False,
            base | {"action": "temporary_update", "memory_id": "owned"},
        )

    with pytest.raises(HomeAssistantError, match="invalid update"):
        await management_ui.async_management_command(
            hass,
            "alice",
            False,
            base
            | {
                "action": "temporary_update",
                "memory_id": "owned",
                "content": "bad",
            },
        )

    updated = await management_ui.async_management_command(
        hass,
        "alice",
        False,
        base
        | {
            "action": "temporary_update",
            "memory_id": "owned",
            "content": "good",
        },
    )
    assert updated["memory"]["owner_scope_id"] == "user:alice"

    monkeypatch.setattr(
        management_ui,
        "_selected_scope",
        lambda *_args: "device:kitchen",
    )
    with pytest.raises(HomeAssistantError, match="Personal or Shared scopes"):
        await management_ui.async_management_command(
            hass, "alice", False, base | {"action": "temporary_list"}
        )

    fallback_results.extend(
        [
            {
                "scopes": [
                    {"scope_id": "user:alice"},
                    {"scope_id": SHARED_HOUSEHOLD_SCOPE_ID},
                    "ignore-me",
                ]
            },
            {"scopes": "not-a-list"},
        ]
    )
    catalog = await management_ui.async_management_command(
        hass,
        "alice",
        True,
        {
            "section": "scopes",
            "action": "catalog",
            "entry_id": "entry",
            "subentry_id": "sub",
        },
    )
    assert catalog["scopes"][0]["temporary_memory_count"] == 2
    assert catalog["scopes"][1]["temporary_memory_count"] == 4

    unchanged = await management_ui.async_management_command(
        hass,
        "alice",
        True,
        {
            "section": "scopes",
            "action": "catalog",
            "entry_id": "entry",
            "subentry_id": "sub",
        },
    )
    assert unchanged == {"scopes": "not-a-list"}


async def test_direct_manager_backup_and_owned_helpers() -> None:
    store = SimpleNamespace(
        async_load=AsyncMock(return_value=None), async_save=AsyncMock()
    )
    memory = TemporaryMemory(store)
    await memory.async_initialize()
    records = [
        _record("valid", owner="user:alice"),
        _record("legacy", owner=None, scope_id=SHARED_HOUSEHOLD_SCOPE_ID),
        _record("invalid", owner="device:kitchen"),
    ]
    validated = TemporaryMemory.validate_backup_data(
        {"records": [asdict(r) for r in records]}
    )
    assert [r.memory_id for r in validated] == ["valid", "legacy"]
    assert validated[1].owner_scope_id == SHARED_HOUSEHOLD_SCOPE_ID
    await memory.async_replace_backup(records)
    updated = await memory.async_update_owned(
        "user:alice", "valid", "new", None, "note"
    )
    assert updated.owner_scope_id == "user:alice"
    assert updated.scope_id == "conversation:test"
    assert memory.owner_counts() == {"user:alice": 1, SHARED_HOUSEHOLD_SCOPE_ID: 1}
    with pytest.raises(ValueError, match="memory_ids must contain"):
        await memory.async_delete_owned("user:alice", [])
    with pytest.raises(ValueError, match="memory_ids must contain"):
        await memory.async_delete_owned(
            "user:alice", [str(i) for i in range(MAX_DELETE_RECORDS + 1)]
        )
    assert await memory.async_delete_owned("user:alice", ["legacy"]) == 0
    assert await memory.async_delete_owned("user:alice", ["valid"]) == 1


async def test_snapshot_contract_fails_closed_before_io_and_uses_bound_owner(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import management_ui

    store = SimpleNamespace(
        async_load=AsyncMock(
            return_value={
                "records": [
                    asdict(_record("one", owner="user:alice")),
                    asdict(_record("foreign", owner="user:bob")),
                ]
            }
        )
    )

    def factory(*_):
        return store

    monkeypatch.setattr(temporary_module, "_temporary_memory_store", factory)
    assert (
        await temporary_module.async_read_temporary_memory_snapshot(
            object(), "entry", "sub", "scope"
        )
        == []
    )
    store.async_load.assert_not_awaited()
    token = temporary_module._ACTIVE_OWNER_SCOPE_ID.set("user:alice")
    try:
        records = await management_ui.async_read_temporary_memory_snapshot(
            object(), "entry", "sub", "different-continuity"
        )
    finally:
        temporary_module._ACTIVE_OWNER_SCOPE_ID.reset(token)
    assert [r.memory_id for r in records] == ["one"]
    store.async_load.assert_awaited_once()
    assert (
        management_ui.async_read_temporary_memory_snapshot
        is temporary_module.async_read_temporary_memory_snapshot
    )
