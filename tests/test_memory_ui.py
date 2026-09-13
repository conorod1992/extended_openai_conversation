"""Tests for the memory management UI backend."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import memory_ui
from custom_components.extended_openai_conversation_responses.const import (
    CONF_SHARED_MEMORY_MODE,
    CONF_TEMPORARY_MEMORY,
    DOMAIN,
    SHARED_MEMORY_EXPLICIT,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.scope import (
    SHARED_HOUSEHOLD_SCOPE_ID,
)


def _agent(*, data: dict | None = None):
    entry = SimpleNamespace(entry_id="entry-1", title="Entry")
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        title="Agent",
        subentry_type="conversation",
        data=data or {},
    )
    return entry, subentry


def _install_agent(monkeypatch, *, data: dict | None = None):
    entry, subentry = _agent(data=data)
    monkeypatch.setattr(
        memory_ui,
        "_entry_and_agent",
        lambda hass, entry_id, subentry_id: (entry, subentry),
    )
    return entry, subentry


async def _manage(hass, action: str, **message):
    if not hasattr(hass, "data"):
        hass.data = {}
    return await memory_ui.async_manage_command(
        hass,
        "user-1",
        {
            "action": action,
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            **message,
        },
    )


def test_entry_and_agent_rejects_wrong_entry_or_subentry() -> None:
    """Exact integration and conversation identifiers are required."""
    config_entries = SimpleNamespace(async_get_entry=Mock(return_value=None))
    hass = SimpleNamespace(config_entries=config_entries)

    with pytest.raises(HomeAssistantError, match="Integration entry not found"):
        memory_ui._entry_and_agent(hass, "entry-1", "agent-1")

    entry = SimpleNamespace(domain=DOMAIN, subentries={})
    config_entries.async_get_entry.return_value = entry
    with pytest.raises(HomeAssistantError, match="Conversation agent not found"):
        memory_ui._entry_and_agent(hass, "entry-1", "agent-1")

    entry.subentries["agent-1"] = SimpleNamespace(subentry_type="not-conversation")
    with pytest.raises(HomeAssistantError, match="Conversation agent not found"):
        memory_ui._entry_and_agent(hass, "entry-1", "agent-1")


@pytest.mark.asyncio
async def test_memory_owner_requires_one_readable_match() -> None:
    """Owner resolution rejects missing identifiers and ambiguous lookups."""
    memory = SimpleNamespace(async_get_many=AsyncMock())

    with pytest.raises(HomeAssistantError, match="memory_id is required"):
        await memory_ui._async_memory_owner(memory, ["user-1"], "")

    memory.async_get_many.return_value = []
    with pytest.raises(HomeAssistantError, match="Memory not found"):
        await memory_ui._async_memory_owner(memory, ["user-1"], "memory-1")

    memory.async_get_many.return_value = [
        SimpleNamespace(user_id="user-1"),
        SimpleNamespace(user_id=SHARED_HOUSEHOLD_SCOPE_ID),
    ]
    with pytest.raises(HomeAssistantError, match="Memory not found"):
        await memory_ui._async_memory_owner(
            memory,
            ["user-1", SHARED_HOUSEHOLD_SCOPE_ID],
            "memory-1",
        )

    memory.async_get_many.return_value = [SimpleNamespace(user_id="user-1")]
    assert (
        await memory_ui._async_memory_owner(memory, ["user-1"], "memory-1")
        == "user-1"
    )


@pytest.mark.asyncio
async def test_manage_agents_lists_only_conversation_subentries(monkeypatch) -> None:
    """Agent discovery reports memory capabilities for conversation agents."""
    enabled = SimpleNamespace(
        subentry_id="agent-1",
        title="Enabled",
        subentry_type="conversation",
        data={
            CONF_SHARED_MEMORY_MODE: SHARED_MEMORY_EXPLICIT,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
        },
    )
    ignored = SimpleNamespace(
        subentry_id="task-1",
        title="Task",
        subentry_type="ai_task_data",
        data={},
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        title="Entry",
        subentries={"agent-1": enabled, "task-1": ignored},
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=Mock(return_value=[entry]))
    )
    monkeypatch.setattr(memory_ui, "get_memory_mode", Mock(return_value="manual"))

    result = await memory_ui.async_manage_command(hass, "user-1", {"action": "agents"})

    assert result == {
        "agents": [
            {
                "entry_id": "entry-1",
                "entry_title": "Entry",
                "subentry_id": "agent-1",
                "title": "Enabled",
                "memory_mode": "manual",
                "shared_memory_enabled": True,
                "temporary_memory_enabled": True,
            }
        ]
    }


@pytest.mark.asyncio
async def test_manage_requires_agent_ids_and_dispatches_agent_test(monkeypatch) -> None:
    """Non-discovery actions require exact agent IDs and dispatch tests."""
    hass = SimpleNamespace()
    with pytest.raises(HomeAssistantError, match="entry_id and subentry_id are required"):
        await memory_ui.async_manage_command(hass, "user-1", {"action": "list"})

    entry, subentry = _install_agent(monkeypatch)
    result = SimpleNamespace(as_dict=Mock(return_value={"status": "ok"}))
    agent_test = AsyncMock(return_value=result)
    monkeypatch.setattr(memory_ui, "async_test_agent", agent_test)

    assert await _manage(hass, "test_agent") == {"status": "ok"}
    agent_test.assert_awaited_once_with(hass, entry, subentry)


@pytest.mark.asyncio
async def test_temporary_delete_and_clear_are_user_scoped(monkeypatch) -> None:
    """Temporary mutations require enablement, confirmation, and user ownership."""
    hass = SimpleNamespace()
    _install_agent(monkeypatch)
    with pytest.raises(HomeAssistantError, match="Temporary Memory is disabled"):
        await _manage(hass, "temporary_delete", memory_id="temporary-1")

    _install_agent(
        monkeypatch,
        data={CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED},
    )
    temporary = SimpleNamespace(
        async_delete=AsyncMock(return_value=1),
        async_list_all=AsyncMock(
            return_value=[
                SimpleNamespace(scope_id="user:user-1", memory_id="temporary-1"),
                SimpleNamespace(scope_id="user:user-2", memory_id="temporary-other"),
            ]
        ),
    )
    monkeypatch.setattr(
        memory_ui,
        "async_get_temporary_memory",
        AsyncMock(return_value=temporary),
    )

    with pytest.raises(HomeAssistantError, match="memory_id is required"):
        await _manage(hass, "temporary_delete")

    assert await _manage(
        hass, "temporary_delete", memory_id="temporary-1"
    ) == {"deleted": 1}
    temporary.async_delete.assert_awaited_with(
        "user:user-1", ["temporary-1"]
    )

    with pytest.raises(HomeAssistantError, match="Explicit confirmation is required"):
        await _manage(hass, "temporary_clear")

    temporary.async_delete.reset_mock()
    assert await _manage(hass, "temporary_clear", confirm=True) == {"deleted": 1}
    temporary.async_delete.assert_awaited_once_with(
        "user:user-1", ["temporary-1"]
    )


@pytest.mark.asyncio
async def test_list_validates_paging_and_includes_first_page_temporary_memory(
    monkeypatch,
) -> None:
    """List validates paging and exposes temporary memory only on page zero."""
    hass = SimpleNamespace()
    _install_agent(
        monkeypatch,
        data={
            CONF_SHARED_MEMORY_MODE: SHARED_MEMORY_EXPLICIT,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
        },
    )
    persistent_record = SimpleNamespace(memory_id="persistent-1")
    memory = SimpleNamespace(async_list=AsyncMock(return_value=[persistent_record]))
    monkeypatch.setattr(memory_ui, "async_get_memory", AsyncMock(return_value=memory))
    monkeypatch.setattr(
        memory_ui,
        "_memory_ui_dict",
        lambda record, user_id: {"memory_id": record.memory_id, "user": user_id},
    )
    temporary_record = SimpleNamespace(
        scope_id="user:user-1", memory_id="temporary-1"
    )
    temporary = SimpleNamespace(
        async_list_all=AsyncMock(return_value=[temporary_record])
    )
    temporary_get = AsyncMock(return_value=temporary)
    monkeypatch.setattr(memory_ui, "async_get_temporary_memory", temporary_get)
    monkeypatch.setattr(
        memory_ui,
        "temporary_memory_as_dict",
        lambda record: {"memory_id": record.memory_id},
    )

    with pytest.raises(HomeAssistantError, match="scope must be personal or household"):
        await _manage(hass, "list", scope="invalid")
    with pytest.raises(HomeAssistantError, match="limit must be"):
        await _manage(hass, "list", limit=True)
    with pytest.raises(HomeAssistantError, match="offset must be zero or greater"):
        await _manage(hass, "list", offset=-1)

    first = await _manage(hass, "list", limit=1, offset=0)
    assert first == {
        "memories": [{"memory_id": "persistent-1", "user": "user-1"}],
        "temporary_memories": [{"memory_id": "temporary-1"}],
        "next_offset": 1,
    }
    memory.async_list.assert_awaited_with(
        ["user-1", SHARED_HOUSEHOLD_SCOPE_ID], None, 1, 0
    )

    memory.async_list.return_value = []
    second = await _manage(hass, "list", limit=1, offset=1)
    assert second["temporary_memories"] == []
    assert second["next_offset"] is None
    temporary_get.assert_awaited_once()


@pytest.mark.asyncio
async def test_household_scope_requires_shared_memory(monkeypatch) -> None:
    """Household writes cannot bypass an agent with shared memory disabled."""
    hass = SimpleNamespace()
    _install_agent(monkeypatch)
    monkeypatch.setattr(
        memory_ui,
        "async_get_memory",
        AsyncMock(return_value=SimpleNamespace()),
    )

    with pytest.raises(HomeAssistantError, match="Shared household memory is disabled"):
        await _manage(hass, "add", scope="household", content="Shared fact")


@pytest.mark.asyncio
async def test_add_forwards_optional_metadata_only_when_present(monkeypatch) -> None:
    """Add preserves the legacy call shape unless metadata was supplied."""
    hass = SimpleNamespace()
    _install_agent(monkeypatch)
    memory = SimpleNamespace(async_add=AsyncMock(return_value={"status": "created"}))
    monkeypatch.setattr(memory_ui, "async_get_memory", AsyncMock(return_value=memory))

    assert await _manage(hass, "add", content="Fact", category="pets") == {
        "status": "created"
    }
    memory.async_add.assert_awaited_once_with("user-1", "Fact", "pets", "explicit")

    memory.async_add.reset_mock()
    await _manage(
        hass,
        "add",
        content="Fact",
        importance="high",
        subject="Oscar",
        key="pet.oscar",
        valid_from="2026-09-13",
    )
    memory.async_add.assert_awaited_once_with(
        "user-1",
        "Fact",
        "general",
        "explicit",
        "high",
        "Oscar",
        "pet.oscar",
        "2026-09-13",
    )


@pytest.mark.asyncio
async def test_update_validates_controls_and_can_move_to_household(monkeypatch) -> None:
    """Update validates management-only fields before forwarding a mutation."""
    hass = SimpleNamespace()
    _install_agent(
        monkeypatch,
        data={CONF_SHARED_MEMORY_MODE: SHARED_MEMORY_EXPLICIT},
    )
    updated_record = SimpleNamespace(memory_id="memory-1")
    memory = SimpleNamespace(async_update=AsyncMock(return_value=updated_record))
    monkeypatch.setattr(memory_ui, "async_get_memory", AsyncMock(return_value=memory))
    monkeypatch.setattr(
        memory_ui,
        "_async_memory_owner",
        AsyncMock(return_value="user-1"),
    )
    monkeypatch.setattr(
        memory_ui,
        "_memory_ui_dict",
        lambda record, user_id: {"memory_id": record.memory_id, "user": user_id},
    )

    with pytest.raises(HomeAssistantError, match="memory_id is required"):
        await _manage(hass, "update")
    with pytest.raises(HomeAssistantError, match="clear_fields must be a list"):
        await _manage(hass, "update", memory_id="memory-1", clear_fields="subject")
    with pytest.raises(HomeAssistantError, match="expected_revision must be a string"):
        await _manage(hass, "update", memory_id="memory-1", expected_revision=1)
    with pytest.raises(
        HomeAssistantError, match="refresh_confirmation must be true or false"
    ):
        await _manage(hass, "update", memory_id="memory-1", refresh_confirmation="yes")

    result = await _manage(
        hass,
        "update",
        memory_id="memory-1",
        scope="household",
        content="Updated fact",
        clear_fields=["subject"],
        expected_revision="revision-1",
        refresh_confirmation=True,
    )
    assert result == {
        "status": "updated",
        "memory": {"memory_id": "memory-1", "user": "user-1"},
    }
    memory.async_update.assert_awaited_once_with(
        "user-1",
        "memory-1",
        content="Updated fact",
        category=None,
        importance=None,
        subject=None,
        key=None,
        valid_from=None,
        refresh_confirmation=True,
        target_user_id=SHARED_HOUSEHOLD_SCOPE_ID,
        clear_fields=["subject"],
        expected_revision="revision-1",
    )


@pytest.mark.asyncio
async def test_delete_clear_and_unknown_action_paths(monkeypatch) -> None:
    """Delete and clear enforce ownership, confirmation, and category typing."""
    hass = SimpleNamespace()
    _install_agent(monkeypatch)
    memory = SimpleNamespace(
        async_delete=AsyncMock(return_value=1),
        async_clear=AsyncMock(return_value=2),
    )
    monkeypatch.setattr(memory_ui, "async_get_memory", AsyncMock(return_value=memory))
    owner = AsyncMock(return_value="user-1")
    monkeypatch.setattr(memory_ui, "_async_memory_owner", owner)

    with pytest.raises(HomeAssistantError, match="memory_id is required"):
        await _manage(hass, "delete")
    assert await _manage(hass, "delete", memory_id="memory-1") == {"deleted": 1}
    memory.async_delete.assert_awaited_once_with("user-1", ["memory-1"])

    with pytest.raises(HomeAssistantError, match="Explicit confirmation is required"):
        await _manage(hass, "clear")
    with pytest.raises(HomeAssistantError, match="category must be a string"):
        await _manage(hass, "clear", confirm=True, category=1)
    assert await _manage(hass, "clear", confirm=True, category="pets") == {
        "deleted": 2
    }
    memory.async_clear.assert_awaited_once_with("user-1", "pets")

    with pytest.raises(HomeAssistantError, match="Unknown management action"):
        await _manage(hass, "not-real")


@pytest.mark.asyncio
async def test_websocket_manage_translates_errors_and_returns_results(
    monkeypatch,
) -> None:
    """The authenticated WebSocket boundary translates expected failures."""
    connection = SimpleNamespace(
        user=SimpleNamespace(id="user-1"),
        send_error=Mock(),
        send_result=Mock(),
    )
    handler = getattr(memory_ui.websocket_manage, "__wrapped__")
    manage = AsyncMock(side_effect=ValueError("bad request"))
    monkeypatch.setattr(memory_ui, "async_manage_command", manage)

    await handler(SimpleNamespace(), connection, {"id": 1, "action": "list"})
    connection.send_error.assert_called_once_with(1, "invalid_request", "bad request")
    connection.send_result.assert_not_called()

    manage.side_effect = None
    manage.return_value = {"memories": []}
    await handler(SimpleNamespace(), connection, {"id": 2, "action": "list"})
    connection.send_result.assert_called_once_with(2, {"memories": []})


@pytest.mark.asyncio
async def test_setup_memory_ui_registers_once(monkeypatch) -> None:
    """Static assets, command, and panel are installed idempotently."""
    register_paths = AsyncMock()
    hass = SimpleNamespace(
        data={},
        http=SimpleNamespace(async_register_static_paths=register_paths),
    )
    static_path = Mock(side_effect=lambda url, path, cache_headers: (url, path, cache_headers))
    register_command = Mock()
    register_panel = AsyncMock()
    monkeypatch.setattr(memory_ui, "StaticPathConfig", static_path)
    monkeypatch.setattr(memory_ui.websocket_api, "async_register_command", register_command)
    monkeypatch.setattr(memory_ui.panel_custom, "async_register_panel", register_panel)

    await memory_ui.async_setup_memory_ui(hass)
    await memory_ui.async_setup_memory_ui(hass)

    assert hass.data[memory_ui._UI_SETUP] is True
    assert static_path.call_count == 2
    register_paths.assert_awaited_once()
    register_command.assert_called_once_with(hass, memory_ui.websocket_manage)
    register_panel.assert_awaited_once_with(
        hass,
        webcomponent_name="extended-openai-memory-management-panel",
        frontend_url_path=memory_ui.MEMORY_PANEL_URL,
        module_url=f"/{DOMAIN}/memory-management-panel.js",
        sidebar_title=memory_ui.MEMORY_PANEL_TITLE,
        sidebar_icon="mdi:brain",
        require_admin=False,
    )