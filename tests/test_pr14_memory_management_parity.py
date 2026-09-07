"""Regression tests for PR14 Memory management parity."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_MEMORY_MODE,
    CONF_SHARED_MEMORY_MODE,
    CONF_TEMPORARY_MEMORY,
    MEMORY_MODE_MANUAL,
    SHARED_MEMORY_EXPLICIT,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
    memory_as_dict,
)
from custom_components.extended_openai_conversation_responses.memory_ui import (
    async_manage_command,
)
from custom_components.extended_openai_conversation_responses.scope import (
    SHARED_HOUSEHOLD_SCOPE_ID,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemoryRecord,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util


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


def _hass_and_agent():
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Family assistant",
        data={
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_SHARED_MEMORY_MODE: SHARED_MEMORY_EXPLICIT,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
        },
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        domain="extended_openai_conversation_responses",
        title="OpenAI",
        subentries={"agent-1": subentry},
    )
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = entry
    hass.config_entries.async_entries.return_value = [entry]
    return hass


def _temporary_record(memory_id: str, scope_id: str) -> TemporaryMemoryRecord:
    now = dt_util.utcnow()
    return TemporaryMemoryRecord(
        memory_id=memory_id,
        scope_id=scope_id,
        content=f"temporary {memory_id}",
        category="context",
        source="automatic",
        expires_at=(now + timedelta(hours=1)).isoformat(),
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
    )


def _persistent_record(memory_id: str, user_id: str = "user-7") -> SimpleNamespace:
    return SimpleNamespace(
        memory_id=memory_id,
        user_id=user_id,
        content=f"persistent {memory_id}",
        category="context",
        source="explicit",
        created_at="2026-09-01T10:00:00+00:00",
        updated_at="2026-09-01T10:00:00+00:00",
        importance="normal",
        subject=None,
        key=None,
        valid_from=None,
        last_confirmed_at="2026-09-01T10:00:00+00:00",
    )


async def test_ui_lists_only_authenticated_user_temporary_scope() -> None:
    """The non-admin panel derives Temporary Memory ownership server-side."""
    hass = _hass_and_agent()
    persistent = SimpleNamespace(async_list=AsyncMock(return_value=[]))
    own = _temporary_record("mine", "user:user-7")
    other = _temporary_record("other", "user:user-8")
    device = _temporary_record("device", "device:kitchen")
    conversation = _temporary_record("conversation", "conversation:session-1")
    temporary = SimpleNamespace(
        async_list_all=AsyncMock(return_value=[own, other, device, conversation])
    )
    base = {"entry_id": "entry-1", "subentry_id": "agent-1"}

    with (
        patch(
            "custom_components.extended_openai_conversation_responses.memory_ui.async_get_memory",
            AsyncMock(return_value=persistent),
        ),
        patch(
            "custom_components.extended_openai_conversation_responses.memory_ui.async_get_temporary_memory",
            AsyncMock(return_value=temporary),
        ),
    ):
        result = await async_manage_command(
            hass, "user-7", {**base, "action": "list"}
        )

    persistent.async_list.assert_awaited_once_with(
        ["user-7", SHARED_HOUSEHOLD_SCOPE_ID], None, 100, 0
    )
    temporary.async_list_all.assert_awaited_once_with()
    assert result["temporary_memories"] == [
        {
            "memory_id": "mine",
            "content": "temporary mine",
            "category": "context",
            "source": "automatic",
            "expires_at": own.expires_at,
            "created_at": own.created_at,
            "updated_at": own.updated_at,
        }
    ]
    assert result["next_offset"] is None
    assert "scope_id" not in result["temporary_memories"][0]


async def test_ui_temporary_delete_cannot_choose_another_scope() -> None:
    """A delete always targets the authenticated user's derived scope."""
    hass = _hass_and_agent()
    temporary = SimpleNamespace(async_delete=AsyncMock(return_value=1))
    base = {"entry_id": "entry-1", "subentry_id": "agent-1"}

    with patch(
        "custom_components.extended_openai_conversation_responses.memory_ui.async_get_temporary_memory",
        AsyncMock(return_value=temporary),
    ):
        result = await async_manage_command(
            hass,
            "user-7",
            {
                **base,
                "action": "temporary_delete",
                "memory_id": "mine",
                "scope_id": "user:user-8",
            },
        )

    assert result == {"deleted": 1}
    temporary.async_delete.assert_awaited_once_with("user:user-7", ["mine"])


