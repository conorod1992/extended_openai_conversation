"""Portable restore ownership mapping regressions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ArchiveSession,
)
from custom_components.extended_openai_conversation_responses.memory import MemoryRecord
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemoryRecord,
)


class _Auth:
    def __init__(self, users):
        self._users = list(users)

    async def async_get_users(self):
        return list(self._users)


def _prepared() -> transfer.PreparedTransfer:
    stamp = "2026-10-04T12:00:00+00:00"
    return transfer.PreparedTransfer(
        source_kind="custom_backup",
        mode="custom",
        title="Jarvis",
        available_sections=frozenset(
            {
                transfer.SECTION_CONFIGURATION,
                transfer.SECTION_PERSISTENT_MEMORY,
                transfer.SECTION_TEMPORARY_MEMORY,
                transfer.SECTION_CONVERSATION_ARCHIVE,
            }
        ),
        created_at=stamp,
        integration_version="7.0.0",
        config={
            "voice_default_user_id": "source-a",
            "voice_device_mappings": {
                "device-one": "user:source-a",
                "device-two": "shared:household",
            },
        },
        raw_configuration={
            "voice_default_user_id": "source-a",
            "voice_device_mappings": {
                "device-one": "user:source-a",
                "device-two": "shared:household",
            },
        },
        memories=[
            MemoryRecord(
                memory_id="memory-1",
                user_id="source-a",
                content="Personal fact",
                category="general",
                source="explicit",
                created_at=stamp,
                updated_at=stamp,
            )
        ],
        temporary_memories=[
            TemporaryMemoryRecord(
                memory_id="temporary-1",
                scope_id="user:source-a",
                content="Short-lived fact",
                category="general",
                source="manual",
                expires_at="2026-10-05T12:00:00+00:00",
                created_at=stamp,
                updated_at=stamp,
                owner_scope_id="user:source-a",
            )
        ],
        archive_sessions=[
            ArchiveSession(
                session_id="session-1",
                home_assistant_conversation_id="conversation-1",
                agent_subentry_id="agent-1",
                scope_id="user:source-a",
                scope_type="user",
                scope_source="authenticated_user",
                source_device_id=None,
                started_at=stamp,
                last_message_at=stamp,
                title="Conversation",
                turn_count=0,
                retention_state="retained",
                last_activity_at=stamp,
            )
        ],
    )


async def test_restore_requires_mapping_for_source_user_missing_on_destination() -> None:
    hass = SimpleNamespace(
        auth=_Auth([SimpleNamespace(id="destination-a", name="Destination user")])
    )
    prepared = _prepared()
    selected = prepared.available_sections

    plan = await transfer.async_user_scope_mapping_plan(hass, prepared, selected)

    assert plan["required_source_user_ids"] == ["source-a"]
    assert plan["missing_source_user_ids"] == ["source-a"]
    assert plan["resolved"] == {}
    assert plan["destination_users"] == [
        {"user_id": "destination-a", "name": "Destination user"}
    ]


async def test_restore_uses_same_user_id_without_prompting_for_mapping() -> None:
    hass = SimpleNamespace(auth=_Auth([SimpleNamespace(id="source-a", name="Same user")]))

    plan = await transfer.async_user_scope_mapping_plan(
        hass, _prepared(), _prepared().available_sections
    )

    assert plan["required_source_user_ids"] == []
    assert plan["missing_source_user_ids"] == []
    assert plan["resolved"] == {"source-a": "source-a"}


async def test_restore_mapping_rewrites_every_selected_user_owned_surface() -> None:
    prepared = _prepared()

    mapped = transfer.apply_user_scope_mappings(
        prepared,
        prepared.available_sections,
        {"source-a": "destination-a"},
    )

    assert mapped.config["voice_default_user_id"] == "destination-a"
    assert mapped.config["voice_device_mappings"]["device-one"] == "user:destination-a"
    assert mapped.raw_configuration["voice_default_user_id"] == "destination-a"
    assert mapped.memories[0].user_id == "destination-a"
    assert mapped.temporary_memories[0].owner_scope_id == "user:destination-a"
    assert mapped.temporary_memories[0].scope_id == "user:destination-a"
    assert mapped.archive_sessions[0].scope_id == "user:destination-a"

    # The parsed upload remains immutable so another preview can choose a different
    # destination without inheriting the previous preview's mapping.
    assert prepared.config["voice_default_user_id"] == "source-a"
    assert prepared.memories[0].user_id == "source-a"


async def test_restore_mapping_rejects_unknown_destination_user() -> None:
    hass = SimpleNamespace(
        auth=_Auth([SimpleNamespace(id="destination-a", name="Destination user")])
    )

    with pytest.raises(backup.BackupError, match="does not exist"):
        await transfer.async_user_scope_mapping_plan(
            hass,
            _prepared(),
            _prepared().available_sections,
            {"source-a": "missing-user"},
        )
