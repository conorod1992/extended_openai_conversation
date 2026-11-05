"""Reviewed sharing keeps safe repair data without promoting executable tools."""

from copy import deepcopy

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.function_configuration_repair import (
    function_configuration_repair_state,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    assemble_function_tools,
)
from custom_components.extended_openai_conversation_responses.sharing import (
    SHARING_VERSION,
)
from tests.test_function_groups import _group, _tool
from tests.test_secret_restore_context import _rest_tool
from tests.test_transfer import _portable_document


def quarantine_source(damage, *, encoded=False):
    held = {**_tool("held_tool"), "enabled": True}
    tools = [held]
    groups = [_group("held", ["held_tool"], "always", enabled=False)]
    if damage in {"missing_spec", "missing_name", "spec_list", "name_list"}:
        broken = deepcopy(_tool("broken_tool"))
        if damage == "missing_spec":
            del broken["spec"]
        elif damage == "missing_name":
            del broken["spec"]["name"]
        elif damage == "spec_list":
            broken["spec"] = []
        else:
            broken["spec"]["name"] = ["broken_tool"]
        tools.append(broken)
    elif damage == "scalar_tool":
        tools.append("quarantined tool")
    elif damage == "null_tool":
        tools.append(None)
    elif damage == "invalid_group":
        groups.append("quarantined group")
    elif damage == "invalid_members":
        groups.append({**_group("broken", []), "functions": ["held_tool", {}]})
    elif damage == "member_string":
        groups.append({**_group("broken", []), "functions": "held_tool"})
    elif damage == "group_collection":
        groups = {"quarantined": "group collection"}
    elif damage == "unknown_member":
        groups[0]["functions"].append("missing_tool")
    else:
        assert damage == "healthy"
    return {
        "functions": yaml.safe_dump(tools, sort_keys=False) if encoded else tools,
        "function_groups": groups,
    }


def effective_tool_names(config):
    repair = function_configuration_repair_state(config)
    if repair:
        tools, groups = repair.valid, repair.groups
    else:
        snapshot = agent_config_snapshot(config)
        tools, groups = snapshot["functions"], snapshot["function_groups"]
    return {
        tool["spec"]["name"]
        for tool in assemble_function_tools(tools, groups, set()).tools
    }


def setup_roundtrip(config, *, reviewed):
    document = _portable_document(mode="setup")
    document["version"] = SHARING_VERSION
    document["sections"]["configuration"] = backup.export_configuration_snapshot(config)
    result = transfer.finalize_setup_export(document, reviewed=reviewed)
    restored = transfer.inspect_transfer(result["json"], "target")
    return result, restored.config


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize(
    "damage",
    [
        "healthy",
        "missing_spec",
        "missing_name",
        "spec_list",
        "name_list",
        "scalar_tool",
        "null_tool",
        "invalid_group",
        "invalid_members",
        "member_string",
        "group_collection",
        "unknown_member",
    ],
)
def test_reviewed_setup_preserves_safe_quarantined_records(hass, damage, encoded):
    config = quarantine_source(damage, encoded=encoded)
    before = deepcopy(config)
    expected = backup.export_configuration_snapshot(config)
    result, restored = setup_roundtrip(config, reviewed=True)
    projected = result["document"]["sections"]["configuration"]
    assert projected["functions"] == expected["functions"]
    assert projected["function_groups"] == expected["function_groups"]
    assert config == before
    assert effective_tool_names(restored) == effective_tool_names(config)
    assert bool(function_configuration_repair_state(restored)) == (damage != "healthy")
    assert not result["security"]["omitted"] or all(
        not item["field"].startswith(("function_tool_", "function_group_"))
        for item in result["security"]["omitted"]
    )


@pytest.mark.parametrize(
    "damage", ["healthy", "missing_spec", "missing_name", "invalid_group"]
)
def test_restricted_setup_still_omits_custom_function_records(hass, damage):
    result, restored = setup_roundtrip(quarantine_source(damage), reviewed=False)
    projected = result["document"]["sections"]["configuration"]
    assert projected["functions"] == []
    assert projected["function_groups"] == []
    assert effective_tool_names(restored) == set()
    assert result["security"]["omitted"]


