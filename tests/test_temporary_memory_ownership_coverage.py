"""Focused residual coverage for Temporary Memory ownership boundaries."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses import (
    management_ui,
    temporary_memory_ownership as ownership,
)
from custom_components.extended_openai_conversation_responses.scope import (
    SHARED_HOUSEHOLD_SCOPE_ID,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    MAX_ACTIVE_RECORDS,
    TemporaryMemoryRecord,
)


def _record(
    memory_id: str,
    *,
    scope_id: str = "user:one",
    owner_scope_id: str | None = "user:one",
    expires_delta: int = 3600,
    updated_delta: int = 0,
) -> TemporaryMemoryRecord:
    now = dt_util.utcnow()
    created = (now - timedelta(seconds=30)).isoformat()
    updated = (now + timedelta(seconds=updated_delta)).isoformat()
    expires = (now + timedelta(seconds=expires_delta)).isoformat()
    return TemporaryMemoryRecord(
        memory_id=memory_id,
        scope_id=scope_id,
        content=f"content-{memory_id}",
        category="general",
        source="automatic",
        expires_at=expires,
        created_at=created,
        updated_at=updated,
        owner_scope_id=owner_scope_id,
    )


def test_owner_validation_and_resolved_scope_translation() -> None:
    assert ownership._valid_owner_scope_id(" user:abc ") == "user:abc"
    assert ownership._valid_owner_scope_id(SHARED_HOUSEHOLD_SCOPE_ID) == SHARED_HOUSEHOLD_SCOPE_ID
    assert ownership._valid_owner_scope_id("user:") is None
    assert ownership._valid_owner_scope_id("device:kitchen") is None
    assert ownership._valid_owner_scope_id(42) is None
    assert ownership._valid_owner_scope_id("user:" + "x" * 124) is None

    assert ownership._owner_from_resolved_scope(SimpleNamespace(scope_type="user", user_id="abc")) == "user:abc"
    assert ownership._owner_from_resolved_scope(SimpleNamespace(scope_type="shared", user_id=None)) == SHARED_HOUSEHOLD_SCOPE_ID
    assert ownership._owner_from_resolved_scope(SimpleNamespace(scope_type="device", user_id="abc")) is None
    assert ownership._owner_from_resolved_scope(None) is None


def test_require_owner_uses_context_and_rejects_missing_owner() -> None:
    token = ownership._ACTIVE_OWNER_SCOPE_ID.set("user:context")
    try:
        assert ownership._require_owner_scope_id() == "user:context"
    finally:
        ownership._ACTIVE_OWNER_SCOPE_ID.reset(token)

    with pytest.raises(ValueError, match="resolved Personal or Shared owner"):
        ownership._require_owner_scope_id("device:kitchen")


def test_record_owner_normalization_preserves_valid_and_migrates_only_safe_legacy() -> None:
    valid = _record("valid", owner_scope_id="user:one")
    spaced = replace(valid, memory_id="spaced", owner_scope_id=" user:one ")
    legacy_safe = _record("legacy", owner_scope_id=None, scope_id="user:legacy")
    legacy_unsafe = _record("unsafe", owner_scope_id=None, scope_id="conversation:123")
    invalid = _record("invalid", owner_scope_id="device:kitchen")

    assert ownership._normalize_record_owner(valid) is valid
    assert ownership._normalize_record_owner(spaced).owner_scope_id == "user:one"
    assert ownership._normalize_record_owner(legacy_safe).owner_scope_id == "user:legacy"
    assert ownership._normalize_record_owner(legacy_unsafe) is None
    assert ownership._normalize_record_owner(invalid) is None


@pytest.mark.asyncio
async def test_normalize_loaded_records_prunes_invalid_and_overflow_and_persists() -> None:
    records = {
        f"r{i}": _record(f"r{i}", updated_delta=i)
        for i in range(MAX_ACTIVE_RECORDS + 2)
    }
    records["invalid"] = _record("invalid", owner_scope_id="device:kitchen")
    manager = SimpleNamespace(
        _lock=asyncio.Lock(),
        _records=records,
        _async_save_locked=AsyncMock(),
    )

    await ownership._normalize_loaded_records(manager)

    assert len(manager._records) == MAX_ACTIVE_RECORDS
    assert "invalid" not in manager._records
    assert "r0" not in manager._records
    assert "r1" not in manager._records
    assert manager.invalid_owners_pruned == 1
    assert manager.overflow_pruned == 2
    manager._async_save_locked.assert_awaited_once()


@pytest.mark.asyncio
async def test_normalize_loaded_records_restores_original_on_save_failure() -> None:
    invalid = _record("invalid", owner_scope_id="device:kitchen")
    original = {"invalid": invalid}
    manager = SimpleNamespace(
        _lock=asyncio.Lock(),
        _records=original,
        _async_save_locked=AsyncMock(side_effect=OSError("disk failed")),
    )

    with pytest.raises(OSError, match="disk failed"):
        await ownership._normalize_loaded_records(manager)

    assert manager._records is original


def test_records_for_owner_filters_expired_and_orders_deterministically() -> None:
    manager = SimpleNamespace(
        _records={
            "later": _record("later", expires_delta=7200),
            "earlier": _record("earlier", expires_delta=3600),
            "other": _record("other", owner_scope_id="user:other"),
            "expired": _record("expired", expires_delta=-10),
        }
    )
    result = ownership._records_for_owner(manager, "user:one")
    assert [item.memory_id for item in result] == ["earlier", "later"]


def test_required_message_string_rejects_missing_empty_and_wrong_type() -> None:
    assert ownership._required_message_string({"entry_id": "entry-1"}, "entry_id") == "entry-1"
    for value in (None, "", 123):
        with pytest.raises(HomeAssistantError, match="entry_id is required"):
            ownership._required_message_string({"entry_id": value}, "entry_id")


@pytest.fixture
def restore_management_contract():
    originals = {
        "command": management_ui.async_management_command,
        "preview": management_ui._async_preview_effective_request,
        "preview_prompt": getattr(management_ui, "_async_preview_effective_prompt", None),
        "modules": management_ui.MANAGEMENT_FRONTEND_MODULES,
    }
    try:
        yield
    finally:
        management_ui.async_management_command = originals["command"]
        management_ui._async_preview_effective_request = originals["preview"]
        if originals["preview_prompt"] is not None:
            management_ui._async_preview_effective_prompt = originals["preview_prompt"]
        management_ui.MANAGEMENT_FRONTEND_MODULES = originals["modules"]


@pytest.mark.asyncio
async def test_management_temporary_list_and_delete_are_owner_scoped(
    monkeypatch, restore_management_contract
) -> None:
    record = _record("memory-1", owner_scope_id="user:user-1")
    manager = SimpleNamespace(
        async_list_owned=AsyncMock(return_value=[record]),
        async_delete_owned=AsyncMock(return_value=1),
        stats=Mock(return_value={"active": 1}),
    )
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_args: (
        SimpleNamespace(entry_id="entry-1"), SimpleNamespace(subentry_id="agent-1")
    ))
    monkeypatch.setattr(management_ui, "_selected_scope", lambda *_args: "user:user-1")
    monkeypatch.setattr(management_ui, "async_get_temporary_memory", AsyncMock(return_value=manager))
    ownership._install_management_contract()

    base = {"section": "memories", "entry_id": "entry-1", "subentry_id": "agent-1"}
    listed = await management_ui.async_management_command(
        object(), "user-1", True, {**base, "action": "temporary_list"}
    )
    assert listed["scope_id"] == "user:user-1"
    assert listed["memories"][0]["owner_scope_id"] == "user:user-1"
    manager.async_list_owned.assert_awaited_once_with("user:user-1")

    deleted = await management_ui.async_management_command(
        object(), "user-1", True,
        {**base, "action": "temporary_delete", "memory_id": "memory-1"},
    )
    assert deleted == {"deleted": 1}
    manager.async_delete_owned.assert_awaited_once_with("user:user-1", ["memory-1"])


@pytest.mark.asyncio
async def test_management_update_validates_scope_id_fields_and_translates_value_errors(
    monkeypatch, restore_management_contract
) -> None:
    manager = SimpleNamespace(async_update_owned=AsyncMock(side_effect=ValueError("bad expiry")))
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_args: (
        SimpleNamespace(entry_id="entry-1"), SimpleNamespace(subentry_id="agent-1")
    ))
    monkeypatch.setattr(management_ui, "async_get_temporary_memory", AsyncMock(return_value=manager))
    ownership._install_management_contract()
    base = {"section": "memories", "action": "temporary_update", "entry_id": "entry-1", "subentry_id": "agent-1", "memory_id": "m1"}

    monkeypatch.setattr(management_ui, "_selected_scope", lambda *_args: "device:kitchen")
    with pytest.raises(HomeAssistantError, match="Personal or Shared"):
        await management_ui.async_management_command(object(), "u", True, {**base, "content": "x"})

    monkeypatch.setattr(management_ui, "_selected_scope", lambda *_args: "user:u")
    with pytest.raises(HomeAssistantError, match="must be strings"):
        await management_ui.async_management_command(object(), "u", True, {**base, "content": 7})
    with pytest.raises(HomeAssistantError, match="at least one"):
        await management_ui.async_management_command(object(), "u", True, base)
    with pytest.raises(HomeAssistantError, match="bad expiry"):
        await management_ui.async_management_command(object(), "u", True, {**base, "expires_at": "tomorrow"})


@pytest.mark.asyncio
async def test_scope_catalog_is_enriched_with_owner_counts(monkeypatch, restore_management_contract) -> None:
    async def original_command(_hass, _user_id, _is_admin, _message):
        return {"scopes": [{"scope_id": "user:u"}, {"scope_id": SHARED_HOUSEHOLD_SCOPE_ID}, {"scope_id": "device:x"}]}

    manager = SimpleNamespace(owner_counts=lambda: {"user:u": 2, SHARED_HOUSEHOLD_SCOPE_ID: 3})
    monkeypatch.setattr(management_ui, "async_management_command", original_command)
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_args: (
        SimpleNamespace(entry_id="entry-1"), SimpleNamespace(subentry_id="agent-1")
    ))
    monkeypatch.setattr(management_ui, "async_get_temporary_memory", AsyncMock(return_value=manager))
    ownership._install_management_contract()

    result = await management_ui.async_management_command(
        object(), "u", True,
        {"section": "scopes", "action": "catalog", "entry_id": "entry-1", "subentry_id": "agent-1"},
    )
    assert [scope["temporary_memory_count"] for scope in result["scopes"]] == [2, 3, 0]


def test_install_is_idempotent(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(ownership, "_INSTALLED", False)
    for name in (
        "_install_manager_contract",
        "_install_snapshot_contract",
        "_install_conversation_contract",
        "_install_management_contract",
    ):
        monkeypatch.setattr(ownership, name, lambda n=name: calls.append(n))

    ownership.install_temporary_memory_ownership()
    ownership.install_temporary_memory_ownership()

    assert calls == [
        "_install_manager_contract",
        "_install_snapshot_contract",
        "_install_conversation_contract",
        "_install_management_contract",
    ]
