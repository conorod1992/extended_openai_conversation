"""Group-only quarantine must agree across public editors and recovery."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    management_ui as ui,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
)
from custom_components.extended_openai_conversation_responses.management_function_repair import (
    persisted_config_projection,
    repair_state_for_projection,
)
from tests.test_save_validation_reuse import _hass_with_agent


@pytest.fixture(params=["missing-name", "object-member", "list-member"])
def grouped_agent(request, monkeypatch, hass):
    _, entry, subentry = _hass_with_agent()
    raw = deepcopy(subentry.data)
    if request.param == "missing-name":
        del raw["function_groups"][0]["name"]
    else:
        raw["function_groups"][0]["functions"].append(
            {"invalid": "member"} if request.param == "object-member" else ["invalid"]
        )
    raw["opaque_extension"] = {"untouched": [False, None]}
    subentry.data = raw
    original = deepcopy(raw)
    hass.config_entries.async_get_entry.return_value = entry

    def apply(_entry, _subentry, *, data, title=None):
        subentry.data = data
        if title is not None:
            subentry.title = title

    hass.config_entries.async_update_subentry.side_effect = apply
    monkeypatch.setattr(ui, "entry_and_agent", lambda *_: (entry, subentry))
    monkeypatch.setattr(
        ui,
        "async_get_knowledge",
        AsyncMock(return_value=SimpleNamespace(total_source_count=0)),
    )
    monkeypatch.setattr(
        ui, "decorate_configuration_result", lambda _h, _d, result, **kw: result
    )
    return hass, entry, subentry, original


@pytest.mark.parametrize(
    "route", ["get-save", "cold-save", "update", "validate", "knowledge"]
)
async def test_group_only_public_field_edits_preserve_exact_raw_definitions(
    grouped_agent, route
):
    hass, entry, subentry, original = grouped_agent
    message = {
        "entry_id": entry.entry_id,
        "subentry_id": subentry.subentry_id,
        "section": "configuration",
        "revision": persisted_config_projection(subentry).revision,
    }
    if route == "get-save":
        read = await ui.async_management_command(
            hass, "admin", True, {**message, "action": "get"}
        )
        assert read["function_repair"]["group_issues"]
        assert subentry.data == original
        message["revision"] = read["revision"]
    updates = {"prompt": "Changed independently"}
    action = route if route in {"update", "validate"} else "save"
    if route == "knowledge":
        message.update(section="knowledge", enabled=True)
        updates = {"knowledge_enabled": True}
        action = "set_enabled"
    result = await ui.async_management_command(
        hass, "admin", True, {**message, "action": action, "config": updates}
    )
    assert result.get("valid", True), result
    assert subentry.data == (
        original if route == "validate" else {**original, **updates}
    )
    assert subentry.data["functions"] == original["functions"]
    assert subentry.data["function_groups"] == original["function_groups"]


@pytest.mark.parametrize(
    "projection",
    [
        backup.export_configuration_snapshot,
        backup.private_configuration_snapshot,
        backup.recoverable_configuration_snapshot,
    ],
)
def test_group_only_recovery_projections_preserve_raw_groups(grouped_agent, projection):
    _, _, subentry, original = grouped_agent
    snapshot = projection(subentry.data)
    assert snapshot["function_groups"] == original["function_groups"]
    assert snapshot["opaque_extension"] == original["opaque_extension"]
    assert subentry.data == original


async def test_group_only_backup_export_preserves_raw_definition(
    grouped_agent, monkeypatch
):
    hass, entry, subentry, original = grouped_agent
    payloads = (
        {"memories": []},
        {"records": []},
        {"sources": []},
        {"sessions": [], "turns": []},
        {"totals": {}, "daily": {}, "requests": [], "runs": []},
        {"schedule": None},
        {"storage_version": 1, "defaults": {}, "rules": []},
    )
    managers = tuple(
        SimpleNamespace(async_backup_data=AsyncMock(return_value=p)) for p in payloads
    )
    monkeypatch.setattr(backup, "_managers", AsyncMock(return_value=managers))
    document = await ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "section": "backup",
            "action": "create",
        },
    )
    assert (
        document["document"]["agent"]["config"]["function_groups"]
        == original["function_groups"]
    )
    assert subentry.data == original


@pytest.mark.parametrize("field", ["functions", "function_groups"])
async def test_submitted_function_fields_still_require_strict_validation(
    grouped_agent, field
):
    hass, entry, subentry, original = grouped_agent
    result = await ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "section": "configuration",
            "action": "save",
            "revision": persisted_config_projection(subentry).revision,
            "config": {field: "not a collection"},
        },
    )
    assert result["valid"] is False
    assert subentry.data == original


def test_group_repair_records_nonstring_members_without_crashing(grouped_agent):
    _, _, subentry, original = grouped_agent
    state, _ = repair_state_for_projection(persisted_config_projection(subentry))
    assert state is not None and state.group_issues
    assert state.raw_groups == original["function_groups"]
    assert subentry.data == original


@pytest.mark.parametrize("malformed", ["not: a tool collection", "[unterminated YAML"])
@pytest.mark.parametrize("route", ["save", "update", "knowledge"])
async def test_unrecognizable_tool_collection_still_requires_repair(
    grouped_agent, malformed, route
):
    hass, entry, subentry, _ = grouped_agent
    subentry.data = {**subentry.data, "functions": malformed}
    original = deepcopy(subentry.data)
    message = {
        "entry_id": entry.entry_id,
        "subentry_id": subentry.subentry_id,
        "section": "configuration",
        "action": route,
        "revision": persisted_config_projection(subentry).revision,
        "config": {"prompt": "Edited"},
    }
    if route == "knowledge":
        message.update(section="knowledge", action="set_enabled", enabled=True)
    if route == "save":
        result = await ui.async_management_command(hass, "admin", True, message)
        assert result["valid"] is False and "functions" in result["errors"]
    else:
        with pytest.raises(AgentConfigError, match="repair the malformed"):
            await ui.async_management_command(hass, "admin", True, message)
    assert subentry.data == original


@pytest.mark.parametrize("legacy", [None, [], "", "null"])
def test_group_recovery_retains_legacy_default_tool_semantics(hass, legacy):
    from custom_components.extended_openai_conversation_responses.agent_config import (
        configured_function_tools_from_data,
        function_tool_yaml,
    )

    raw = {"functions": legacy, "function_groups": [{"id": "broken", "functions": []}]}
    state, _ = repair_state_for_projection(
        persisted_config_projection(SimpleNamespace(data=raw, title="Legacy"))
    )
    assert state is not None
    assert [function_tool_yaml(tool) for tool in state.valid] == [
        function_tool_yaml(tool) for tool in configured_function_tools_from_data(raw)
    ]
