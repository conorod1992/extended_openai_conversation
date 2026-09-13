"""Focused residual coverage for Temporary Memory ownership boundaries."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import (
    temporary_memory as temporary_module,
    temporary_memory_ownership as ownership,
)
from custom_components.extended_openai_conversation_responses.scope import (
    SHARED_HOUSEHOLD_SCOPE_ID,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    MAX_ACTIVE_RECORDS,
    MAX_DELETE_RECORDS,
    TemporaryMemory,
    TemporaryMemoryRecord,
)


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


def _register_manager_mutations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure direct installer mutations are restored after each test."""
    for name in (
        "async_initialize",
        "async_active",
        "async_add",
        "async_update",
        "async_delete",
        "async_active_snapshot",
        "async_list",
        "async_list_all",
        "stats",
        "validate_backup_data",
        "async_replace_backup",
    ):
        monkeypatch.setattr(TemporaryMemory, name, getattr(TemporaryMemory, name))
    monkeypatch.setattr(
        temporary_module, "_matches_owner", temporary_module._matches_owner
    )
    monkeypatch.setattr(
        temporary_module,
        "_clean_owner_scope_id",
        temporary_module._clean_owner_scope_id,
    )


@pytest.mark.asyncio
async def test_manager_contract_enforces_owner_and_owned_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installed manager helpers fail closed and preserve the resolved owner."""
    _register_manager_mutations(monkeypatch)

    update_calls: list[tuple[Any, ...]] = []
    delete_calls: list[tuple[Any, ...]] = []
    replacement_batches: list[list[TemporaryMemoryRecord]] = []

    async def fake_update(
        _manager: Any,
        scope_id: str,
        memory_id: str,
        content: str | None,
        expires_at: str | None,
        category: str | None,
        *,
        owner_scope_id: str | None = None,
    ) -> TemporaryMemoryRecord:
        update_calls.append(
            (scope_id, memory_id, content, expires_at, category, owner_scope_id)
        )
        return _record(memory_id, owner=owner_scope_id, scope_id=scope_id)

    async def fake_delete(
        _manager: Any,
        scope_id: str,
        memory_ids: list[str],
        *,
        owner_scope_id: str | None = None,
    ) -> int:
        delete_calls.append((scope_id, list(memory_ids), owner_scope_id))
        return len(memory_ids)

    def fake_validate_backup(_payload: Any) -> list[TemporaryMemoryRecord]:
        return [
            _record("valid", owner="user:alice"),
            _record("legacy", owner=None, scope_id=SHARED_HOUSEHOLD_SCOPE_ID),
            _record("invalid", owner="device:kitchen"),
        ]

    async def fake_replace_backup(
        _manager: Any, records: list[TemporaryMemoryRecord]
    ) -> None:
        replacement_batches.append(list(records))

    monkeypatch.setattr(TemporaryMemory, "async_update", fake_update)
    monkeypatch.setattr(TemporaryMemory, "async_delete", fake_delete)
    monkeypatch.setattr(
        TemporaryMemory, "validate_backup_data", staticmethod(fake_validate_backup)
    )
    monkeypatch.setattr(TemporaryMemory, "async_replace_backup", fake_replace_backup)

    ownership._install_manager_contract()

    fake_manager = SimpleNamespace(
        _records={
            "alice": _record("alice", owner="user:alice"),
            "shared": _record("shared", owner=SHARED_HOUSEHOLD_SCOPE_ID),
            "expired": _record(
                "expired", owner="user:alice", expires_delta=timedelta(hours=-1)
            ),
            "invalid": _record("invalid", owner="device:kitchen"),
            "bad_expiry": TemporaryMemoryRecord(
                memory_id="bad_expiry",
                scope_id="conversation:test",
                content="bad",
                category="general",
                source="automatic",
                expires_at="not-a-date",
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
                owner_scope_id="user:alice",
            ),
        },
        invalid_owners_pruned=2,
        overflow_pruned=3,
    )

    assert await TemporaryMemory.async_active_snapshot(fake_manager, "scope") == []

    updated = await TemporaryMemory.async_update_owned(
        fake_manager, "user:alice", "m1", "new", None, "note"
    )
    assert updated.owner_scope_id == "user:alice"
    assert update_calls == [("user:alice", "m1", "new", None, "note", "user:alice")]

    with pytest.raises(ValueError, match="memory_ids must contain"):
        await TemporaryMemory.async_delete_owned(fake_manager, "user:alice", [])
    with pytest.raises(ValueError, match="memory_ids must contain"):
        await TemporaryMemory.async_delete_owned(
            fake_manager,
            "user:alice",
            [str(index) for index in range(MAX_DELETE_RECORDS + 1)],
        )
    assert await TemporaryMemory.async_delete_owned(
        fake_manager, "user:alice", ["m1"]
    ) == 1
    assert delete_calls == [("user:alice", ["m1"], "user:alice")]

    assert TemporaryMemory.owner_counts(fake_manager) == {
        "user:alice": 1,
        SHARED_HOUSEHOLD_SCOPE_ID: 1,
    }

    validated = TemporaryMemory.validate_backup_data({"ignored": True})
    assert [record.memory_id for record in validated] == ["valid", "legacy"]
    assert validated[1].owner_scope_id == SHARED_HOUSEHOLD_SCOPE_ID

    oversized = [
        _record(
            f"m{index:03d}",
            owner="user:alice",
            updated_delta=timedelta(seconds=index),
        )
        for index in range(MAX_ACTIVE_RECORDS + 2)
    ]
    oversized.append(_record("drop-invalid", owner="device:kitchen"))
    await TemporaryMemory.async_replace_backup(fake_manager, oversized)
    assert len(replacement_batches) == 1
    assert len(replacement_batches[0]) == MAX_ACTIVE_RECORDS
    assert replacement_batches[0][0].memory_id == f"m{MAX_ACTIVE_RECORDS + 1:03d}"
    assert all(
        record.owner_scope_id == "user:alice" for record in replacement_batches[0]
    )


@pytest.mark.asyncio
async def test_snapshot_contract_fails_closed_and_forwards_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cold snapshot reads require the same owner identity as live reads."""
    from custom_components.extended_openai_conversation_responses import management_ui

    calls: list[str | None] = []

    async def original(
        _hass: Any,
        _entry_id: str,
        _subentry_id: str,
        _scope_id: str,
        owner_scope_id: str | None = None,
    ) -> list[TemporaryMemoryRecord]:
        calls.append(owner_scope_id)
        return [_record("one", owner=owner_scope_id)]

    monkeypatch.setattr(temporary_module, "async_read_temporary_memory_snapshot", original)
    monkeypatch.setattr(management_ui, "async_read_temporary_memory_snapshot", original)

    ownership._install_snapshot_contract()

    assert (
        await temporary_module.async_read_temporary_memory_snapshot(
            object(), "entry", "sub", "scope"
        )
        == []
    )
    assert calls == []

    token = ownership._ACTIVE_OWNER_SCOPE_ID.set("user:alice")
    try:
        records = await temporary_module.async_read_temporary_memory_snapshot(
            object(), "entry", "sub", "scope"
        )
    finally:
        ownership._ACTIVE_OWNER_SCOPE_ID.reset(token)

    assert [record.memory_id for record in records] == ["one"]
    assert calls == ["user:alice"]
    assert management_ui.async_read_temporary_memory_snapshot is (
        temporary_module.async_read_temporary_memory_snapshot
    )