@pytest.mark.parametrize("mode,enabled", [("always", False), ("on_demand", True)])
@pytest.mark.parametrize("damage", ["missing_spec", "invalid_group", "invalid_members"])
def test_private_tool_omission_keeps_quarantine_and_group_restrictions(
    hass, damage, mode, enabled
):
    config = quarantine_source(damage)
    config["function_groups"][0].update(loading_mode=mode, enabled=enabled)
    private = _rest_tool()
    private["spec"]["name"] = "private_api"
    config["functions"].append(private)
    result, restored = setup_roundtrip(config, reviewed=True)
    assert effective_tool_names(restored) == effective_tool_names(config) - {
        "private_api"
    }
    assert function_configuration_repair_state(restored)
    assert any(
        item["field"].startswith("function_tool_")
        for item in result["security"]["omitted"]
    )
    assert config["function_groups"][0]["enabled"] is enabled


def test_omission_cannot_activate_an_ambiguous_quarantined_tool_collection(hass):
    held = {**_tool("held_tool"), "enabled": True}
    private = _rest_tool()
    private["spec"]["name"] = "held_tool"
    config = {"functions": [held, private], "function_groups": []}
    repair = function_configuration_repair_state(config)
    assert repair and not repair.field_updates_allowed
    assert not effective_tool_names(config)
    result, restored = setup_roundtrip(config, reviewed=True)
    assert not effective_tool_names(restored)
    assert any(
        item["field"].endswith("disabled") for item in result["security"]["omitted"]
    )


def test_safe_ambiguous_collection_remains_quarantined_without_omission(hass):
    tool = {**_tool("held_tool"), "enabled": True}
    config = {"functions": [tool, deepcopy(tool)], "function_groups": []}
    result, restored = setup_roundtrip(config, reviewed=True)
    expected = backup.export_configuration_snapshot(config)
    assert (
        result["document"]["sections"]["configuration"]["functions"]
        == expected["functions"]
    )
    repair = function_configuration_repair_state(restored)
    assert repair and not repair.field_updates_allowed
    assert not effective_tool_names(restored)
    assert not any(
        item["field"].endswith("disabled") for item in result["security"]["omitted"]
    )


def test_secret_in_unnamed_quarantined_tool_is_omitted(hass):
    config = quarantine_source("missing_name")
    private = _rest_tool()
    del private["spec"]["name"]
    config["functions"][1] = private
    before = deepcopy(config)
    result, restored = setup_roundtrip(config, reviewed=True)
    assert effective_tool_names(restored) == effective_tool_names(config)
    assert len(result["document"]["sections"]["configuration"]["functions"]) == 1
    assert any(
        item["field"] == "function_tool_2" for item in result["security"]["omitted"]
    )
    assert private["function"]["headers"]["Authorization"] not in result["json"]
    assert config == before


def test_unsafe_malformed_group_disables_identifiable_members(hass):
    config = quarantine_source("healthy")
    group = {
        **_group("unsafe", ["held_tool", {}]),
        "description": "Authorization: Bearer sk-FABRICATED123456789012345",
    }
    config["function_groups"] = [group]
    before = deepcopy(config)
    result, restored = setup_roundtrip(config, reviewed=True)
    assert not effective_tool_names(restored)
    assert (
        result["document"]["sections"]["configuration"]["functions"][0]["enabled"]
        is False
    )
    assert any(
        item["field"] == "function_group_member_disabled"
        for item in result["security"]["omitted"]
    )
    assert "FABRICATED" not in result["json"]
    assert config == before


def test_private_member_omission_preserves_safe_and_quarantined_groups(hass):
    config = quarantine_source("invalid_group")
    private = _rest_tool()
    private["spec"]["name"] = "private_api"
    config["functions"].append(private)
    config["function_groups"][0]["functions"].append("private_api")
    result, restored = setup_roundtrip(config, reviewed=True)
    projected = result["document"]["sections"]["configuration"]
    assert projected["function_groups"][0]["functions"] == ["held_tool"]
    assert projected["function_groups"][1] == "quarantined group"
    assert not effective_tool_names(restored)
    assert function_configuration_repair_state(restored).group_issues


def test_unsafe_opaque_group_collection_is_omitted_without_inventing_memberships(hass):
    config = quarantine_source("group_collection")
    config["function_groups"]["Authorization"] = "Bearer local-credential"
    before = deepcopy(config)
    result, restored = setup_roundtrip(config, reviewed=True)
    assert result["document"]["sections"]["configuration"]["function_groups"] == []
    assert effective_tool_names(restored) == effective_tool_names(config)
    assert any(
        item["field"] == "function_groups" for item in result["security"]["omitted"]
    )
    assert "local-credential" not in result["json"]
    assert config == before
