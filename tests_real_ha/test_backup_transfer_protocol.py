"""Real Home Assistant acceptance tests for backup/export/import transport."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import CLIENT_ID, MockConfigEntry, MockUser

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.backup_transfer import (
    WS_BACKUP_TRANSFER,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
    CONF_MEMORY_MODE,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.memory import async_get_memory
from custom_components.extended_openai_conversation_responses.transfer import (
    SECTION_PERSISTENT_MEMORY,
)


def _entry() -> MockConfigEntry:
    """Build one local-only entry with durable Memory enabled."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Backup Transfer Acceptance",
        data={
            CONF_API_KEY: "sk-backup-transfer-acceptance",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {CONF_MEMORY_MODE: MEMORY_MODE_MANUAL},
                "subentry_type": "conversation",
                "title": "Backup Transfer Conversation",
                "unique_id": None,
            }
        ],
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Load the integration through Home Assistant's real config-entry manager."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _conversation_subentry(entry: MockConfigEntry):
    return next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "conversation"
    )


async def _user_token(hass: HomeAssistant, user: MockUser) -> str:
    """Create a real Home Assistant access token for one test user."""
    user.add_to_hass(hass)
    refresh_token = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    return hass.auth.async_create_access_token(refresh_token)


async def _transfer_call(
    client: Any,
    *,
    entry: MockConfigEntry,
    action: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the registered backup-transfer WebSocket command."""
    subentry = _conversation_subentry(entry)
    await client.send_json_auto_id(
        {
            "type": WS_BACKUP_TRANSFER,
            "action": action,
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "data": data or {},
        }
    )
    return await client.receive_json()


async def _download_archive(
    client: Any,
    *,
    entry: MockConfigEntry,
    mode: str,
    sections: list[str] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Drive export_start/export_chunk and return the reconstructed archive."""
    start_data: dict[str, Any] = {"mode": mode}
    if sections is not None:
        start_data["sections"] = sections
    started = await _transfer_call(
        client, entry=entry, action="export_start", data=start_data
    )
    assert started["success"], started
    metadata = started["result"]
    session_id = metadata["session_id"]

    chunks: list[bytes] = []
    try:
        for index in range(metadata["chunk_count"]):
            response = await _transfer_call(
                client,
                entry=entry,
                action="export_chunk",
                data={"session_id": session_id, "index": index},
            )
            assert response["success"], response
            result = response["result"]
            decoded = base64.b64decode(result["data"], validate=True)
            assert len(decoded) == result["bytes"]
            assert result["index"] == index
            assert result["final"] is (index == metadata["chunk_count"] - 1)
            chunks.append(decoded)
    finally:
        cancelled = await _transfer_call(
            client,
            entry=entry,
            action="export_cancel",
            data={"session_id": session_id},
        )
        assert cancelled["success"], cancelled
        assert cancelled["result"] == {"cancelled": True}

    archive = b"".join(chunks)
    assert len(archive) == metadata["size"]
    assert hashlib.sha256(archive).hexdigest() == metadata["sha256"]
    return archive, metadata


async def _upload_archive(
    client: Any,
    *,
    entry: MockConfigEntry,
    archive: bytes,
    filename: str,
) -> str:
    """Drive import_start/import_chunk and return the completed upload session."""
    started = await _transfer_call(
        client,
        entry=entry,
        action="import_start",
        data={"filename": filename, "size": len(archive)},
    )
    assert started["success"], started
    result = started["result"]
    session_id = result["session_id"]
    chunk_size = result["chunk_size"]

    for index, offset in enumerate(range(0, len(archive), chunk_size)):
        chunk = archive[offset : offset + chunk_size]
        response = await _transfer_call(
            client,
            entry=entry,
            action="import_chunk",
            data={
                "session_id": session_id,
                "index": index,
                "data": base64.b64encode(chunk).decode("ascii"),
            },
        )
        assert response["success"], response
        uploaded = response["result"]
        assert uploaded["received"] == min(offset + chunk_size, len(archive))
        assert uploaded["next_index"] == index + 1
        assert uploaded["complete"] is (offset + chunk_size >= len(archive))

    return session_id


@pytest.mark.asyncio
async def test_full_backup_round_trip_through_registered_websocket(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """A real admin client can export, inspect, and atomically restore a full backup."""
    entry = _entry()
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    admin = MockUser(id="backup-admin", name="Backup Admin", is_owner=True)
    admin_client = await hass_ws_client(hass, await _user_token(hass, admin))

    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    owner = "backup-owner"
    created = await memory.async_add(
        owner,
        "Original full-backup acceptance marker.",
        "acceptance",
        "explicit",
    )
    original_id = created["memory"]["memory_id"]

    archive, metadata = await _download_archive(
        admin_client, entry=entry, mode="full"
    )
    assert metadata["mode"] == "full"
    assert metadata["content_type"] == "application/zip"
    assert metadata["filename"].endswith(".zip")

    assert await memory.async_delete(owner, [original_id]) == 1
    replacement = await memory.async_add(
        owner,
        "Post-export state that must be replaced by restore.",
        "acceptance",
        "explicit",
    )
    replacement_id = replacement["memory"]["memory_id"]

    import_session = await _upload_archive(
        admin_client,
        entry=entry,
        archive=archive,
        filename=metadata["filename"],
    )

    inspected = await _transfer_call(
        admin_client,
        entry=entry,
        action="import_inspect",
        data={"session_id": import_session},
    )
    assert inspected["success"], inspected
    inspection = inspected["result"]
    assert inspection["valid"] is True
    assert inspection["source_kind"] == "full_backup"
    assert inspection["summary"]["persistent_memories"] == 1
    assert SECTION_PERSISTENT_MEMORY in inspection["available_sections"]
    assert SECTION_PERSISTENT_MEMORY in inspection["preview"]["selected_sections"]

    restored = await _transfer_call(
        admin_client,
        entry=entry,
        action="import_restore",
        data={"session_id": import_session},
    )
    assert restored["success"], restored
    assert restored["result"]["status"] == "restored"
    assert SECTION_PERSISTENT_MEMORY in restored["result"]["transfer"][
        "selected_sections"
    ]

    memories = await memory.async_list(owner)
    assert [(item.memory_id, item.content) for item in memories] == [
        (original_id, "Original full-backup acceptance marker.")
    ]
    assert all(item.memory_id != replacement_id for item in memories)

    replay = await _transfer_call(
        admin_client,
        entry=entry,
        action="import_restore",
        data={"session_id": import_session},
    )
    assert not replay["success"]
    assert replay["error"]["code"] == "invalid_request"
    assert "expired or does not exist" in replay["error"]["message"]


@pytest.mark.asyncio
async def test_backup_transfer_rejects_non_admin_through_registered_websocket(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """A normal authenticated user is rejected by HA's real admin boundary."""
    entry = _entry()
    await _setup_entry(hass, entry)

    normal_user = MockUser(id="backup-normal", name="Backup Normal User")
    normal_client = await hass_ws_client(
        hass, await _user_token(hass, normal_user)
    )
    denied = await _transfer_call(
        normal_client,
        entry=entry,
        action="export_start",
        data={"mode": "custom", "sections": [SECTION_PERSISTENT_MEMORY]},
    )
    assert not denied["success"], denied
    assert denied["error"]["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_custom_backup_selection_round_trip_through_registered_websocket(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """A custom backup restores only selected sections and preserves configuration."""
    entry = _entry()
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    original_config = agent_config_snapshot(subentry.data)
    owner = "custom-backup-owner"
    original = await memory.async_add(
        owner,
        "Custom backup acceptance marker.",
        "acceptance",
        "explicit",
    )
    original_id = original["memory"]["memory_id"]

    admin = MockUser(id="custom-backup-admin", name="Custom Backup Admin", is_owner=True)
    admin_client = await hass_ws_client(hass, await _user_token(hass, admin))
    archive, metadata = await _download_archive(
        admin_client,
        entry=entry,
        mode="custom",
        sections=[SECTION_PERSISTENT_MEMORY],
    )
    assert metadata["mode"] == "custom"

    assert await memory.async_delete(owner, [original_id]) == 1
    await memory.async_add(
        owner,
        "Custom backup replacement marker.",
        "acceptance",
        "explicit",
    )

    import_session = await _upload_archive(
        admin_client,
        entry=entry,
        archive=archive,
        filename=metadata["filename"],
    )
    try:
        inspected = await _transfer_call(
            admin_client,
            entry=entry,
            action="import_inspect",
            data={"session_id": import_session},
        )
        assert inspected["success"], inspected
        inspection = inspected["result"]
        assert inspection["source_kind"] == "custom_backup"
        assert inspection["available_sections"] == [SECTION_PERSISTENT_MEMORY]
        assert inspection["preview"]["selected_sections"] == [
            SECTION_PERSISTENT_MEMORY
        ]
        assert inspection["can_create_new_agent"] is False

        restored = await _transfer_call(
            admin_client,
            entry=entry,
            action="import_restore",
            data={"session_id": import_session},
        )
        assert restored["success"], restored
        assert restored["result"]["transfer"]["selected_sections"] == [
            SECTION_PERSISTENT_MEMORY
        ]
        import_session = ""
    finally:
        if import_session:
            cancelled = await _transfer_call(
                admin_client,
                entry=entry,
                action="import_cancel",
                data={"session_id": import_session},
            )
            assert cancelled["success"], cancelled

    memories = await memory.async_list(owner)
    assert [(item.memory_id, item.content) for item in memories] == [
        (original_id, "Custom backup acceptance marker.")
    ]

    assert isinstance(subentry.data[CONF_FUNCTION_TOOLS], str)
    assert agent_config_snapshot(subentry.data) == original_config