@pytest.mark.asyncio
async def test_conversation_contract_binds_and_resets_owner_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Conversation reads/tools inherit only the resolved retained-data owner."""
    from custom_components.extended_openai_conversation_responses import conversation

    seen: list[tuple[str, str | None]] = []

    async def original_retrieve(_entity: Any) -> list[TemporaryMemoryRecord]:
        seen.append(("retrieve", ownership._ACTIVE_OWNER_SCOPE_ID.get()))
        return [_record("one", owner=ownership._ACTIVE_OWNER_SCOPE_ID.get())]

    async def original_tool(
        _entity: Any, operation: str, _arguments: dict[str, Any]
    ) -> dict[str, Any]:
        seen.append((operation, ownership._ACTIVE_OWNER_SCOPE_ID.get()))
        return {"ok": True}

    entity_cls = conversation.ExtendedOpenAIAgentEntity
    monkeypatch.setattr(entity_cls, "_async_retrieve_temporary_memories", original_retrieve)
    monkeypatch.setattr(entity_cls, "_async_execute_temporary_memory_tool", original_tool)

    ownership._install_conversation_contract()

    entity = object()
    assert await entity_cls._async_retrieve_temporary_memories(entity) == []
    with pytest.raises(RuntimeError, match="temporary memory is unavailable"):
        await entity_cls._async_execute_temporary_memory_tool(entity, "add", {})

    scope_token = conversation._ACTIVE_SCOPE.set(
        SimpleNamespace(scope_type="user", user_id="alice")
    )
    temporary_token = conversation._ACTIVE_TEMPORARY_SCOPE.set("conversation:one")
    try:
        records = await entity_cls._async_retrieve_temporary_memories(entity)
        result = await entity_cls._async_execute_temporary_memory_tool(entity, "add", {})
    finally:
        conversation._ACTIVE_TEMPORARY_SCOPE.reset(temporary_token)
        conversation._ACTIVE_SCOPE.reset(scope_token)

    assert [record.memory_id for record in records] == ["one"]
    assert result == {"ok": True}
    assert seen == [("retrieve", "user:alice"), ("add", "user:alice")]
    assert ownership._ACTIVE_OWNER_SCOPE_ID.get() is None


@pytest.mark.asyncio
async def test_management_contract_validates_and_enriches_owner_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Management commands cannot escape Personal/Shared ownership boundaries."""
    from custom_components.extended_openai_conversation_responses import management_ui

    monkeypatch.setattr(
        management_ui,
        "MANAGEMENT_FRONTEND_MODULES",
        management_ui.MANAGEMENT_FRONTEND_MODULES,
    )
    monkeypatch.setattr(
        management_ui,
        "_async_preview_effective_request",
        management_ui._async_preview_effective_request,
    )
    monkeypatch.setattr(
        management_ui,
        "_async_preview_effective_prompt",
        management_ui._async_preview_effective_prompt,
    )
    monkeypatch.setattr(
        management_ui, "async_management_command", management_ui.async_management_command
    )

    preview_seen: list[str | None] = []

    async def preview(
        _hass: Any,
        _entry: Any,
        _subentry: Any,
        _candidate: dict[str, Any],
        _user_id: str,
    ) -> dict[str, Any]:
        preview_seen.append(ownership._ACTIVE_OWNER_SCOPE_ID.get())
        return {"preview": True}

    fallback_results: list[dict[str, Any]] = []

    async def fallback(
        _hass: Any, _user_id: str, _is_admin: bool, _message: dict[str, Any]
    ) -> dict[str, Any]:
        return fallback_results.pop(0)

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

    monkeypatch.setattr(management_ui, "_async_preview_effective_request", preview)
    monkeypatch.setattr(management_ui, "_async_preview_effective_prompt", preview)
    monkeypatch.setattr(management_ui, "async_management_command", fallback)
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

    ownership._install_management_contract()

    assert ownership._FRONTEND_MODULE in management_ui.MANAGEMENT_FRONTEND_MODULES
    assert await management_ui._async_preview_effective_request(
        object(), entry, subentry, {}, "alice"
    ) == {"preview": True}
    assert preview_seen == ["user:alice"]
    assert ownership._ACTIVE_OWNER_SCOPE_ID.get() is None

    base = {
        "section": "memories",
        "entry_id": "entry",
        "subentry_id": "sub",
        "scope_id": "user:alice",
    }
    listed = await management_ui.async_management_command(
        object(), "alice", False, base | {"action": "temporary_list"}
    )
    assert listed["scope_id"] == "user:alice"
    assert listed["memories"][0]["owner_scope_id"] == "user:alice"

    with pytest.raises(HomeAssistantError, match="memory_id is required"):
        await management_ui.async_management_command(
            object(), "alice", False, base | {"action": "temporary_delete"}
        )

    deleted = await management_ui.async_management_command(
        object(),
        "alice",
        False,
        base | {"action": "temporary_delete", "memory_id": "owned"},
    )
    assert deleted == {"deleted": 1}

    with pytest.raises(HomeAssistantError, match="must be strings when supplied"):
        await management_ui.async_management_command(
            object(),
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
            object(),
            "alice",
            False,
            base | {"action": "temporary_update", "memory_id": "owned"},
        )

    with pytest.raises(HomeAssistantError, match="invalid update"):
        await management_ui.async_management_command(
            object(),
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
        object(),
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
            object(), "alice", False, base | {"action": "temporary_list"}
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
        object(),
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
        object(),
        "alice",
        True,
        {"section": "scopes", "action": "catalog"},
    )
    assert unchanged == {"scopes": "not-a-list"}


def test_public_installer_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated startup installation never stacks ownership wrappers."""
    monkeypatch.setattr(ownership, "_INSTALLED", True)
    for name in (
        "_install_manager_contract",
        "_install_snapshot_contract",
        "_install_conversation_contract",
        "_install_management_contract",
    ):
        monkeypatch.setattr(
            ownership,
            name,
            lambda: pytest.fail("installer should not run when already installed"),
        )

    ownership.install_temporary_memory_ownership()
