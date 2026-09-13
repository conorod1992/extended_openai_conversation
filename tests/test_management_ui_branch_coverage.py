"""Branch-focused coverage for the management UI command boundary."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    ToolSnapshot,
)
from homeassistant.exceptions import HomeAssistantError


def _entry_pair():
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Jarvis",
        data=agent_config_defaults(),
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        domain=DOMAIN,
        title="Provider",
        data={},
        subentries={subentry.subentry_id: subentry},
    )
    return entry, subentry


def _hass(entry=None):
    config_entries = MagicMock()
    config_entries.async_get_entry.return_value = entry
    return SimpleNamespace(
        config_entries=config_entries,
        auth=SimpleNamespace(),
        config=SimpleNamespace(language="en"),
        data={},
        services=SimpleNamespace(async_call=AsyncMock()),
    )


def _message(section: str, action: str, **values):
    return {
        "section": section,
        "action": action,
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        **values,
    }


def _tool(name: str) -> dict:
    return {
        "spec": {
            "name": name,
            "description": "",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "script", "sequence": []},
        "enabled": True,
    }


@pytest.mark.asyncio
async def test_effective_request_preview_covers_discovery_guest_filters_and_notes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry, subentry = _entry_pair()
    hass = _hass(entry)
    hass.config.language = "en"
    manager = SimpleNamespace(status=lambda: {"active": False})
    monkeypatch.setattr(
        management_ui, "async_get_guest_mode", AsyncMock(return_value=manager)
    )
    monkeypatch.setattr(management_ui, "get_loaded_temporary_memory", lambda *_: None)
    monkeypatch.setattr(
        management_ui,
        "async_read_temporary_memory_snapshot",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(management_ui.SkillManager, "get_loaded_instance", lambda: None)
    monkeypatch.setattr(management_ui, "get_loaded_knowledge", lambda *_: None)
    monkeypatch.setattr(
        management_ui,
        "render_effective_prompt",
        lambda *_args, **_kwargs: SimpleNamespace(text="prompt", sections=[]),
    )
    monkeypatch.setattr(
        management_ui,
        "build_provider_request_snapshot",
        lambda *_args: SimpleNamespace(
            api_mode="responses", api_kwargs={}, provider_tools=[]
        ),
    )
    monkeypatch.setattr(
        management_ui, "format_function_tools", lambda tools, _mode: tools
    )
    monkeypatch.setattr(
        management_ui,
        "assemble_integration_function_tools",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(management_ui, "memory_enabled", lambda _options: False)

    ha_tool = {
        "spec": {"name": "ha_light"},
        "function": {"type": "ha_llm", "source_id": "assist", "tool_name": "Light"},
        "enabled": True,
    }
    custom = _tool("allowed")
    options = {
        **agent_config_defaults(),
        management_ui.CONF_TEMPORARY_MEMORY: "balanced",
        management_ui.CONF_MEMORY_AUTO_RETRIEVE_LIMIT: 0,
    }
    configured = [ha_tool, custom]
    monkeypatch.setattr(
        management_ui, "configured_function_tools_from_data", lambda _data: configured
    )
    discover = AsyncMock(return_value=ToolSnapshot())
    monkeypatch.setattr(management_ui, "async_discover", discover)
    monkeypatch.setattr(
        management_ui,
        "resolve_guest_policy",
        lambda *_: GuestCapabilityPolicy.unrestricted(),
    )
    monkeypatch.setattr(management_ui, "validate_function_groups", lambda *_: [])
    monkeypatch.setattr(
        management_ui,
        "assemble_function_tools",
        lambda tools, *_args: SimpleNamespace(tools=tools),
    )
    monkeypatch.setattr(management_ui, "get_exposed_entities", lambda _hass: [])

    trusted = await management_ui._async_preview_effective_request(
        hass, entry, subentry, options, "user"
    )
    assert trusted["prompt"] == "prompt"
    assert not any(note.startswith("Query-derived") for note in trusted["notes"])
    discover.assert_awaited_once()

    guest_policy = GuestCapabilityPolicy(
        True,
        readable_entity_ids=frozenset({"sensor.allowed"}),
        configured_tool_names=frozenset({"allowed"}),
    )
    groups = [
        {"id": "public", "guest_allowed": True, "functions": ["allowed", "blocked"]},
        {"id": "private", "guest_allowed": False, "functions": ["private"]},
    ]
    guest_tools = [custom, _tool("blocked"), _tool("private"), ha_tool]
    monkeypatch.setattr(
        management_ui, "configured_function_tools_from_data", lambda _data: guest_tools
    )
    monkeypatch.setattr(management_ui, "resolve_guest_policy", lambda *_: guest_policy)
    monkeypatch.setattr(
        management_ui, "validate_function_groups", lambda *_: deepcopy(groups)
    )
    monkeypatch.setattr(
        management_ui,
        "get_exposed_entities",
        lambda _hass: [
            {"entity_id": "sensor.allowed"},
            {"entity_id": "sensor.private"},
        ],
    )
    monkeypatch.setattr(
        management_ui.ConversationContinuity,
        "identity_key",
        lambda *_args: (None, "none"),
    )

    guest = await management_ui._async_preview_effective_request(
        hass, entry, subentry, options, "user"
    )
    assert any("temporary memories are excluded" in note for note in guest["notes"])
    assert discover.await_count == 1

    monkeypatch.setattr(
        management_ui, "configured_function_tools_from_data", lambda _data: []
    )
    monkeypatch.setattr(management_ui, "validate_function_groups", lambda *_: [])
    empty = await management_ui._async_preview_effective_request(
        hass, entry, subentry, options, "user"
    )
    request_settings = next(
        section for section in empty["sections"] if section["key"] == "request_settings"
    )
    assert "tool_choice" not in request_settings["content"]


@pytest.mark.asyncio
async def test_request_rule_test_defaults_duplicate_move_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry, subentry = _entry_pair()
    hass = _hass(entry)
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_: (entry, subentry))
    rules = SimpleNamespace(
        async_set_defaults=AsyncMock(return_value={"case_sensitive": True}),
        async_duplicate=AsyncMock(return_value={"id": "copy"}),
        async_move=AsyncMock(return_value={"id": "rule", "priority": 2}),
        revision=lambda: "revision-2",
    )
    monkeypatch.setattr(
        management_ui, "async_get_request_rules", AsyncMock(return_value=rules)
    )
    registry = SimpleNamespace(entities={})
    monkeypatch.setattr(management_ui.er, "async_get", lambda _hass: registry)

    with pytest.raises(HomeAssistantError, match="Test request text is required"):
        await management_ui.async_management_command(
            hass, "admin", True, _message("request_rules", "test", text=" ")
        )
    with pytest.raises(HomeAssistantError, match="entity is not available"):
        await management_ui.async_management_command(
            hass, "admin", True, _message("request_rules", "test", text="hello")
        )

    registry.entities["conversation.jarvis"] = SimpleNamespace(
        config_entry_id="entry-1",
        config_subentry_id="agent-1",
        domain="conversation",
        entity_id="conversation.jarvis",
    )
    hass.services.async_call.return_value = {"response": "ok"}
    assert (
        await management_ui.async_management_command(
            hass, "admin", True, _message("request_rules", "test", text=" hello ")
        )
    ) == {"response": "ok"}
    assert "defaults" in await management_ui.async_management_command(
        hass, "admin", True, _message("request_rules", "defaults", defaults={})
    )

    with pytest.raises(HomeAssistantError, match="rule_id is required"):
        await management_ui.async_management_command(
            hass, "admin", True, _message("request_rules", "duplicate")
        )
    duplicated = await management_ui.async_management_command(
        hass, "admin", True, _message("request_rules", "duplicate", rule_id="rule")
    )
    moved = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message("request_rules", "move", rule_id="rule", direction="up"),
    )
    assert duplicated["rule"]["id"] == "copy"
    assert moved["rule"]["priority"] == 2
    with pytest.raises(HomeAssistantError, match="direction is required"):
        await management_ui.async_management_command(
            hass, "admin", True, _message("request_rules", "move", rule_id="rule")
        )
    with pytest.raises(HomeAssistantError, match="Unknown Request Rules action"):
        await management_ui.async_management_command(
            hass, "admin", True, _message("request_rules", "other", rule_id="rule")
        )


@pytest.mark.asyncio
async def test_backup_preview_and_diagnostics_success_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry, subentry = _entry_pair()
    hass = _hass(entry)
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_: (entry, subentry))
    prepared = SimpleNamespace(title="Backup", summary=lambda: {"agents": 1})
    inspect = Mock(return_value=prepared)
    restore = AsyncMock(return_value={"restored": True})
    preview = AsyncMock(return_value={"preview": True})
    monkeypatch.setattr(management_ui, "inspect_backup", inspect)
    monkeypatch.setattr(management_ui, "async_restore_backup", restore)
    monkeypatch.setattr(management_ui, "_async_preview_effective_request", preview)
    monkeypatch.setattr(
        management_ui, "merge_agent_config", lambda data, updates: {**data, **updates}
    )
    monkeypatch.setattr(
        management_ui,
        "async_test_agent",
        AsyncMock(return_value=SimpleNamespace(as_dict=lambda: {"healthy": True})),
    )

    assert (
        await management_ui.async_management_command(
            hass, "admin", True, _message("backup", "inspect", document={})
        )
    )["title"] == "Backup"
    assert await management_ui.async_management_command(
        hass, "admin", True, _message("backup", "restore", document={}, confirm=True)
    ) == {"restored": True}
    assert await management_ui.async_management_command(
        hass, "admin", True, _message("configuration", "request_preview", config={})
    ) == {"preview": True}
    assert await management_ui.async_management_command(
        hass, "user", False, _message("diagnostics", "test_agent")
    ) == {"healthy": True}


@pytest.mark.asyncio
async def test_ha_tool_add_validation_refresh_and_group_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry, subentry = _entry_pair()
    hass = _hass(entry)
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_: (entry, subentry))

    with pytest.raises(HomeAssistantError, match="Select up to 1000"):
        await management_ui.async_management_command(
            hass, "admin", True, _message("tools", "ha_add", tools="bad")
        )
    monkeypatch.setattr(
        management_ui,
        "validate_reference",
        Mock(side_effect=ValueError("bad reference")),
    )
    with pytest.raises(HomeAssistantError, match="bad reference"):
        await management_ui.async_management_command(
            hass, "admin", True, _message("tools", "ha_add", tools=[{}])
        )

    reference = {"source_id": "assist", "tool_name": "Light"}
    monkeypatch.setattr(management_ui, "validate_reference", lambda _value: reference)
    monkeypatch.setattr(management_ui, "reference_key", lambda _value: "assist:Light")
    snapshot = SimpleNamespace(tools={"assist:Light": object()})
    monkeypatch.setattr(
        management_ui, "async_discover", AsyncMock(return_value=snapshot)
    )
    hass.config_entries.async_get_entry.return_value = None
    with pytest.raises(HomeAssistantError, match="Agent no longer exists"):
        await management_ui.async_management_command(
            hass, "admin", True, _message("tools", "ha_add", tools=[reference])
        )

    hass.config_entries.async_get_entry.return_value = entry
    monkeypatch.setattr(
        management_ui, "configured_function_tools_from_data", lambda _data: []
    )
    group = {"id": "group", "functions": []}
    monkeypatch.setattr(management_ui, "validate_function_groups", lambda *_: [group])
    with pytest.raises(HomeAssistantError, match="Function Group no longer exists"):
        await management_ui.async_management_command(
            hass,
            "admin",
            True,
            _message("tools", "ha_add", tools=[reference], group_id="missing"),
        )

    added = _tool("ha_light")
    monkeypatch.setattr(management_ui, "new_reference_tool", lambda *_args: added)
    persist = Mock(return_value={"status": "saved"})
    monkeypatch.setattr(management_ui, "_persist_function_configuration", persist)
    result = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message("tools", "ha_add", tools=[reference], group_id="group"),
    )
    assert result == {"status": "saved"}
    assert group["functions"] == ["ha_light"]


@pytest.mark.asyncio
async def test_function_tool_validation_and_save_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry, subentry = _entry_pair()
    hass = _hass(entry)
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_: (entry, subentry))
    tools = [_tool("one"), _tool("two")]
    monkeypatch.setattr(
        management_ui,
        "configured_function_tools_from_data",
        lambda _data: deepcopy(tools),
    )
    monkeypatch.setattr(management_ui, "validate_function_groups", lambda *_: [])
    monkeypatch.setattr(
        management_ui,
        "validate_function_tools",
        lambda value: deepcopy(value) if isinstance(value, list) else [],
    )
    persist = Mock(return_value={"status": "saved", "revision": "next"})
    monkeypatch.setattr(management_ui, "_persist_function_configuration", persist)

    assert await management_ui.async_management_command(
        hass, "admin", True, _message("tools", "validate", tools=[])
    ) == {"valid": True, "errors": {}, "config": []}
    assert await management_ui.async_management_command(
        hass, "admin", True, _message("tools", "validate_current")
    ) == {"valid": True, "errors": {}}

    cases = [
        (_message("tools", "save"), "tool must be an object"),
        (
            _message("tools", "save", tool=_tool("one"), original_name=3),
            "original_name",
        ),
        (
            _message("tools", "save", tool=_tool("new"), original_name="missing"),
            "no longer exists",
        ),
        (
            _message("tools", "save", tool=_tool("two"), original_name="one"),
            "already exists",
        ),
    ]
    for message, error in cases:
        with pytest.raises(HomeAssistantError, match=error):
            await management_ui.async_management_command(hass, "admin", True, message)

    assert await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message("tools", "save", tool=_tool("one"), original_name="one"),
    ) == {"status": "saved", "revision": "next"}


@pytest.mark.asyncio
async def test_function_enable_delete_and_group_validation_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry, subentry = _entry_pair()
    hass = _hass(entry)
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_: (entry, subentry))
    tools = [_tool("one")]
    groups = [{"id": "group", "functions": ["one"]}]
    monkeypatch.setattr(
        management_ui,
        "configured_function_tools_from_data",
        lambda _data: deepcopy(tools),
    )
    monkeypatch.setattr(
        management_ui, "validate_function_groups", lambda *_: deepcopy(groups)
    )
    monkeypatch.setattr(
        management_ui,
        "_persist_function_configuration",
        Mock(side_effect=lambda *_args, **_kwargs: {"status": "saved"}),
    )
    monkeypatch.setattr(
        management_ui,
        "_function_reference_state",
        AsyncMock(return_value=(object(), {"request_rules": [], "guest_mode": False})),
    )

    cases = [
        (
            _message("tools", "set_enabled", name="one", enabled="no"),
            "name and enabled",
        ),
        (
            _message("tools", "set_enabled", name="missing", enabled=True),
            "no longer exists",
        ),
        (_message("tools", "delete", name="one"), "Explicit confirmation"),
        (_message("tools", "delete", confirm=True), "name is required"),
        (
            _message("tools", "delete", name="missing", confirm=True),
            "no longer exists",
        ),
        (_message("tools", "save_group"), "group must be an object"),
        (
            _message("tools", "save_group", group={"id": "new", "functions": [3]}),
            "group functions",
        ),
        (
            _message(
                "tools",
                "save_group",
                group={"id": "new", "functions": []},
                original_id=3,
            ),
            "original_id",
        ),
        (
            _message(
                "tools",
                "save_group",
                group={"id": "new", "functions": []},
                original_id="missing",
            ),
            "Group no longer exists",
        ),
        (_message("tools", "delete_group", group_id="group"), "Explicit confirmation"),
        (_message("tools", "delete_group", confirm=True), "group_id is required"),
        (
            _message("tools", "delete_group", group_id="missing", confirm=True),
            "Group no longer exists",
        ),
    ]
    for message, error in cases:
        with pytest.raises(HomeAssistantError, match=error):
            await management_ui.async_management_command(hass, "admin", True, message)

    disabled = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message("tools", "set_enabled", name="one", enabled=False),
    )
    assert disabled["references"] == {"request_rules": [], "guest_mode": False}
    assert await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message("tools", "delete_group", group_id="group", confirm=True),
    ) == {"status": "saved"}
    enabled = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message("tools", "set_enabled", name="one", enabled=True),
    )
    assert "references" not in enabled


@pytest.mark.asyncio
async def test_function_rename_propagates_failure_after_successful_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry, subentry = _entry_pair()
    hass = _hass(entry)
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_: (entry, subentry))
    tools = [_tool("old")]
    monkeypatch.setattr(
        management_ui,
        "configured_function_tools_from_data",
        lambda _data: deepcopy(tools),
    )
    monkeypatch.setattr(management_ui, "validate_function_groups", lambda *_: [])
    monkeypatch.setattr(
        management_ui, "validate_function_tools", lambda value: deepcopy(value)
    )
    monkeypatch.setattr(
        management_ui, "_agent_config_revision", lambda *_args: "revision"
    )
    rules = SimpleNamespace(
        revision=lambda: "rules-revision",
        async_rename_function_reference=AsyncMock(
            side_effect=RuntimeError("rename failed")
        ),
    )
    monkeypatch.setattr(
        management_ui,
        "_function_reference_state",
        AsyncMock(return_value=(rules, {"request_rules": [], "guest_mode": False})),
    )
    persist = Mock(
        side_effect=[
            {"status": "saved", "revision": "new-revision"},
            {"status": "rolled-back"},
        ]
    )
    monkeypatch.setattr(management_ui, "_persist_function_configuration", persist)

    with pytest.raises(RuntimeError, match="rename failed"):
        await management_ui.async_management_command(
            hass,
            "admin",
            True,
            _message("tools", "save", tool=_tool("new"), original_name="old"),
        )
    assert persist.call_count == 2


@pytest.mark.asyncio
async def test_conversation_cleanup_and_admin_temporary_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry, subentry = _entry_pair()
    hass = _hass(entry)
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_: (entry, subentry))
    continuity = SimpleNamespace(async_end=AsyncMock(return_value=True))
    runtime = SimpleNamespace(end=Mock())
    reset = Mock()
    monkeypatch.setattr(management_ui, "async_get_continuity", lambda *_: continuity)
    monkeypatch.setattr(management_ui, "get_function_group_runtime", lambda *_: runtime)
    monkeypatch.setattr(management_ui, "_reset_request_rule_runtime", reset)

    assert await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message("conversations", "end_active", continuity_key="key"),
    ) == {"ended": 1}
    runtime.end.assert_called_once_with("continuity:key")
    reset.assert_called_once_with(hass, "entry-1", "agent-1", "key")

    temporary = SimpleNamespace(async_delete=AsyncMock(return_value=1))
    monkeypatch.setattr(
        management_ui, "async_get_temporary_memory", AsyncMock(return_value=temporary)
    )
    assert await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message(
            "memories",
            "temporary_delete",
            memory_id="memory",
            temporary_scope_id="user:someone-else",
        ),
    ) == {"deleted": 1}


@pytest.mark.asyncio
async def test_fallthrough_branches_and_scope_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry, subentry = _entry_pair()
    hass = _hass(entry)
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_: (entry, subentry))
    monkeypatch.setattr(
        management_ui,
        "async_get_guest_mode",
        AsyncMock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        management_ui, "async_get_usage", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        management_ui,
        "configured_function_tools_from_data",
        lambda _data: [],
    )
    monkeypatch.setattr(management_ui, "validate_function_groups", lambda *_: [])
    monkeypatch.setattr(
        management_ui,
        "async_get_continuity",
        lambda *_: SimpleNamespace(),
    )
    monkeypatch.setattr(
        management_ui,
        "async_get_archive",
        AsyncMock(return_value=SimpleNamespace(scope_counts=lambda: {})),
    )
    monkeypatch.setattr(
        management_ui,
        "async_get_memory",
        AsyncMock(return_value=SimpleNamespace(scope_counts=lambda: {})),
    )
    monkeypatch.setattr(
        management_ui,
        "async_get_temporary_memory",
        AsyncMock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        management_ui,
        "async_get_knowledge",
        AsyncMock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        management_ui,
        "_scope_catalog",
        AsyncMock(return_value=[{"scope_id": "user:user"}]),
    )

    assert await management_ui.async_management_command(
        hass, "admin", True, _message("scopes", "catalog")
    ) == {"scopes": [{"scope_id": "user:user"}]}

    with pytest.raises(HomeAssistantError, match="Unknown memories management action"):
        await management_ui.async_management_command(
            hass, "admin", True, _message("memories", "temporary_unknown")
        )

    for section in (
        "guest_mode",
        "backup",
        "configuration",
        "tools",
        "usage",
        "conversations",
        "memories",
        "knowledge",
    ):
        with pytest.raises(
            HomeAssistantError, match=f"Unknown {section} management action"
        ):
            await management_ui.async_management_command(
                hass, "admin", True, _message(section, "unknown")
            )


def test_reference_error_handles_each_reference_kind_independently() -> None:
    rules_only = management_ui._function_reference_error(
        "demo", {"request_rules": [{"name": "Rule"}], "guest_mode": False}
    )
    guest_only = management_ui._function_reference_error(
        "demo", {"request_rules": [], "guest_mode": True}
    )
    assert "Request Rules: Rule" in rules_only
    assert "Guest Mode" not in rules_only
    assert "Guest Mode custom function access" in guest_only


@pytest.mark.asyncio
async def test_conversation_cleanup_without_function_group_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry, subentry = _entry_pair()
    hass = _hass(entry)
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_: (entry, subentry))
    monkeypatch.setattr(
        management_ui,
        "async_get_continuity",
        lambda *_: SimpleNamespace(async_end=AsyncMock(return_value=True)),
    )
    monkeypatch.setattr(management_ui, "get_function_group_runtime", lambda *_: None)
    reset = Mock()
    monkeypatch.setattr(management_ui, "_reset_request_rule_runtime", reset)

    assert await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message("conversations", "end_active", continuity_key="key"),
    ) == {"ended": 1}
    reset.assert_called_once()