async def test_ui_temporary_clear_is_confirmed_scoped_and_batched() -> None:
    """Bulk clearing cannot cross scope or exceed the store's delete bound."""
    hass = _hass_and_agent()
    own = [_temporary_record(f"mine-{index}", "user:user-7") for index in range(55)]
    other = _temporary_record("other", "user:user-8")
    device = _temporary_record("device", "device:kitchen")
    temporary = SimpleNamespace(
        async_list_all=AsyncMock(return_value=[*own, other, device]),
        async_delete=AsyncMock(side_effect=[50, 5]),
    )
    base = {"entry_id": "entry-1", "subentry_id": "agent-1"}

    with patch(
        "custom_components.extended_openai_conversation_responses.memory_ui.async_get_temporary_memory",
        AsyncMock(return_value=temporary),
    ):
        with pytest.raises(HomeAssistantError, match="confirmation"):
            await async_manage_command(
                hass, "user-7", {**base, "action": "temporary_clear"}
            )
        result = await async_manage_command(
            hass,
            "user-7",
            {
                **base,
                "action": "temporary_clear",
                "confirm": True,
                "scope_id": "device:kitchen",
            },
        )

    assert result == {"deleted": 55}
    temporary.async_delete.assert_has_awaits(
        [
            call("user:user-7", [record.memory_id for record in own[:50]]),
            call("user:user-7", [record.memory_id for record in own[50:]]),
        ]
    )


async def test_ui_resolves_persistent_owner_server_side_and_forwards_metadata() -> None:
    """A browser-provided original scope cannot select the record being edited."""
    hass = _hass_and_agent()
    record = _persistent_record("memory-1", SHARED_HOUSEHOLD_SCOPE_ID)
    record.content = "Bins go out Friday."
    record.category = "home"
    record.importance = "high"
    persistent = SimpleNamespace(
        async_get_many=AsyncMock(return_value=[record]),
        async_update=AsyncMock(return_value=record),
    )
    base = {"entry_id": "entry-1", "subentry_id": "agent-1"}

    with patch(
        "custom_components.extended_openai_conversation_responses.memory_ui.async_get_memory",
        AsyncMock(return_value=persistent),
    ):
        result = await async_manage_command(
            hass,
            "user-7",
            {
                **base,
                "action": "update",
                "memory_id": "memory-1",
                "original_scope": "personal",
                "scope": "household",
                "content": "Bins go out Friday.",
                "category": "home",
                "importance": "high",
                "clear_fields": ["subject", "key", "valid_from"],
                "expected_revision": "a" * 64,
            },
        )

    persistent.async_get_many.assert_awaited_once_with(
        [
            ("user-7", "memory-1"),
            (SHARED_HOUSEHOLD_SCOPE_ID, "memory-1"),
        ],
        ["user-7", SHARED_HOUSEHOLD_SCOPE_ID],
    )
    persistent.async_update.assert_awaited_once_with(
        SHARED_HOUSEHOLD_SCOPE_ID,
        "memory-1",
        content="Bins go out Friday.",
        category="home",
        importance="high",
        subject=None,
        key=None,
        valid_from=None,
        refresh_confirmation=True,
        target_user_id=SHARED_HOUSEHOLD_SCOPE_ID,
        clear_fields=["subject", "key", "valid_from"],
        expected_revision="a" * 64,
    )
    assert result["memory"]["scope"] == "Shared household"
    assert result["memory"]["importance"] == "high"
    assert len(result["memory"]["revision"]) == 64


