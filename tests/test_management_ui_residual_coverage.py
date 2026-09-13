"""Residual branch coverage for the management API boundary."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    AGENT_CONFIG_EXPORT_VERSION,
    AgentConfigError,
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.knowledge import (
    KnowledgeSource,
)
from custom_components.extended_openai_conversation_responses.memory import MemoryRecord
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemoryRecord,
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
        http=SimpleNamespace(async_register_static_paths=AsyncMock()),
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


def test_entry_resolution_and_scope_validation_boundaries() -> None:
    entry, subentry = _entry_pair()
    hass = _hass(None)
    with pytest.raises(HomeAssistantError, match="Integration entry not found"):
        management_ui.entry_and_agent(hass, "missing", "agent-1")

    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        domain="other", subentries={}
    )
    with pytest.raises(HomeAssistantError, match="Integration entry not found"):
        management_ui.entry_and_agent(hass, "entry-1", "agent-1")

    hass.config_entries.async_get_entry.return_value = entry
    assert management_ui.entry_and_agent(hass, "entry-1", "agent-1") == (
        entry,
        subentry,
    )
    subentry.subentry_type = "not-conversation"
    with pytest.raises(HomeAssistantError, match="Conversation agent not found"):
        management_ui.entry_and_agent(hass, "entry-1", "agent-1")

    with pytest.raises(HomeAssistantError, match="scope_id must be a string"):
        management_ui._selected_scope("user", True, 7)
    with pytest.raises(HomeAssistantError, match="Unknown data scope"):
        management_ui._selected_scope("user", True, "organization:example")
    assert management_ui._memory_scope("user:alice") == "alice"
    assert management_ui._memory_scope("shared:household") == "shared:household"


def test_revision_and_reference_helpers_cover_validation_edges() -> None:
    _entry, subentry = _entry_pair()
    management_ui._require_agent_config_revision(subentry, None)
    with pytest.raises(HomeAssistantError, match="revision must be a string"):
        management_ui._require_agent_config_revision(subentry, 1)

    assert management_ui._function_reference_error(
        "weather", {"request_rules": [{"id": "rule-1"}], "guest_mode": True}
    ) == (
        "Function Tool `weather` is still referenced by Request Rules: rule-1; "
        "Guest Mode custom function access. Update those references before deleting it."
    )

    valid = management_ui._validation_result(lambda: {"ok": True})
    assert valid == {"valid": True, "errors": {}, "config": {"ok": True}}

    def invalid():
        raise AgentConfigError("field", "bad value")

    assert management_ui._validation_result(invalid) == {
        "valid": False,
        "errors": {"field": "bad value"},
    }


@pytest.mark.parametrize(
    ("document", "error"),
    [
        ("config: [", "invalid JSON/YAML"),
        ([], "must be an object"),
        ({"schema": "wrong", "version": 1, "config": {}}, "export schema"),
        (
            {
                "schema": "extended_openai_conversation.agent",
                "version": AGENT_CONFIG_EXPORT_VERSION + 1,
                "config": {},
            },
            "export version",
        ),
        (
            {
                "schema": "extended_openai_conversation.agent",
                "version": AGENT_CONFIG_EXPORT_VERSION,
                "config": {},
                "unexpected": True,
            },
            "unknown fields",
        ),
        (
            {
                "schema": "extended_openai_conversation.agent",
                "version": AGENT_CONFIG_EXPORT_VERSION,
                "config": [],
            },
            "must be an object",
        ),
    ],
)
def test_import_document_rejects_malformed_boundaries(document, error) -> None:
    with pytest.raises(AgentConfigError, match=error):
        management_ui._parse_import_document(document)


def test_request_rule_function_reference_validation_edges() -> None:
    with pytest.raises(HomeAssistantError, match="rule must be an object"):
        management_ui._prepare_request_rule([])

    action = f"{DOMAIN}.call_function"
    with pytest.raises(HomeAssistantError, match="action is invalid"):
        management_ui._validate_request_rule_functions(
            {"action": {"actions": ["ignored", {"action": action, "data": {}}]}}, []
        )
    with pytest.raises(HomeAssistantError, match="arguments must be an object"):
        management_ui._validate_request_rule_functions(
            {
                "action": {
                    "actions": [
                        {
                            "action": action,
                            "data": {"function": "demo", "arguments": []},
                        }
                    ]
                }
            },
            [],
        )

    tool = {
        "spec": {
            "name": "demo",
            "parameters": {"type": "object", "required": ["city"]},
        },
        "function": {"type": "native", "name": "execute_service"},
    }
    rule = {
        "action": {
            "actions": [
                {"action": action, "data": {"function": "missing", "arguments": {}}}
            ]
        }
    }
    with pytest.raises(HomeAssistantError, match="unavailable or disabled: missing"):
        management_ui._validate_request_rule_functions(rule, [tool])

    rule["action"]["actions"][0]["data"]["function"] = "demo"
    with pytest.raises(HomeAssistantError, match="needs input: city"):
        management_ui._validate_request_rule_functions(rule, [tool])


async def test_non_admin_scope_catalog_handles_known_and_missing_users() -> None:
    hass = _hass()
    hass.auth.async_get_user = AsyncMock(
        side_effect=[SimpleNamespace(name="Alice"), None]
    )
    first = await management_ui._scope_catalog(hass, "alice", False, {"alice": 2})
    second = await management_ui._scope_catalog(hass, "missing", False)
    assert first[0]["display_name"] == "Alice"
    assert first[0]["memory_count"] == 2
    assert second[0]["display_name"] == "missing"


async def test_agents_overview_skips_non_conversation_subentries(
    monkeypatch,
) -> None:
    entry, _subentry = _entry_pair()
    skipped = SimpleNamespace(subentry_type="sensor")
    entry.subentries["skip"] = skipped
    hass = _hass(entry)
    hass.config_entries.async_entries.return_value = [entry]
    hass.auth.async_get_users = AsyncMock(return_value=[])

    usage = SimpleNamespace(today_summary=lambda: {"total_tokens": 123})
    memory = SimpleNamespace(stats=lambda: {"memory_count": 4})
    knowledge = SimpleNamespace(source_count=2)
    guest = SimpleNamespace(status=lambda: {"state": "inactive"})
    monkeypatch.setattr(management_ui, "async_get_usage", AsyncMock(return_value=usage))
    monkeypatch.setattr(
        management_ui, "async_get_memory", AsyncMock(return_value=memory)
    )
    monkeypatch.setattr(
        management_ui, "async_get_knowledge", AsyncMock(return_value=knowledge)
    )
    monkeypatch.setattr(
        management_ui, "async_get_guest_mode", AsyncMock(return_value=guest)
    )

    result = await management_ui.async_management_command(
        hass, "admin", True, {"action": "agents"}
    )
    assert len(result["agents"]) == 1
    assert result["agents"][0]["tokens_today"] == 123
    assert result["agents"][0]["memory_count"] == 4
    assert result["is_admin"] is True


async def test_command_requires_agent_identifiers() -> None:
    with pytest.raises(
        HomeAssistantError, match="entry_id and subentry_id are required"
    ):
        await management_ui.async_management_command(
            _hass(), "user", False, {"action": "get"}
        )


async def test_guest_backup_and_service_dispatch_edges(monkeypatch) -> None:
    entry, subentry = _entry_pair()
    hass = _hass(entry)
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_: (entry, subentry))
    policy = SimpleNamespace(as_diagnostics=lambda: {"guest_active": False})
    guest = SimpleNamespace(
        status=lambda: {"state": "inactive"},
        async_update_trusted=AsyncMock(return_value={"state": "active"}),
        async_disable_trusted=AsyncMock(return_value={"state": "inactive"}),
    )
    monkeypatch.setattr(
        management_ui, "async_get_guest_mode", AsyncMock(return_value=guest)
    )
    monkeypatch.setattr(management_ui, "resolve_guest_policy", lambda *_: policy)

    result = await management_ui.async_management_command(
        hass, "user", False, _message("guest_mode", "get")
    )
    assert result == {
        "status": {"state": "inactive"},
        "policy": {"guest_active": False},
    }

    with pytest.raises(HomeAssistantError, match="config must be an object"):
        await management_ui.async_management_command(
            hass, "admin", True, _message("guest_mode", "save_policy", config=[])
        )
    with pytest.raises(HomeAssistantError, match="unknown fields"):
        await management_ui.async_management_command(
            hass,
            "admin",
            True,
            _message("guest_mode", "save_policy", config={"unknown": True}),
        )

    updated = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message("guest_mode", "update", active_from="08:00", indefinite=True),
    )
    disabled = await management_ui.async_management_command(
        hass, "admin", True, _message("guest_mode", "disable")
    )
    assert updated["status"]["state"] == "active"
    assert disabled["status"]["state"] == "inactive"

    monkeypatch.setattr(
        management_ui, "async_create_backup", AsyncMock(return_value={"backup": True})
    )
    assert await management_ui.async_management_command(
        hass, "admin", True, _message("backup", "create")
    ) == {"backup": True}
    with pytest.raises(HomeAssistantError, match="Explicit confirmation"):
        await management_ui.async_management_command(
            hass, "admin", True, _message("backup", "restore")
        )

    service_descriptions = AsyncMock(return_value={"light": {}})
    monkeypatch.setattr(
        management_ui.service_helper,
        "async_get_all_descriptions",
        service_descriptions,
    )
    assert await management_ui.async_management_command(
        hass, "admin", True, _message("service_catalog", "get")
    ) == {"services": {"light": {}}}


@dataclass
class _LatestRun:
    run_id: str


async def test_usage_dispatch_covers_all_read_routes(monkeypatch) -> None:
    entry, subentry = _entry_pair()
    hass = _hass(entry)
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_: (entry, subentry))
    usage = SimpleNamespace(
        latest_run=_LatestRun("run-1"),
        request_retention_days=7,
        run_retention_days=30,
        as_dict=lambda: {"requests": 3},
        today_summary=lambda: {"requests": 1},
        month_summary=lambda: {"requests": 2},
        daily_series=lambda start, end: [{"start": start, "end": end}],
        recent_runs=lambda **kwargs: {"runs": [kwargs]},
        requests_for_run=lambda run_id, **kwargs: {"run_id": run_id, **kwargs},
        breakdowns=lambda start, end: {"range": [start, end]},
        async_clear_details=AsyncMock(return_value={"cleared": 2}),
    )
    monkeypatch.setattr(management_ui, "async_get_usage", AsyncMock(return_value=usage))

    summary = await management_ui.async_management_command(
        hass, "user", False, _message("usage", "summary")
    )
    assert summary["latest"] == {"run_id": "run-1"}
    assert (
        await management_ui.async_management_command(
            hass,
            "user",
            False,
            _message("usage", "daily", start_date="2026-01-01", end_date="2026-01-02"),
        )
    )["days"][0]["start"] == "2026-01-01"
    assert (
        await management_ui.async_management_command(
            hass, "user", False, _message("usage", "runs", limit=2, offset=1)
        )
    )["runs"][0]["limit"] == 2

    with pytest.raises(HomeAssistantError, match="run_id is required"):
        await management_ui.async_management_command(
            hass, "user", False, _message("usage", "requests")
        )
    requests = await management_ui.async_management_command(
        hass, "user", False, _message("usage", "requests", run_id="run-1")
    )
    assert requests["run_id"] == "run-1"
    assert (
        await management_ui.async_management_command(
            hass, "user", False, _message("usage", "breakdowns")
        )
    )["range"] == [None, None]
    assert await management_ui.async_management_command(
        hass, "user", False, _message("usage", "retention")
    ) == {"request_days": 7, "run_days": 30}
    with pytest.raises(HomeAssistantError, match="Administrator"):
        await management_ui.async_management_command(
            hass, "user", False, _message("usage", "clear_details", confirm=True)
        )
    assert await management_ui.async_management_command(
        hass, "admin", True, _message("usage", "clear_details", confirm=True)
    ) == {"cleared": 2}


async def test_conversation_memory_and_knowledge_dispatch(monkeypatch) -> None:
    entry, subentry = _entry_pair()
    hass = _hass(entry)
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_: (entry, subentry))
    continuity = SimpleNamespace(
        async_list=AsyncMock(return_value=[{"key": "active"}]),
        async_end=AsyncMock(return_value=False),
    )
    archive = SimpleNamespace(
        async_list_sessions=AsyncMock(return_value={"sessions": []}),
        async_search=AsyncMock(return_value={"matches": []}),
        async_get=AsyncMock(return_value={"session": "one"}),
        async_delete_session=AsyncMock(return_value={"deleted": 1}),
        async_clear_scope=AsyncMock(return_value={"deleted": 2}),
        async_delete_date_range=AsyncMock(return_value={"deleted": 3}),
    )
    monkeypatch.setattr(management_ui, "async_get_continuity", lambda *_: continuity)
    monkeypatch.setattr(
        management_ui, "async_get_archive", AsyncMock(return_value=archive)
    )

    with pytest.raises(HomeAssistantError, match="Administrator"):
        await management_ui.async_management_command(
            hass, "user", False, _message("conversations", "active")
        )
    assert (
        await management_ui.async_management_command(
            hass, "admin", True, _message("conversations", "active")
        )
    )["active"][0]["key"] == "active"
    with pytest.raises(HomeAssistantError, match="continuity_key is required"):
        await management_ui.async_management_command(
            hass, "admin", True, _message("conversations", "end_active")
        )
    assert await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message("conversations", "end_active", continuity_key="gone"),
    ) == {"ended": 0}

    for action, expected_key in (
        ("list", "sessions"),
        ("search", "matches"),
        ("get", "session"),
        ("delete", "deleted"),
        ("clear", "deleted"),
        ("delete_range", "deleted"),
    ):
        result = await management_ui.async_management_command(
            hass, "user", False, _message("conversations", action)
        )
        assert expected_key in result
    assert "archive_enabled" in await management_ui.async_management_command(
        hass, "user", False, _message("conversations", "settings")
    )

    temporary_record = TemporaryMemoryRecord(
        memory_id="temp-1",
        scope_id="user:user",
        content="Temporary",
        category="general",
        source="explicit",
        expires_at="2026-01-02",
        created_at="2026-01-01",
        updated_at="2026-01-01",
    )
    temporary = SimpleNamespace(
        async_list=AsyncMock(return_value=[temporary_record]),
        async_list_all=AsyncMock(return_value=[temporary_record]),
        async_delete=AsyncMock(return_value=1),
    )
    monkeypatch.setattr(
        management_ui, "async_get_temporary_memory", AsyncMock(return_value=temporary)
    )
    listed = await management_ui.async_management_command(
        hass, "user", False, _message("memories", "temporary_list")
    )
    assert listed["memories"][0]["memory_id"] == "temp-1"
    with pytest.raises(HomeAssistantError, match="temporary_scope_id is invalid"):
        await management_ui.async_management_command(
            hass,
            "admin",
            True,
            _message("memories", "temporary_delete", temporary_scope_id=2),
        )
    with pytest.raises(HomeAssistantError, match="not available"):
        await management_ui.async_management_command(
            hass,
            "user",
            False,
            _message("memories", "temporary_delete", temporary_scope_id="user:other"),
        )
    assert await management_ui.async_management_command(
        hass,
        "user",
        False,
        _message("memories", "temporary_delete", memory_id="temp-1"),
    ) == {"deleted": 1}

    record = MemoryRecord(
        memory_id="memory-1",
        user_id="user",
        content="Remember this",
        category="general",
        source="explicit",
        created_at="2026-01-01",
        updated_at="2026-01-01",
    )
    memory = SimpleNamespace(
        async_list=AsyncMock(return_value=[record]),
        async_add=AsyncMock(return_value={"status": "created"}),
        async_update=AsyncMock(return_value=record),
        async_delete=AsyncMock(return_value=1),
        async_clear=AsyncMock(return_value=2),
        async_reassign=AsyncMock(return_value={"reassigned": 1}),
    )
    monkeypatch.setattr(
        management_ui, "async_get_memory", AsyncMock(return_value=memory)
    )
    listed = await management_ui.async_management_command(
        hass, "user", False, _message("memories", "list")
    )
    assert listed["memories"][0]["content"] == "Remember this"
    assert await management_ui.async_management_command(
        hass, "user", False, _message("memories", "add", content="New")
    ) == {"status": "created"}
    assert (
        await management_ui.async_management_command(
            hass, "user", False, _message("memories", "update", memory_id="memory-1")
        )
    )["status"] == "updated"
    assert await management_ui.async_management_command(
        hass, "user", False, _message("memories", "delete", memory_id="memory-1")
    ) == {"deleted": 1}
    with pytest.raises(HomeAssistantError, match="Explicit confirmation"):
        await management_ui.async_management_command(
            hass, "user", False, _message("memories", "clear")
        )
    assert await management_ui.async_management_command(
        hass, "user", False, _message("memories", "clear", confirm=True)
    ) == {"deleted": 2}
    with pytest.raises(HomeAssistantError, match="Administrator"):
        await management_ui.async_management_command(
            hass,
            "user",
            False,
            _message("memories", "reassign_legacy", memory_ids=[]),
        )
    with pytest.raises(HomeAssistantError, match="list of strings"):
        await management_ui.async_management_command(
            hass,
            "admin",
            True,
            _message(
                "memories",
                "reassign_legacy",
                target_scope_id="user:user",
                memory_ids=[1],
            ),
        )
    assert await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message(
            "memories",
            "reassign_legacy",
            target_scope_id="user:user",
            memory_ids=["memory-1"],
        ),
    ) == {"reassigned": 1}

    source = KnowledgeSource(
        source_id="source-1",
        title="Notes",
        description="",
        content="Text",
        enabled=True,
        created_at="2026-01-01",
        updated_at="2026-01-01",
    )
    library = SimpleNamespace(
        async_list=AsyncMock(return_value=[{"source_id": "source-1"}]),
        stats=lambda: {"source_count": 1},
        async_get=AsyncMock(return_value=source),
        async_create=AsyncMock(return_value=source),
        async_update=AsyncMock(return_value=source),
        async_delete=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        management_ui, "async_get_knowledge", AsyncMock(return_value=library)
    )
    assert (
        await management_ui.async_management_command(
            hass, "user", False, _message("knowledge", "list")
        )
    )["stats"] == {"source_count": 1}
    for action in ("get", "create", "update"):
        assert "source" in await management_ui.async_management_command(
            hass, "user", False, _message("knowledge", action)
        )
    with pytest.raises(HomeAssistantError, match="Explicit confirmation"):
        await management_ui.async_management_command(
            hass, "user", False, _message("knowledge", "delete")
        )
    assert await management_ui.async_management_command(
        hass, "user", False, _message("knowledge", "delete", confirm=True)
    ) == {"deleted": 1}

    with pytest.raises(HomeAssistantError, match="settings must be an object"):
        await management_ui.async_management_command(
            hass, "admin", True, _message("settings", "update", settings=[])
        )
    settings = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        _message("settings", "update", settings={"archive_enabled": True}),
    )
    assert settings["settings"]["archive_enabled"] is True
    with pytest.raises(HomeAssistantError, match="Unknown unknown management action"):
        await management_ui.async_management_command(
            hass, "admin", True, _message("unknown", "unknown")
        )


async def test_websocket_and_setup_wiring(monkeypatch) -> None:
    connection = SimpleNamespace(
        user=SimpleNamespace(id="user", is_admin=False),
        send_error=MagicMock(),
        send_result=MagicMock(),
    )
    monkeypatch.setattr(
        management_ui,
        "async_management_command",
        AsyncMock(side_effect=HomeAssistantError("bad request")),
    )
    await management_ui.websocket_management(
        _hass(), connection, {"id": 7, "action": "x"}
    )
    connection.send_error.assert_called_once_with(7, "invalid_request", "bad request")

    management_ui.async_management_command.side_effect = None
    management_ui.async_management_command.return_value = {"ok": True}
    await management_ui.websocket_management(
        _hass(), connection, {"id": 8, "action": "x"}
    )
    connection.send_result.assert_called_once_with(8, {"ok": True})

    hass = _hass()
    register_command = MagicMock()
    register_panel = AsyncMock()
    monkeypatch.setattr(
        management_ui.websocket_api, "async_register_command", register_command
    )
    monkeypatch.setattr(
        management_ui.panel_custom, "async_register_panel", register_panel
    )
    await management_ui.async_setup_management_ui(hass)
    await management_ui.async_setup_management_ui(hass)
    hass.http.async_register_static_paths.assert_awaited_once()
    register_command.assert_called_once_with(hass, management_ui.websocket_management)
    register_panel.assert_awaited_once()
