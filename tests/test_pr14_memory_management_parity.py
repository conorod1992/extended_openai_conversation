"""Regression tests for PR14 Memory management parity."""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
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


async def test_ui_preserves_household_and_advanced_persistent_editing() -> None:
    """Existing persistent parity remains user/household scoped with metadata edits."""
    hass = _hass_and_agent()
    record = SimpleNamespace(
        memory_id="memory-1",
        user_id=SHARED_HOUSEHOLD_SCOPE_ID,
        content="Bins go out Friday.",
        category="home",
        source="explicit",
        created_at="2026-09-01T10:00:00+00:00",
        updated_at="2026-09-01T10:00:00+00:00",
        importance="high",
        subject=None,
        key=None,
        valid_from=None,
        last_confirmed_at="2026-09-01T10:00:00+00:00",
    )
    persistent = SimpleNamespace(async_update=AsyncMock(return_value=record))
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
                "subject": "",
                "key": "",
                "valid_from": "",
            },
        )

    persistent.async_update.assert_awaited_once_with(
        "user-7",
        "memory-1",
        "Bins go out Friday.",
        "home",
        "high",
        "",
        "",
        "",
        target_user_id=SHARED_HOUSEHOLD_SCOPE_ID,
    )
    assert result["memory"]["scope"] == "Shared household"
    assert result["memory"]["importance"] == "high"


def test_agents_report_shared_and_temporary_management_capabilities() -> None:
    """The panel can accurately describe management surfaces for each agent."""
    hass = _hass_and_agent()

    result = pytest.run(async_manage_command(hass, "user-7", {"action": "agents"}))

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
