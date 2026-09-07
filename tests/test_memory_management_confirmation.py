"""Persistent Memory management confirmation semantics."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.extended_openai_conversation_responses.const import (
    CONF_MEMORY_MODE,
    CONF_SHARED_MEMORY_MODE,
    CONF_TEMPORARY_MEMORY,
    DOMAIN,
    MEMORY_MODE_MANUAL,
    SHARED_MEMORY_EXPLICIT,
    TEMPORARY_MEMORY_OFF,
)
from custom_components.extended_openai_conversation_responses.memory_ui import (
    async_manage_command,
)


async def test_manual_management_update_does_not_confirm_by_default() -> None:
    """A management edit only refreshes confirmation when explicitly requested."""
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Assistant",
        data={
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_SHARED_MEMORY_MODE: SHARED_MEMORY_EXPLICIT,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_OFF,
        },
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        domain=DOMAIN,
        title="OpenAI",
        subentries={"agent-1": subentry},
    )
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = entry

    record = SimpleNamespace(
        memory_id="memory-1",
        user_id="user-7",
        content="Driving lessons are one hour.",
        category="work",
        source="explicit",
        created_at="2026-09-01T10:00:00+00:00",
        updated_at="2026-09-01T10:00:00+00:00",
        importance="normal",
        subject=None,
        key=None,
        valid_from=None,
        last_confirmed_at="2026-09-01T10:00:00+00:00",
    )
    persistent = SimpleNamespace(
        async_get_many=AsyncMock(return_value=[record]),
        async_update=AsyncMock(return_value=record),
    )

    with patch(
        "custom_components.extended_openai_conversation_responses.memory_ui.async_get_memory",
        AsyncMock(return_value=persistent),
    ):
        await async_manage_command(
            hass,
            "user-7",
            {
                "action": "update",
                "entry_id": "entry-1",
                "subentry_id": "agent-1",
                "memory_id": "memory-1",
                "content": "Driving lessons are one hour.",
                "category": "work",
            },
        )

    assert persistent.async_update.await_args.kwargs["refresh_confirmation"] is False
