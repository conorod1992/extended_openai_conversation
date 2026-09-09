"""Real Home Assistant acceptance tests for simple user ownership boundaries."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import CLIENT_ID, MockConfigEntry, MockUser

from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_MEMORY_MODE,
    CONF_SKIP_AUTHENTICATION,
    CONF_TEMPORARY_MEMORY,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    MEMORY_MODE_MANUAL,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    async_get_archive,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    WS_COMMAND,
)
from custom_components.extended_openai_conversation_responses.memory import async_get_memory
from custom_components.extended_openai_conversation_responses.scope import user_scope
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    async_get_temporary_memory,
)


def _entry() -> MockConfigEntry:
    """Build one local-only entry with personal retained data enabled."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="User Ownership Acceptance",
        data={
            CONF_API_KEY: "sk-ownership-acceptance",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {
                    CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
                    CONF_ARCHIVE_ENABLED: True,
                    CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
                },
                "subentry_type": "conversation",
                "title": "User Ownership Conversation",
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


async def _normal_user_token(
    hass: HomeAssistant, user_id: str, name: str
) -> tuple[MockUser, str]:
    """Create one non-admin HA user and a real access token for WebSocket auth."""
    user = MockUser(id=user_id, name=name)
    user.add_to_hass(hass)
    refresh_token = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    return user, hass.auth.async_create_access_token(refresh_token)


async def _seed_personal_data(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    user: MockUser,
    memory_text: str,
    conversation_text: str,
) -> tuple[str, str]:
    """Seed one personal memory and one retained archive session for a user."""
    subentry = _conversation_subentry(entry)
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    created = await memory.async_add(user.id, memory_text, "personal", "explicit")
    memory_id = created["memory"]["memory_id"]

    archive = await async_get_archive(hass, entry.entry_id, subentry.subentry_id)
    session = await archive.async_begin_session(
        f"ownership-{user.id}",
        user_scope(user.id, source="authenticated_user"),
        f"conversation-{user.id}",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    assert session is not None
    await archive.async_record_turn(
        session.session_id,
        run_id=f"run-{user.id}",
        user_text=conversation_text,
        assistant_text=f"Reply for {user.name}",
        successful=True,
    )
    return memory_id, session.session_id


async def _seed_temporary_memory(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    user: MockUser,
    content: str,
) -> str:
    """Seed one active Temporary Memory record owned by a user."""
    subentry = _conversation_subentry(entry)
    temporary = await async_get_temporary_memory(
        hass, entry.entry_id, subentry.subentry_id
    )
    created = await temporary.async_add(
        f"conversation:{user.id}",
        content,
        (dt_util.utcnow() + timedelta(hours=1)).isoformat(),
        "general",
        owner_scope_id=f"user:{user.id}",
    )
    return created["memory"]["memory_id"]


async def _management_call(
    client: Any,
    *,
    entry: MockConfigEntry,
    section: str,
    action: str,
    **data: Any,
) -> dict[str, Any]:
    """Call the authenticated management WebSocket and return its HA envelope."""
    subentry = _conversation_subentry(entry)
    await client.send_json_auto_id(
        {
            "type": WS_COMMAND,
            "section": section,
            "action": action,
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            **data,
        }
    )
    return await client.receive_json()


@pytest.mark.asyncio
async def test_management_websocket_lists_only_the_authenticated_users_personal_data(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """Normal users must see only their own personal retained data."""
    entry = _entry()
    await _setup_entry(hass, entry)
    alice, alice_token = await _normal_user_token(hass, "privacy-alice", "Alice")
    bob, bob_token = await _normal_user_token(hass, "privacy-bob", "Bob")

    alice_memory_id, alice_session_id = await _seed_personal_data(
        hass,
        entry,
        user=alice,
        memory_text="Alice keeps the spare key in the blue drawer.",
        conversation_text="Alice private conversation marker.",
    )
    bob_memory_id, bob_session_id = await _seed_personal_data(
        hass,
        entry,
        user=bob,
        memory_text="Bob keeps the spare key in the green drawer.",
        conversation_text="Bob private conversation marker.",
    )
    alice_temporary_id = await _seed_temporary_memory(
        hass,
        entry,
        user=alice,
        content="Alice temporary marker.",
    )
    bob_temporary_id = await _seed_temporary_memory(
        hass,
        entry,
        user=bob,
        content="Bob temporary marker.",
    )

    alice_client = await hass_ws_client(hass, alice_token)
    bob_client = await hass_ws_client(hass, bob_token)

    alice_memories = await _management_call(
        alice_client, entry=entry, section="memories", action="list"
    )
    bob_memories = await _management_call(
        bob_client, entry=entry, section="memories", action="list"
    )
    assert alice_memories["success"]
    assert bob_memories["success"]
    assert [item["memory_id"] for item in alice_memories["result"]["memories"]] == [
        alice_memory_id
    ]
    assert [item["memory_id"] for item in bob_memories["result"]["memories"]] == [
        bob_memory_id
    ]
    assert alice_memories["result"]["scope_id"] == f"user:{alice.id}"
    assert bob_memories["result"]["scope_id"] == f"user:{bob.id}"

    alice_temporary = await _management_call(
        alice_client, entry=entry, section="memories", action="temporary_list"
    )
    bob_temporary = await _management_call(
        bob_client, entry=entry, section="memories", action="temporary_list"
    )
    assert alice_temporary["success"]
    assert bob_temporary["success"]
    assert [
        item["memory_id"] for item in alice_temporary["result"]["memories"]
    ] == [alice_temporary_id]
    assert [item["memory_id"] for item in bob_temporary["result"]["memories"]] == [
        bob_temporary_id
    ]
    assert alice_temporary["result"]["scope_id"] == f"user:{alice.id}"
    assert bob_temporary["result"]["scope_id"] == f"user:{bob.id}"

    alice_conversations = await _management_call(
        alice_client, entry=entry, section="conversations", action="list"
    )
    bob_conversations = await _management_call(
        bob_client, entry=entry, section="conversations", action="list"
    )
    assert alice_conversations["success"]
    assert bob_conversations["success"]
    assert [
        item["session_id"] for item in alice_conversations["result"]["sessions"]
    ] == [alice_session_id]
    assert [item["session_id"] for item in bob_conversations["result"]["sessions"]] == [
        bob_session_id
    ]


@pytest.mark.asyncio
async def test_management_websocket_rejects_explicit_cross_user_scope(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """A normal user cannot select another user's personal management scope."""
    entry = _entry()
    await _setup_entry(hass, entry)
    alice, alice_token = await _normal_user_token(hass, "scope-alice", "Alice")
    bob, _bob_token = await _normal_user_token(hass, "scope-bob", "Bob")
    await _seed_personal_data(
        hass,
        entry,
        user=bob,
        memory_text="Bob private memory.",
        conversation_text="Bob private conversation.",
    )
    await _seed_temporary_memory(
        hass,
        entry,
        user=bob,
        content="Bob private temporary memory.",
    )
    alice_client = await hass_ws_client(hass, alice_token)

    for section in ("memories", "conversations"):
        response = await _management_call(
            alice_client,
            entry=entry,
            section=section,
            action="list",
            scope_id=f"user:{bob.id}",
        )
        assert not response["success"]
        assert response["error"]["code"] == "invalid_request"
        assert "not available to the current user" in response["error"]["message"]

    temporary_response = await _management_call(
        alice_client,
        entry=entry,
        section="memories",
        action="temporary_list",
        scope_id=f"user:{bob.id}",
    )
    assert not temporary_response["success"]
    assert temporary_response["error"]["code"] == "invalid_request"
    assert "not available to the current user" in temporary_response["error"]["message"]

    scopes = await _management_call(
        alice_client, entry=entry, section="scopes", action="catalog"
    )
    assert scopes["success"]
    assert [item["scope_id"] for item in scopes["result"]["scopes"]] == [
        f"user:{alice.id}"
    ]


@pytest.mark.asyncio
async def test_known_cross_user_record_ids_cannot_be_read_or_modified(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """Knowing another user's record IDs must not bypass the personal scope."""
    entry = _entry()
    await _setup_entry(hass, entry)
    alice, alice_token = await _normal_user_token(hass, "idor-alice", "Alice")
    bob, bob_token = await _normal_user_token(hass, "idor-bob", "Bob")
    bob_memory_id, bob_session_id = await _seed_personal_data(
        hass,
        entry,
        user=bob,
        memory_text="Bob IDOR memory marker.",
        conversation_text="Bob IDOR conversation marker.",
    )
    bob_temporary_id = await _seed_temporary_memory(
        hass,
        entry,
        user=bob,
        content="Bob IDOR temporary marker.",
    )
    alice_client = await hass_ws_client(hass, alice_token)
    bob_client = await hass_ws_client(hass, bob_token)

    memory_delete = await _management_call(
        alice_client,
        entry=entry,
        section="memories",
        action="delete",
        memory_id=bob_memory_id,
    )
    assert memory_delete["success"]
    assert memory_delete["result"] == {"deleted": 0}

    temporary_update = await _management_call(
        alice_client,
        entry=entry,
        section="memories",
        action="temporary_update",
        memory_id=bob_temporary_id,
        content="Alice changed Bob's temporary memory.",
    )
    assert not temporary_update["success"]
    assert temporary_update["error"]["code"] == "invalid_request"

    temporary_delete = await _management_call(
        alice_client,
        entry=entry,
        section="memories",
        action="temporary_delete",
        memory_id=bob_temporary_id,
    )
    assert temporary_delete["success"]
    assert temporary_delete["result"] == {"deleted": 0}

    conversation_get = await _management_call(
        alice_client,
        entry=entry,
        section="conversations",
        action="get",
        session_id=bob_session_id,
    )
    assert not conversation_get["success"]
    assert conversation_get["error"]["code"] == "invalid_request"

    conversation_delete = await _management_call(
        alice_client,
        entry=entry,
        section="conversations",
        action="delete",
        session_id=bob_session_id,
    )
    assert not conversation_delete["success"]
    assert conversation_delete["error"]["code"] == "invalid_request"

    bob_memories = await _management_call(
        bob_client, entry=entry, section="memories", action="list"
    )
    assert bob_memories["success"]
    assert [item["memory_id"] for item in bob_memories["result"]["memories"]] == [
        bob_memory_id
    ]

    bob_temporary = await _management_call(
        bob_client, entry=entry, section="memories", action="temporary_list"
    )
    assert bob_temporary["success"]
    assert [item["memory_id"] for item in bob_temporary["result"]["memories"]] == [
        bob_temporary_id
    ]
    assert bob_temporary["result"]["memories"][0]["content"] == (
        "Bob IDOR temporary marker."
    )

    bob_conversation = await _management_call(
        bob_client,
        entry=entry,
        section="conversations",
        action="get",
        session_id=bob_session_id,
    )
    assert bob_conversation["success"]
    assert bob_conversation["result"]["session"]["session_id"] == bob_session_id
    assert bob_conversation["result"]["turns"][0]["user_text"] == (
        "Bob IDOR conversation marker."
    )