async def test_ui_delete_ignores_spoofed_scope_and_uses_server_owner() -> None:
    """Persistent deletion is authorized against the stored owner, not UI scope data."""
    hass = _hass_and_agent()
    record = _persistent_record("memory-1", SHARED_HOUSEHOLD_SCOPE_ID)
    persistent = SimpleNamespace(
        async_get_many=AsyncMock(return_value=[record]),
        async_delete=AsyncMock(return_value=1),
    )
    base = {"entry_id": "entry-1", "subentry_id": "agent-1"}

    with patch(
        "custom_components.extended_openai_conversation_responses.memory_ui.async_get_memory",
        AsyncMock(return_value=persistent),
    ):
        result = await async_manage_command(
            hass,
            "user-7",
            {**base, "action": "delete", "memory_id": "memory-1", "scope": "personal"},
        )

    assert result == {"deleted": 1}
    persistent.async_delete.assert_awaited_once_with(
        SHARED_HOUSEHOLD_SCOPE_ID, ["memory-1"]
    )


async def test_ui_exposes_persistent_paging_continuation() -> None:
    """A full page advertises the next offset so the browser cannot stop at 100."""
    hass = _hass_and_agent()
    records = [_persistent_record(f"memory-{index}") for index in range(100)]
    persistent = SimpleNamespace(async_list=AsyncMock(return_value=records))
    temporary = SimpleNamespace(async_list_all=AsyncMock(return_value=[]))
    base = {"entry_id": "entry-1", "subentry_id": "agent-1"}

    with (
        patch(
            "custom_components.extended_openai_conversation_responses.memory_ui.async_get_memory",
            AsyncMock(return_value=persistent),
        ),
        patch(
            "custom_components.extended_openai_conversation_responses.memory_ui.async_get_temporary_memory",
            AsyncMock(return_value=temporary),
        ),
    ):
        result = await async_manage_command(
            hass, "user-7", {**base, "action": "list", "limit": 100, "offset": 0}
        )

    assert len(result["memories"]) == 100
    assert result["next_offset"] == 100


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
    memory_id = created["memory"]["memory_id"]
    first_revision = created["memory"]["revision"]

    updated = await memory.async_update(
        "user-7",
        memory_id,
        content="Oscar is a Cavachon dog.",
        clear_fields=["subject"],
        expected_revision=first_revision,
        refresh_confirmation=False,
    )
    second_revision = memory_as_dict(updated)["revision"]
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
    assert memory_as_dict(current)["revision"] == second_revision


async def test_persistent_blank_update_is_rejected_without_mutation() -> None:
    """Backend validation independently prevents a blank editor save."""
    memory = PersistentMemory(_Storage())
    await memory.async_initialize()
    created = await memory.async_add(
        "user-7", "Driving lessons are one hour.", "work", "explicit"
    )
    memory_id = created["memory"]["memory_id"]
    revision = created["memory"]["revision"]

    with pytest.raises(ValueError, match="content must be 1 to"):
        await memory.async_update(
            "user-7", memory_id, content="   ", expected_revision=revision
        )

    current = (await memory.async_list("user-7"))[0]
    assert current.content == "Driving lessons are one hour."
    assert memory_as_dict(current)["revision"] == revision


async def test_agents_report_shared_and_temporary_management_capabilities() -> None:
    """The panel accurately describes management surfaces for each agent."""
    hass = _hass_and_agent()

    result = await async_manage_command(hass, "user-7", {"action": "agents"})

    assert result["agents"][0]["shared_memory_enabled"] is True
    assert result["agents"][0]["temporary_memory_enabled"] is True


def test_memory_panel_never_submits_a_temporary_scope_id() -> None:
    """The browser cannot select arbitrary Temporary Memory ownership."""
    panel = Path(
        "custom_components/extended_openai_conversation_responses/frontend/memory-panel.js"
    ).read_text()
    assert 'id="temporaryMemories"' in panel
    assert 'this._call("temporary_delete"' in panel
    assert 'this._call("temporary_clear"' in panel
    assert "scope_id" not in panel
