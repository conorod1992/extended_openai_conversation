"""Focused tests for Knowledge Library management UI behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import knowledge_ui
from custom_components.extended_openai_conversation_responses.const import DOMAIN


def _subentry(
    subentry_id: str = "agent-1",
    *,
    subentry_type: str = "conversation",
    knowledge_enabled: bool = True,
):
    return SimpleNamespace(
        subentry_id=subentry_id,
        subentry_type=subentry_type,
        title="Agent",
        data={"knowledge_enabled": knowledge_enabled},
    )


def _entry(
    entry_id: str = "entry-1",
    *,
    domain: str = DOMAIN,
    subentry=None,
):
    subentry = subentry or _subentry()
    return SimpleNamespace(
        entry_id=entry_id,
        domain=domain,
        title="OpenAI",
        subentries={subentry.subentry_id: subentry},
    )


def _hass(entry=None):
    entry = entry or _entry()
    config_entries = SimpleNamespace(
        async_get_entry=Mock(return_value=entry),
        async_entries=Mock(return_value=[entry]),
    )
    return SimpleNamespace(config_entries=config_entries, data={}, http=SimpleNamespace())


def test_entry_and_agent_rejects_wrong_entry_or_subentry() -> None:
    hass = _hass(_entry(domain="other"))
    with pytest.raises(HomeAssistantError, match="Integration entry not found"):
        knowledge_ui._entry_and_agent(hass, "entry-1", "agent-1")

    hass = _hass(_entry(subentry=_subentry(subentry_type="other")))
    with pytest.raises(HomeAssistantError, match="Conversation agent not found"):
        knowledge_ui._entry_and_agent(hass, "entry-1", "agent-1")


def test_agents_lists_only_conversation_subentries_and_feature_state() -> None:
    conversation = _subentry("conversation", knowledge_enabled=False)
    other = _subentry("other", subentry_type="something_else")
    entry = _entry(subentry=conversation)
    entry.subentries[other.subentry_id] = other
    hass = _hass(entry)

    result = pytest.run(async_fn=knowledge_ui.async_manage_knowledge_command) if False else None


@pytest.mark.asyncio
async def test_agents_lists_only_conversation_subentries_and_feature_state_async() -> None:
    conversation = _subentry("conversation", knowledge_enabled=False)
    other = _subentry("other", subentry_type="something_else")
    entry = _entry(subentry=conversation)
    entry.subentries[other.subentry_id] = other
    hass = _hass(entry)

    result = await knowledge_ui.async_manage_knowledge_command(hass, {"action": "agents"})

    assert result == {
        "agents": [
            {
                "entry_id": "entry-1",
                "entry_title": "OpenAI",
                "subentry_id": "conversation",
                "title": "Agent",
                "knowledge_enabled": False,
            }
        ]
    }


@pytest.mark.asyncio
async def test_non_agent_actions_require_entry_and_subentry_ids() -> None:
    with pytest.raises(HomeAssistantError, match="entry_id and subentry_id are required"):
        await knowledge_ui.async_manage_knowledge_command(_hass(), {"action": "list"})


@pytest.mark.asyncio
async def test_list_get_create_update_and_delete_route_to_library(monkeypatch) -> None:
    library = SimpleNamespace(
        async_list=AsyncMock(return_value=[{"id": "source-1"}]),
        stats=Mock(return_value={"source_count": 1}),
        async_get=AsyncMock(return_value=object()),
        async_create=AsyncMock(return_value=object()),
        async_update=AsyncMock(return_value=object()),
        async_delete=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(knowledge_ui, "async_get_knowledge", AsyncMock(return_value=library))
    monkeypatch.setattr(
        knowledge_ui,
        "knowledge_source_as_dict",
        lambda source: {"converted": id(source)},
    )
    hass = _hass()
    base = {"entry_id": "entry-1", "subentry_id": "agent-1"}

    assert await knowledge_ui.async_manage_knowledge_command(
        hass, {**base, "action": "list"}
    ) == {"sources": [{"id": "source-1"}], "stats": {"source_count": 1}}

    got = await knowledge_ui.async_manage_knowledge_command(
        hass, {**base, "action": "get", "source_id": "source-1"}
    )
    assert "source" in got
    library.async_get.assert_awaited_once_with("source-1")

    created = await knowledge_ui.async_manage_knowledge_command(
        hass,
        {
            **base,
            "action": "create",
            "title": "Title",
            "description": "Description",
            "content": "Content",
            "enabled": False,
        },
    )
    assert created["status"] == "created"
    library.async_create.assert_awaited_once_with(
        "Title", "Description", "Content", False
    )

    updated = await knowledge_ui.async_manage_knowledge_command(
        hass,
        {
            **base,
            "action": "update",
            "source_id": "source-1",
            "title": "New title",
        },
    )
    assert updated["status"] == "updated"
    library.async_update.assert_awaited_once_with(
        "source-1", "New title", None, None, None
    )

    assert await knowledge_ui.async_manage_knowledge_command(
        hass,
        {**base, "action": "delete", "source_id": "source-1", "confirm": True},
    ) == {"deleted": 1}
    library.async_delete.assert_awaited_once_with("source-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["get", "update"])
async def test_source_actions_require_source_id(monkeypatch, action: str) -> None:
    monkeypatch.setattr(
        knowledge_ui,
        "async_get_knowledge",
        AsyncMock(return_value=SimpleNamespace()),
    )
    with pytest.raises(HomeAssistantError, match="source_id is required"):
        await knowledge_ui.async_manage_knowledge_command(
            _hass(),
            {"action": action, "entry_id": "entry-1", "subentry_id": "agent-1"},
        )


@pytest.mark.asyncio
async def test_delete_requires_explicit_confirmation_before_source_id(monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_ui,
        "async_get_knowledge",
        AsyncMock(return_value=SimpleNamespace()),
    )
    base = {"action": "delete", "entry_id": "entry-1", "subentry_id": "agent-1"}

    with pytest.raises(HomeAssistantError, match="Explicit confirmation is required"):
        await knowledge_ui.async_manage_knowledge_command(_hass(), base)

    with pytest.raises(HomeAssistantError, match="source_id is required"):
        await knowledge_ui.async_manage_knowledge_command(
            _hass(), {**base, "confirm": True}
        )


@pytest.mark.asyncio
async def test_unknown_action_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_ui,
        "async_get_knowledge",
        AsyncMock(return_value=SimpleNamespace()),
    )
    with pytest.raises(HomeAssistantError, match="Unknown knowledge management action"):
        await knowledge_ui.async_manage_knowledge_command(
            _hass(),
            {
                "action": "explode",
                "entry_id": "entry-1",
                "subentry_id": "agent-1",
            },
        )


@pytest.mark.asyncio
async def test_websocket_reports_errors_and_results(monkeypatch) -> None:
    connection = SimpleNamespace(send_error=Mock(), send_result=Mock())
    monkeypatch.setattr(
        knowledge_ui,
        "async_manage_knowledge_command",
        AsyncMock(side_effect=HomeAssistantError("bad request")),
    )
    await knowledge_ui.websocket_manage_knowledge(
        _hass(), connection, {"id": 7, "action": "agents", "type": knowledge_ui.WS_COMMAND}
    )
    connection.send_error.assert_called_once_with(7, "invalid_request", "bad request")
    connection.send_result.assert_not_called()

    connection.send_error.reset_mock()
    connection.send_result.reset_mock()
    knowledge_ui.async_manage_knowledge_command = AsyncMock(return_value={"ok": True})
    await knowledge_ui.websocket_manage_knowledge(
        _hass(), connection, {"id": 8, "action": "agents", "type": knowledge_ui.WS_COMMAND}
    )
    connection.send_result.assert_called_once_with(8, {"ok": True})
    connection.send_error.assert_not_called()


@pytest.mark.asyncio
async def test_setup_registers_static_asset_command_and_panel_once(monkeypatch) -> None:
    register_static = AsyncMock()
    hass = _hass()
    hass.http.async_register_static_paths = register_static
    register_command = Mock()
    register_panel = AsyncMock()
    monkeypatch.setattr(knowledge_ui.websocket_api, "async_register_command", register_command)
    monkeypatch.setattr(knowledge_ui.panel_custom, "async_register_panel", register_panel)

    await knowledge_ui.async_setup_knowledge_ui(hass)
    await knowledge_ui.async_setup_knowledge_ui(hass)

    assert register_static.await_count == 1
    assert register_command.call_count == 1
    assert register_panel.await_count == 1
    static_path = register_static.await_args.args[0][0]
    assert static_path.url_path == f"/{DOMAIN}/knowledge-panel.js"
    assert static_path.cache_headers is False
    assert register_panel.await_args.kwargs["require_admin"] is False
