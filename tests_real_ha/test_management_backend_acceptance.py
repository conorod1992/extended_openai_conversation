"""Real Home Assistant acceptance tests for the management WebSocket backend."""

from __future__ import annotations

from typing import Any

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import CLIENT_ID, MockConfigEntry, MockUser

from custom_components.extended_openai_conversation_responses import (
    memory as memory_module,
    request_rules as request_rules_module,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
    CONF_MEMORY_MODE,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    FUNCTION_GROUP_LOADING_ON_DEMAND,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    WS_COMMAND,
)


ADMIN_ID = "management-acceptance-admin"


def _entry(title: str = "Management Backend Acceptance") -> MockConfigEntry:
    """Build one local-only conversation entry for management acceptance tests."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            CONF_API_KEY: "sk-management-backend-acceptance",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {CONF_MEMORY_MODE: MEMORY_MODE_MANUAL},
                "subentry_type": "conversation",
                "title": f"{title} Conversation",
                "unique_id": None,
            }
        ],
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Load the integration through Home Assistant's real config-entry manager."""
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _conversation_subentry(entry: MockConfigEntry):
    """Return the entry's conversation subentry."""
    return next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "conversation"
    )


async def _admin_client(hass: HomeAssistant, hass_ws_client: Any) -> Any:
    """Create a genuine authenticated Home Assistant admin WebSocket client."""
    admin = MockUser(id=ADMIN_ID, name="Management Acceptance Admin", is_owner=True)
    admin.add_to_hass(hass)
    refresh_token = await hass.auth.async_create_refresh_token(admin, CLIENT_ID)
    access_token = hass.auth.async_create_access_token(refresh_token)
    return await hass_ws_client(hass, access_token)


async def _management_call(
    client: Any,
    *,
    entry: MockConfigEntry,
    section: str,
    action: str,
    **payload: Any,
) -> dict[str, Any]:
    """Call the registered management command and require a successful response."""
    subentry = _conversation_subentry(entry)
    await client.send_json_auto_id(
        {
            "type": WS_COMMAND,
            "section": section,
            "action": action,
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            **payload,
        }
    )
    response = await client.receive_json()
    assert response["success"], response
    return response["result"]


def _evict_durable_management_managers(hass: HomeAssistant) -> None:
    """Discard process-local manager objects while retaining HA's durable stores."""
    hass.data.pop(memory_module._MEMORY_MANAGERS, None)
    hass.data.pop(request_rules_module._MANAGERS, None)
    hass.data.pop(request_rules_module._RUNTIMES, None)


