"""Collection errors must retain recovery access and prevent partial replacement."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
import yaml
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses import management_ui as ui
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    new_reference_tool,
)
from tests.test_group_assignment_transitions import duplicate_assignment_agent
from tests.test_group_repair_command_contracts import message
from tests.test_ha_llm_tools import reference


@pytest.mark.parametrize(
    "section,action",
    [
        ("configuration", "get"),
        ("function_repair", "configuration_get"),
        ("function_repair", "get"),
    ],
)
async def test_collection_repair_editor_retains_typed_variables(
    hass,
    management_agent,
    management_message,
    monkeypatch,
    section,
    action,
):
    from datetime import date
    import json
    import subprocess
    from pathlib import Path
    from custom_components.extended_openai_conversation_responses.function_yaml import (
        management_function_response,
        decode_management_function_request,
    )
    from custom_components.extended_openai_conversation_responses.request_rules import (
        RequestRules,
    )
    from tests.test_request_rules import MemoryStore
    from custom_components.extended_openai_conversation_responses.management_function_repair import (
        discard_persisted_config_projection,
    )

    rules = RequestRules(MemoryStore())
    await rules.async_initialize()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=rules))
    _, subentry = management_agent
    variables = {
        "lookup": {7: "numeric", "7": "literal"},
        "large": 9007199254740993,
        "date": date(2026, 11, 1),
        "literal": {"__eoai_management_yaml_v1__": "7"},
        "small": 0.000001,
        "text": "1e-06",
    }
    tool = {
        "spec": {"name": "typed", "parameters": {"type": "object", "properties": {}}},
        "function": {
            "type": "script",
            "variables": variables,
            "sequence": [{"stop": "Done"}],
        },
    }
    subentry.data = {
        **subentry.data,
        "functions": yaml.safe_dump([tool, deepcopy(tool)]),
        "function_groups": [],
    }
    read = await ui.async_management_command(
        hass, "admin", True, management_message(section, action)
    )
    if "function_repair" in read:
        assert read["function_repair"]["isolatable"] is False
    else:
        assert "duplicate" in read["validation_error"]
    wire = management_function_response(read, section=section)
    script = """import {renderFallbackRepair} from './custom_components/extended_openai_conversation_responses/frontend/management-function-repair.js';
    let raw=''; for await (const chunk of process.stdin) raw+=chunk;
    const panel={_agentId:'agent',_result:JSON.parse(raw)};
    renderFallbackRepair(panel,{message:'repair'});
    process.stdout.write(panel._functionRepairDraft.text.replace('name: typed','name: renamed').replace('"name": "typed"','"name": "renamed"'));
    """
    rendered = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(wire),
        text=True,
        capture_output=True,
        check=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    request = decode_management_function_request(
        management_message(
            "function_repair", "save", tools=rendered.stdout, revision=read["revision"]
        )
    )
    await ui.async_management_command(hass, "admin", True, request)
    discard_persisted_config_projection(subentry)
    reloaded = await ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    for saved in reloaded["config"]["functions"]:
        assert saved["function"]["variables"] == variables
    assert [t["spec"]["name"] for t in reloaded["config"]["functions"]] == [
        "renamed",
        "typed",
    ]


@pytest.fixture(params=[False, True])
def collection_agent(duplicate_assignment_agent, request):
    hass, entry, subentry = duplicate_assignment_agent
    valid = yaml.safe_load(subentry.data["functions"])[0]
    tools = [valid, deepcopy(valid)]
    if request.param:
        invalid = deepcopy(valid)
        invalid["spec"]["name"] = "legacy_probe"
        invalid["function"]["name"] = "missing_legacy_implementation"
        tools.append(invalid)
    groups = deepcopy(subentry.data["function_groups"])
    groups[1].pop("name")
    subentry.data = {
        **subentry.data,
        "functions": "# Preserve collection for repair\n" + yaml.safe_dump(tools),
        "function_groups": groups,
    }
    return hass, entry, subentry, valid


@pytest.mark.parametrize(
    "section,action",
    [
        ("configuration", "get"),
        ("function_repair", "configuration_get"),
        ("function_repair", "get"),
        ("tools", "validate_current"),
    ],
)
async def test_mixed_collection_corruption_keeps_recovery_reads_available(
    collection_agent, section, action
):
    hass, entry, subentry, _ = collection_agent
    before = deepcopy(subentry.data)
    result = await ui.async_management_command(
        hass, "admin", True, message(entry, subentry, section, action)
    )
    if action == "validate_current":
        assert result["valid"] is False
        assert "functions" in result["errors"]
    elif section == "function_repair" and action == "get":
        assert result["tools"] == yaml.safe_load(before["functions"])
        assert "duplicate tool name" in result["validation_error"]
    else:
        assert result["function_repair"]["isolatable"] is False
        assert result["function_repair"]["tools_valid"] is False
        assert result["function_repair"]["tools"] == yaml.safe_load(before["functions"])
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize(
    "projection",
    [
        backup.export_configuration_snapshot,
        backup.private_configuration_snapshot,
        backup.recoverable_configuration_snapshot,
    ],
)
def test_collection_recovery_projections_preserve_all_raw_definitions(
    collection_agent, projection
):
    _, _, subentry, _ = collection_agent
    before = deepcopy(subentry.data)
    result = projection(subentry.data)
    assert result["function_groups"] == before["function_groups"]
    assert (
        yaml.safe_load(before["functions"])
        if projection is backup.export_configuration_snapshot
        else before["functions"]
    ) == result["functions"]
    assert subentry.data == before


@pytest.mark.parametrize(
    "operation", ["add-tool", "add-group", "edit-group", "repair-one", "delete-one"]
)
async def test_partial_commands_cannot_replace_a_nonisolatable_collection(
    collection_agent, operation
):
    hass, entry, subentry, valid = collection_agent
    before = deepcopy(subentry.data)
    if operation in {"add-tool", "repair-one", "delete-one"}:
        tool = deepcopy(valid)
        tool["spec"]["name"] = "new_probe"
        command = (
            message(entry, subentry, "tools", "save", tool=tool)
            if operation == "add-tool"
            else message(
                entry,
                subentry,
                "function_repair",
                "save_one" if operation == "repair-one" else "delete_one",
                index=0,
                tool=tool,
            )
        )
    else:
        group = {
            **before["function_groups"][0],
            "functions": [],
            "description": "Changed",
        }
        options = {"original_id": "alpha"} if operation == "edit-group" else {}
        if operation == "add-group":
            group.update(id="new-group", name="New Group")
        command = message(
            entry, subentry, "tools", "save_group", group=group, **options
        )
    with pytest.raises(HomeAssistantError, match="cannot be isolated"):
        await ui.async_management_command(hass, "admin", True, command)
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_bulk_repair_can_replace_the_collection_and_preserve_groups(
    collection_agent, monkeypatch
):
    hass, entry, subentry, valid = collection_agent
    before = deepcopy(subentry.data)
    monkeypatch.setattr(ui, "_async_validate_configuration_request", AsyncMock())
    result = await ui.async_management_command(
        hass,
        "admin",
        True,
        message(entry, subentry, "function_repair", "save", tools=[valid]),
    )
    assert result["valid"] is True
    assert yaml.safe_load(subentry.data["functions"]) == [valid]
    assert subentry.data["function_groups"] == before["function_groups"]
    assert result["function_repair"]["tools_valid"] is True
    read = await ui.async_management_command(
        hass, "admin", True, message(entry, subentry, "configuration", "get")
    )
    assert read["config"]["functions"] == result["config"]["functions"]


@pytest.mark.parametrize("mixed_tools", [False, True])
async def test_duplicate_ha_identity_uses_the_same_collection_repair_contract(
    duplicate_assignment_agent, mixed_tools
):
    hass, entry, subentry = duplicate_assignment_agent
    first = new_reference_tool(reference(), set())
    second = {**deepcopy(first), "spec": {"name": "another_local_alias"}}
    tools = [first, second]
    native = yaml.safe_load(subentry.data["functions"])[0]
    if mixed_tools:
        invalid = deepcopy(native)
        invalid["function"]["name"] = "missing_legacy_implementation"
        tools.append(invalid)
    subentry.data = {**subentry.data, "functions": yaml.safe_dump(tools)}
    before = deepcopy(subentry.data)
    result = await ui.async_management_command(
        hass, "admin", True, message(entry, subentry, "configuration", "get")
    )
    assert result["function_repair"]["isolatable"] is False
    assert (
        "HA tool reference already configured"
        in result["function_repair"]["validation_error"]
    )
    with pytest.raises(HomeAssistantError, match="cannot be isolated"):
        await ui.async_management_command(
            hass, "admin", True, message(entry, subentry, "tools", "save", tool=native)
        )
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize("action", ["configuration_validate", "configuration_save"])
async def test_legacy_settings_routes_require_an_isolatable_collection(
    collection_agent, monkeypatch, action
):
    hass, entry, subentry, _ = collection_agent
    before = deepcopy(subentry.data)
    monkeypatch.setattr(ui, "_async_validate_configuration_request", AsyncMock())
    result = await ui.async_management_command(
        hass,
        "admin",
        True,
        message(
            entry,
            subentry,
            "function_repair",
            action,
            config={"prompt": "Unrelated edit"},
        ),
    )
    assert result["valid"] is False
    assert "functions" in result["errors"]
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()
