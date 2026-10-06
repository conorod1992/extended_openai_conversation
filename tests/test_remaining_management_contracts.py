"""Remaining Management command, ownership, and response boundary contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_loading_performance as loading,
    management_ui as ui,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_management_ui import _entry_pair


def _request(hass, section, action, **values):
    entry, subentry = _entry_pair()
    return ui._ManagementRequest(
        hass,
        "admin",
        True,
        {"section": section, "action": action, **values},
        entry.entry_id,
        subentry.subentry_id,
        entry,
        subentry,
    )


@pytest.mark.parametrize(
    "handler,section",
    [
        (ui.async_scopes_command, "scopes"),
        (ui.async_service_catalog_command, "services"),
        (ui.async_diagnostics_command, "diagnostics"),
    ],
)
async def test_management_unknown_commands_return_explicit_error(
    hass, handler, section
):
    with pytest.raises(
        HomeAssistantError, match=f"Unknown {section} management action"
    ):
        await handler(_request(hass, section, "unsupported"))
    hass.services.async_call.assert_not_awaited()


async def test_scope_catalog_forwards_explicit_kind_and_identity(hass, monkeypatch):
    catalog = AsyncMock(return_value={"scopes": ["user:admin"]})
    monkeypatch.setattr(loading, "async_scope_catalog", catalog)
    result = await ui.async_scopes_command(
        _request(hass, "scopes", "catalog", scope_kind="memories")
    )
    assert result == {"scopes": ["user:admin"]}
    catalog.assert_awaited_once_with(
        hass, "admin", True, "entry-1", "agent-1", scope_kind="memories"
    )


@pytest.mark.parametrize(
    "action,helper,values,kwargs",
    [
        (
            "settings",
            "async_set_settings",
            {"defaults": {"fuzzy": False}, "wording_groups": [], "revision": "r"},
            {"expected_revision": "r"},
        ),
        (
            "groups",
            "async_set_groups",
            {"groups": [{"id": "lights", "name": "Lights"}], "revision": "r"},
            {"expected_revision": "r"},
        ),
    ],
)
async def test_request_rule_management_settings_forward_revision(
    hass, monkeypatch, action, helper, values, kwargs
):
    update = AsyncMock(return_value={"revision": "next"})
    monkeypatch.setattr(
        ui,
        "async_get_request_rules",
        AsyncMock(return_value=SimpleNamespace(**{helper: update})),
    )
    assert await ui.async_request_rules_command(
        _request(hass, "request_rules", action, **values)
    ) == {"revision": "next"}
    args = (
        (values["defaults"], values["wording_groups"])
        if action == "settings"
        else (values["groups"],)
    )
    update.assert_awaited_once_with(*args, **kwargs)


async def test_management_rule_pack_export_forwards_selection(hass, monkeypatch):
    rules = object()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=rules))
    export = Mock(return_value={"rules": ["selected"]})
    monkeypatch.setattr(ui, "export_rule_pack", export)
    result = await ui.async_request_rules_command(
        _request(
            hass,
            "request_rules",
            "rule_pack_export",
            selection="group",
            group_id="lights",
            rule_ids=["one"],
        )
    )
    assert result == {"rules": ["selected"]}
    export.assert_called_once_with(rules, "group", "lights", ["one"])


async def test_live_rule_test_rejects_unavailable_agent(hass, monkeypatch):
    monkeypatch.setattr(ui, "_conversation_entity_id", lambda *_: None)
    with pytest.raises(HomeAssistantError, match="agent is unavailable"):
        await ui.async_request_rules_command(
            _request(hass, "request_rules", "test", text="run", confirm=True)
        )
    hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("action", ["primary", "detail"])
async def test_overview_loads_only_requested_projection(hass, monkeypatch, action):
    primary, detail = (
        AsyncMock(return_value={"primary": True}),
        AsyncMock(return_value={"detail": True}),
    )
    monkeypatch.setattr(loading, "async_overview_primary", primary)
    monkeypatch.setattr(loading, "async_overview_detail", detail)
    request = _request(hass, "overview", action, kind="usage")
    result = await ui.async_overview_command(request)
    selected = primary if action == "primary" else detail
    other = detail if action == "primary" else primary
    assert result == {action: True}
    selected.assert_awaited_once_with(
        hass,
        request.entry,
        request.subentry,
        is_admin=True,
        **({"kind": "usage"} if action == "detail" else {}),
    )
    other.assert_not_awaited()


@pytest.mark.parametrize("kind", [None, 7, {}])
async def test_overview_detail_requires_text_kind(hass, monkeypatch, kind):
    detail = AsyncMock()
    monkeypatch.setattr(loading, "async_overview_detail", detail)
    with pytest.raises(HomeAssistantError, match="kind is required"):
        await ui.async_overview_command(_request(hass, "overview", "detail", kind=kind))
    detail.assert_not_awaited()


@pytest.mark.parametrize(
    "values,message",
    [
        ({"target_scope_id": "user:"}, "Personal or Shared owner"),
        ({"content": None, "expires_at": "2099-01-01"}, "Memory, category, and expiry"),
        (
            {"content": "remember", "category": 4, "expires_at": "2099-01-01"},
            "Memory, category, and expiry",
        ),
        ({"content": "remember", "expires_at": None}, "Memory, category, and expiry"),
    ],
)
async def test_temporary_memory_add_rejects_invalid_owner_or_values(
    hass, monkeypatch, values, message
):
    manager = SimpleNamespace(async_add_owned=AsyncMock())
    monkeypatch.setattr(
        ui, "async_get_temporary_memory", AsyncMock(return_value=manager)
    )
    with pytest.raises(HomeAssistantError, match=message):
        await ui.async_memories_command(
            _request(hass, "memories", "temporary_add", **values)
        )
    manager.async_add_owned.assert_not_awaited()


async def test_temporary_memory_validation_error_is_user_visible(hass, monkeypatch):
    manager = SimpleNamespace(
        async_add_owned=AsyncMock(
            side_effect=ValueError("expiry must be in the future")
        )
    )
    monkeypatch.setattr(
        ui, "async_get_temporary_memory", AsyncMock(return_value=manager)
    )
    with pytest.raises(
        HomeAssistantError, match="expiry must be in the future"
    ) as caught:
        await ui.async_memories_command(
            _request(
                hass,
                "memories",
                "temporary_add",
                content="remember",
                category="general",
                expires_at="2020-01-01",
            )
        )
    assert isinstance(caught.value.__cause__, ValueError)


async def test_memory_move_between_personal_users_is_rejected(hass, monkeypatch):
    memory = SimpleNamespace(async_update=AsyncMock())
    monkeypatch.setattr(ui, "async_get_memory", AsyncMock(return_value=memory))
    with pytest.raises(HomeAssistantError, match="Personal and Shared scopes"):
        await ui.async_memories_command(
            _request(
                hass,
                "memories",
                "update",
                scope_id="user:first",
                target_scope_id="user:second",
                memory_id="one",
            )
        )
    memory.async_update.assert_not_awaited()


@pytest.mark.parametrize(
    "section,action",
    [("configuration", "get"), ("configuration", "save"), ("tools", "delete")],
)
async def test_websocket_trace_adds_timing_before_sending(
    hass, monkeypatch, section, action
):
    result = {"_performance": {"handler_ms": 2}}
    monkeypatch.setattr(ui, "async_management_command", AsyncMock(return_value=result))
    logger = Mock()
    logger.isEnabledFor.return_value = True
    monkeypatch.setattr(ui, "_PERFORMANCE_LOGGER", logger)
    sent = []
    connection = SimpleNamespace(
        user=SimpleNamespace(id="admin", is_admin=True),
        send_error=Mock(),
        send_result=lambda ident, value: sent.append(
            (ident, dict(value["_performance"]))
        ),
    )
    await ui.websocket_management.__wrapped__(
        hass, connection, {"id": 8, "section": section, "action": action}
    )
    assert sent[0][0] == 8
    assert sent[0][1]["websocket_pre_send_ms"] >= 0
    logger.debug.assert_called_once()
    connection.send_error.assert_not_called()


@pytest.mark.parametrize("memory_ids", [None, "one", [7]])
async def test_legacy_memory_reassignment_requires_string_ids(
    hass, monkeypatch, memory_ids
):
    memory = SimpleNamespace(async_reassign=AsyncMock())
    monkeypatch.setattr(ui, "async_get_memory", AsyncMock(return_value=memory))
    with pytest.raises(
        HomeAssistantError, match="memory_ids must be a list of strings"
    ):
        await ui.async_memories_command(
            _request(
                hass,
                "memories",
                "reassign_legacy",
                target_scope_id="user:admin",
                memory_ids=memory_ids,
            )
        )
    memory.async_reassign.assert_not_awaited()


async def test_knowledge_count_projection_falls_back_when_statistics_are_malformed(
    hass, monkeypatch
):
    library = SimpleNamespace(
        async_list=AsyncMock(return_value=[{"id": "one"}]),
        stats=lambda: {"source_count": "unavailable"},
    )
    monkeypatch.setattr(ui, "async_get_knowledge", AsyncMock(return_value=library))
    result = await ui.async_knowledge_command(_request(hass, "knowledge", "list"))
    assert result["sources"] == [{"id": "one"}]
    assert result["stats"] == {"source_count": "unavailable"}
    assert result["feature_status"]


async def test_function_save_requires_unambiguous_yaml_or_object(hass):
    with pytest.raises(HomeAssistantError, match="either yaml or tool, not both"):
        await ui.async_tools_command(
            _request(hass, "tools", "save", yaml="[]", tool={})
        )
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_configuration_live_metadata_loads_attribute_catalog_only_when_requested(
    hass, monkeypatch
):
    catalog = Mock(return_value={"entities": ["light.kitchen"]})
    monkeypatch.setattr(ui, "exposed_attribute_catalog", catalog)
    request = _request(
        hass,
        "configuration",
        "live_metadata",
        metadata_keys=["exposed_attribute_catalog"],
    )
    result = await ui.async_configuration_command(request)
    assert result == {"exposed_attribute_catalog": {"entities": ["light.kitchen"]}}
    catalog.assert_called_once_with(hass, request.subentry.data)


async def test_configuration_cold_read_does_not_disguise_non_tool_validation_error(
    hass,
):
    request = _request(hass, "configuration", "get")
    request.subentry.data["max_tokens"] = -1
    request.subentry.data[ui.CONF_FUNCTION_TOOLS] = []
    with pytest.raises(AgentConfigError):
        await ui.async_configuration_command(request)


async def test_legacy_guest_editor_loads_exposures_for_admin_migration(
    hass, monkeypatch
):
    from custom_components.extended_openai_conversation_responses import guest_mode

    registry = SimpleNamespace(async_get=lambda *_: None)
    monkeypatch.setattr(guest_mode.er, "async_get", lambda *_: registry)
    monkeypatch.setattr(guest_mode.dr, "async_get", lambda *_: registry)
    request = _request(hass, "guest_mode", "get")
    request.subentry.data.pop(ui.CONF_GUEST_POLICY_VERSION, None)
    request.subentry.data[ui.CONF_FUNCTION_TOOLS] = []
    monkeypatch.setattr(
        ui,
        "async_get_guest_mode",
        AsyncMock(return_value=SimpleNamespace(status=lambda: {"active": False})),
    )
    exposures = Mock(return_value=[])
    monkeypatch.setattr(ui, "get_exposed_entities", exposures)
    result = await ui.async_guest_mode_command(request)
    assert result["status"] == {"active": False}
    assert result["config"]
    exposures.assert_called_once_with(hass)


@pytest.mark.parametrize(
    "keys,message",
    [
        (None, "must be a list of strings"),
        ([1], "must be a list of strings"),
        (["unknown_metadata"], "Unknown configuration metadata"),
    ],
)
async def test_live_metadata_rejects_malformed_or_unknown_selection(
    hass, monkeypatch, keys, message
):
    catalog = Mock()
    monkeypatch.setattr(ui, "exposed_attribute_catalog", catalog)
    with pytest.raises(HomeAssistantError, match=message):
        await ui.async_configuration_command(
            _request(hass, "configuration", "live_metadata", metadata_keys=keys)
        )
    catalog.assert_not_called()


async def test_configuration_save_rejects_model_incompatible_api_before_persistence(
    hass,
):
    request = _request(
        hass,
        "configuration",
        "save",
        config={"chat_model": "gpt-5-pro", "api_mode": "chat_completions"},
    )
    request.subentry.data[ui.CONF_FUNCTION_TOOLS] = []
    request.message["revision"] = ui.persisted_config_projection(
        request.subentry
    ).revision
    result = await ui.async_configuration_command(request)
    assert result["valid"] is False
    assert result["errors"]
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_configuration_update_refreshes_local_handling_metadata(
    hass, monkeypatch
):
    request = _request(
        hass,
        "configuration",
        "update",
        config={ui.CONF_LOCAL_INTENT_EXCLUSIONS: ["HassTurnOn"]},
    )
    request.subentry.data[ui.CONF_FUNCTION_TOOLS] = []
    request.message["revision"] = ui.persisted_config_projection(
        request.subentry
    ).revision
    monkeypatch.setattr(
        ui, "configuration_supports_live_update", lambda *_args, **_kwargs: True
    )

    def update(_hass, _entry, subentry, *, data, title):
        subentry.data = data
        subentry.title = title

    monkeypatch.setattr(ui, "update_live_subentry", update)
    handling = Mock(return_value={"exclusions": ["HassTurnOn"]})
    monkeypatch.setattr(ui, "local_handling_snapshot", handling)
    result = await ui.async_configuration_command(request)
    assert result["local_handling"] == {"exclusions": ["HassTurnOn"]}
    handling.assert_called_once_with(
        hass, request.entry_id, request.subentry_id, ["HassTurnOn"]
    )
    assert request.subentry.data[ui.CONF_LOCAL_INTENT_EXCLUSIONS] == ["HassTurnOn"]


async def test_settings_live_update_persists_validated_settings(hass, monkeypatch):
    request = _request(hass, "settings", "update", settings={"archive_enabled": True})
    request.subentry.data[ui.CONF_FUNCTION_TOOLS] = []
    request.message["revision"] = ui.persisted_config_projection(
        request.subentry
    ).revision
    monkeypatch.setattr(
        ui, "configuration_supports_live_update", lambda *_args, **_kwargs: True
    )
    updated = Mock()
    monkeypatch.setattr(ui, "update_live_subentry", updated)
    result = await ui.async_settings_command(request)
    assert result["settings"]["archive_enabled"] is True
    assert updated.call_args.kwargs["data"]["archive_enabled"] is True
    hass.config_entries.async_update_subentry.assert_not_called()