async def _fresh_reload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Reload the entry with fresh durable managers, approximating a restart boundary."""
    # Config-subentry writes can schedule the integration's update listener. Let any
    # such reload finish before deliberately crossing our own unload/load boundary.
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED

    _evict_durable_management_managers(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.asyncio
async def test_configuration_round_trip_through_management_websocket(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """Configuration written over WS survives a genuine integration reload."""
    entry = _entry("Configuration Acceptance")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)

    before = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    saved = await _management_call(
        client,
        entry=entry,
        section="configuration",
        action="update",
        revision=before["revision"],
        title="Configuration Acceptance Saved",
        config={CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES: 47},
    )
    assert saved["title"] == "Configuration Acceptance Saved"
    assert saved["config"][CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES] == 47

    await _fresh_reload(hass, entry)

    reloaded = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    assert reloaded["title"] == "Configuration Acceptance Saved"
    assert reloaded["config"][CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES] == 47


@pytest.mark.asyncio
async def test_function_tools_and_groups_round_trip_through_management_websocket(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """Function Tool and Group management writes survive a real reload."""
    entry = _entry("Function Acceptance")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)

    tool = {
        "spec": {
            "name": "acceptance_user_lookup",
            "description": "Return the current Home Assistant user's display name.",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "get_user_from_user_id"},
    }
    tool_saved = await _management_call(
        client,
        entry=entry,
        section="tools",
        action="save",
        tool=tool,
    )
    assert any(
        item["spec"]["name"] == "acceptance_user_lookup"
        for item in tool_saved["functions"]
    )

    # The Function Tool write may trigger the config-entry update listener. Wait for
    # that normal lifecycle work before issuing the dependent group mutation.
    await hass.async_block_till_done()

    group = {
        "id": "acceptance_group",
        "name": "Acceptance Group",
        "description": "Functions persisted by the real-HA management acceptance test.",
        "loading_mode": FUNCTION_GROUP_LOADING_ON_DEMAND,
        "functions": ["acceptance_user_lookup"],
        "enabled": True,
    }
    group_saved = await _management_call(
        client,
        entry=entry,
        section="tools",
        action="save_group",
        group=group,
    )
    assert any(
        item["id"] == "acceptance_group"
        and item["functions"] == ["acceptance_user_lookup"]
        for item in group_saved["function_groups"]
    )

    await _fresh_reload(hass, entry)

    reloaded = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    reloaded_tool = next(
        item
        for item in reloaded["config"]["functions"]
        if item["spec"]["name"] == "acceptance_user_lookup"
    )
    assert reloaded_tool["function"] == {
        "type": "native",
        "name": "get_user_from_user_id",
    }
    reloaded_group = next(
        item
        for item in reloaded["config"]["function_groups"]
        if item["id"] == "acceptance_group"
    )
    assert reloaded_group["functions"] == ["acceptance_user_lookup"]
    assert reloaded_group["loading_mode"] == FUNCTION_GROUP_LOADING_ON_DEMAND


@pytest.mark.asyncio
async def test_request_rules_round_trip_through_management_websocket(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """A Request Rule created over WS is rehydrated from storage after reload."""
    entry = _entry("Request Rules Acceptance")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)

    created = await _management_call(
        client,
        entry=entry,
        section="request_rules",
        action="create",
        rule={
            "id": "acceptance-good-night",
            "name": "Acceptance good night",
            "enabled": True,
            "phrases": ["acceptance good night"],
            "match_type": "equals",
            "action_type": "local_action",
            "action": {
                "actions": [
                    {
                        "domain": "script",
                        "service": "turn_on",
                        "target": {"entity_id": ["script.acceptance_goodnight"]},
                        "data": {},
                    }
                ],
                "success_response": "Acceptance complete",
                "failure_response": "Acceptance failed safely",
            },
            "matching_behavior": "defaults",
            "matching": dict(request_rules_module.DEFAULT_MATCHING),
            "order": 0,
        },
    )
    assert created["rule"]["id"] == "acceptance-good-night"

    await _fresh_reload(hass, entry)

    reloaded = await _management_call(
        client, entry=entry, section="request_rules", action="list"
    )
    rule = next(
        item for item in reloaded["rules"] if item["id"] == "acceptance-good-night"
    )
    assert rule["name"] == "Acceptance good night"
    assert rule["phrases"] == ["acceptance good night"]
    assert rule["action"]["success_response"] == "Acceptance complete"


@pytest.mark.asyncio
async def test_memories_round_trip_through_management_websocket(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """A Memory created over WS is rehydrated from storage after reload."""
    entry = _entry("Memory Acceptance")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)

    created = await _management_call(
        client,
        entry=entry,
        section="memories",
        action="add",
        content="Management WebSocket persistence acceptance marker.",
        category="acceptance",
    )
    assert created["memory"]["content"] == (
        "Management WebSocket persistence acceptance marker."
    )

    await _fresh_reload(hass, entry)

    reloaded = await _management_call(
        client, entry=entry, section="memories", action="list"
    )
    assert reloaded["scope_id"] == f"user:{ADMIN_ID}"
    assert [
        (item["content"], item["category"]) for item in reloaded["memories"]
    ] == [("Management WebSocket persistence acceptance marker.", "acceptance")]
