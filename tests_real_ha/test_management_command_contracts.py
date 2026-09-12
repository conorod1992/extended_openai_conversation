"""Real Home Assistant contracts for management WebSocket command boundaries."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    CLIENT_ID,
    MockConfigEntry,
    MockUser,
)

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    configured_function_tools_from_data,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_RETENTION_DAYS,
    CONF_FUNCTION_TOOLS,
    CONF_MEMORY_MODE,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    WS_COMMAND,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

ADMIN_ID = "management-command-admin"
USER_ID = "management-command-user"


def _entry(title: str = "Management Command Contracts") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            CONF_API_KEY: "sk-management-command-contracts",
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
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _subentry(entry: MockConfigEntry):
    return next(
        item
        for item in entry.subentries.values()
        if item.subentry_type == "conversation"
    )


async def _client(
    hass: HomeAssistant,
    hass_ws_client: Any,
    *,
    user_id: str,
    owner: bool,
) -> Any:
    user = MockUser(id=user_id, name=user_id, is_owner=owner)
    user.add_to_hass(hass)
    refresh_token = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    access_token = hass.auth.async_create_access_token(refresh_token)
    return await hass_ws_client(hass, access_token)


async def _raw_call(
    client: Any,
    *,
    entry_id: str,
    subentry_id: str,
    section: str,
    action: str,
    **payload: Any,
) -> dict[str, Any]:
    await client.send_json_auto_id(
        {
            "type": WS_COMMAND,
            "section": section,
            "action": action,
            "entry_id": entry_id,
            "subentry_id": subentry_id,
            **payload,
        }
    )
    return await client.receive_json()


@pytest.mark.asyncio
async def test_management_websocket_reports_invalid_entry_and_subentry_ids(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    entry = _entry("Invalid Target Contracts")
    await _setup_entry(hass, entry)
    client = await _client(hass, hass_ws_client, user_id=ADMIN_ID, owner=True)
    subentry = _subentry(entry)

    missing_entry = await _raw_call(
        client,
        entry_id="missing-entry",
        subentry_id=subentry.subentry_id,
        section="configuration",
        action="get",
    )
    assert missing_entry["success"] is False
    assert missing_entry["error"] == {
        "code": "invalid_request",
        "message": "Integration entry not found",
    }

    missing_subentry = await _raw_call(
        client,
        entry_id=entry.entry_id,
        subentry_id="missing-subentry",
        section="configuration",
        action="get",
    )
    assert missing_subentry["success"] is False
    assert missing_subentry["error"] == {
        "code": "invalid_request",
        "message": "Conversation agent not found",
    }


@pytest.mark.asyncio
async def test_unknown_management_action_returns_one_controlled_error(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    entry = _entry("Unknown Action Contract")
    await _setup_entry(hass, entry)
    client = await _client(hass, hass_ws_client, user_id=ADMIN_ID, owner=True)
    subentry = _subentry(entry)

    response = await _raw_call(
        client,
        entry_id=entry.entry_id,
        subentry_id=subentry.subentry_id,
        section="settings",
        action="unsupported",
    )

    assert response["success"] is False
    assert response["error"] == {
        "code": "invalid_request",
        "message": "Unknown settings management action: unsupported",
    }
    assert "result" not in response


@pytest.mark.asyncio
async def test_non_admin_settings_update_is_denied_without_mutation(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    entry = _entry("Permission Contract")
    await _setup_entry(hass, entry)
    client = await _client(hass, hass_ws_client, user_id=USER_ID, owner=False)
    subentry = _subentry(entry)
    before = dict(subentry.data)

    response = await _raw_call(
        client,
        entry_id=entry.entry_id,
        subentry_id=subentry.subentry_id,
        section="settings",
        action="update",
        settings={CONF_ARCHIVE_RETENTION_DAYS: 17},
    )

    assert response["success"] is False
    assert response["error"]["code"] == "invalid_request"
    assert response["error"]["message"] == "Administrator permission is required"
    current = hass.config_entries.async_get_entry(entry.entry_id)
    assert current is not None
    assert dict(current.subentries[subentry.subentry_id].data) == before


@pytest.mark.asyncio
async def test_mixed_validity_function_tools_update_is_atomic_and_recovers(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    entry = _entry("Function Tool Update Contract")
    await _setup_entry(hass, entry)
    client = await _client(hass, hass_ws_client, user_id=ADMIN_ID, owner=True)
    subentry = _subentry(entry)

    def native_tool(name: str) -> dict[str, Any]:
        return {
            "spec": {
                "name": name,
                "description": f"Return the current user for {name}.",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {"type": "native", "name": "get_user_from_user_id"},
        }

    tool_a = native_tool("saved_user_lookup")
    tool_c = native_tool("additional_user_lookup")
    malformed_b = native_tool("malformed_user_lookup")
    del malformed_b["spec"]["name"]

    current = await _raw_call(
        client,
        entry_id=entry.entry_id,
        subentry_id=subentry.subentry_id,
        section="configuration",
        action="get",
    )
    initial = await _raw_call(
        client,
        entry_id=entry.entry_id,
        subentry_id=subentry.subentry_id,
        section="configuration",
        action="update",
        revision=current["result"]["revision"],
        config={CONF_FUNCTION_TOOLS: [tool_a]},
    )
    assert initial["success"] is True
    await hass.async_block_till_done()

    saved_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert saved_entry is not None
    saved_subentry = saved_entry.subentries[subentry.subentry_id]
    before_data = deepcopy(dict(saved_subentry.data))
    before_revision = initial["result"]["revision"]
    assert configured_function_tools_from_data(saved_subentry.data) == [tool_a]

    rejected = await _raw_call(
        client,
        entry_id=entry.entry_id,
        subentry_id=subentry.subentry_id,
        section="configuration",
        action="update",
        revision=before_revision,
        config={CONF_FUNCTION_TOOLS: [tool_a, tool_c, malformed_b]},
    )

    assert rejected["success"] is False
    assert rejected["error"] == {
        "code": "invalid_request",
        "message": "functions[2].spec.name: is required",
    }
    after_rejection = hass.config_entries.async_get_entry(entry.entry_id)
    assert after_rejection is not None
    rejected_subentry = after_rejection.subentries[subentry.subentry_id]
    assert dict(rejected_subentry.data) == before_data
    assert configured_function_tools_from_data(rejected_subentry.data) == [tool_a]

    unchanged = await _raw_call(
        client,
        entry_id=entry.entry_id,
        subentry_id=subentry.subentry_id,
        section="configuration",
        action="get",
    )
    assert unchanged["success"] is True
    assert unchanged["result"]["revision"] == before_revision

    corrected = await _raw_call(
        client,
        entry_id=entry.entry_id,
        subentry_id=subentry.subentry_id,
        section="configuration",
        action="update",
        revision=before_revision,
        config={CONF_FUNCTION_TOOLS: [tool_a, tool_c]},
    )
    assert corrected["success"] is True
    assert corrected["result"]["revision"] != before_revision
    assert corrected["result"]["config"][CONF_FUNCTION_TOOLS] == [tool_a, tool_c]

    recovered_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert recovered_entry is not None
    recovered_subentry = recovered_entry.subentries[subentry.subentry_id]
    assert configured_function_tools_from_data(recovered_subentry.data) == [
        tool_a,
        tool_c,
    ]


@pytest.mark.asyncio
async def test_configuration_speech_preview_returns_exact_success_payload(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    entry = _entry("Speech Preview Contract")
    await _setup_entry(hass, entry)
    client = await _client(hass, hass_ws_client, user_id=ADMIN_ID, owner=True)
    subentry = _subentry(entry)
    before = dict(subentry.data)

    response = await _raw_call(
        client,
        entry_id=entry.entry_id,
        subentry_id=subentry.subentry_id,
        section="configuration",
        action="speech_preview",
        sample_text="Exact preview response",
        config={},
    )

    assert response["success"] is True
    assert response["result"] == {"speech_text": "Exact preview response"}
    current = hass.config_entries.async_get_entry(entry.entry_id)
    assert current is not None
    assert dict(current.subentries[subentry.subentry_id].data) == before


@pytest.mark.asyncio
async def test_settings_update_returns_snapshot_and_mutates_canonical_subentry(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    entry = _entry("Settings Mutation Contract")
    await _setup_entry(hass, entry)
    client = await _client(hass, hass_ws_client, user_id=ADMIN_ID, owner=True)
    subentry = _subentry(entry)

    response = await _raw_call(
        client,
        entry_id=entry.entry_id,
        subentry_id=subentry.subentry_id,
        section="settings",
        action="update",
        settings={CONF_ARCHIVE_RETENTION_DAYS: 90},
    )

    assert response["success"] is True
    assert response["result"]["settings"][CONF_ARCHIVE_RETENTION_DAYS] == 90
    current = hass.config_entries.async_get_entry(entry.entry_id)
    assert current is not None
    assert (
        current.subentries[subentry.subentry_id].data[CONF_ARCHIVE_RETENTION_DAYS] == 90
    )


@pytest.mark.asyncio
async def test_dependency_runtime_error_is_translated_to_websocket_error(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry("Dependency Failure Contract")
    await _setup_entry(hass, entry)
    client = await _client(hass, hass_ws_client, user_id=ADMIN_ID, owner=True)
    subentry = _subentry(entry)

    async def _fail_knowledge(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("knowledge runtime unavailable")

    monkeypatch.setattr(management_ui, "async_get_knowledge", _fail_knowledge)

    response = await _raw_call(
        client,
        entry_id=entry.entry_id,
        subentry_id=subentry.subentry_id,
        section="knowledge",
        action="list",
    )

    assert response["success"] is False
    assert response["error"] == {
        "code": "invalid_request",
        "message": "knowledge runtime unavailable",
    }
    assert "result" not in response


@pytest.mark.asyncio
async def test_dependency_value_error_is_translated_to_websocket_error(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry("Dependency Value Error Contract")
    await _setup_entry(hass, entry)
    client = await _client(hass, hass_ws_client, user_id=ADMIN_ID, owner=True)
    subentry = _subentry(entry)

    async def _fail_knowledge(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("knowledge state is invalid")

    monkeypatch.setattr(management_ui, "async_get_knowledge", _fail_knowledge)

    response = await _raw_call(
        client,
        entry_id=entry.entry_id,
        subentry_id=subentry.subentry_id,
        section="knowledge",
        action="list",
    )

    assert response["success"] is False
    assert response["error"] == {
        "code": "invalid_request",
        "message": "knowledge state is invalid",
    }
    assert "result" not in response
