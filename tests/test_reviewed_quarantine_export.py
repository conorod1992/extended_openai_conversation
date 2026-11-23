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


def setup_roundtrip(config):
    document = _portable_document(mode="setup")
    document["version"] = SHARING_VERSION
    document["sections"]["configuration"] = backup.export_configuration_snapshot(config)
    result = transfer.finalize_setup_export(document)
    restored = transfer.inspect_transfer(result["json"], "target")
    return result, restored.config


@pytest.mark.parametrize(
    "damage", ["healthy", "missing_spec", "missing_name", "invalid_group"]
)
def test_restricted_setup_still_omits_custom_function_records(hass, damage):
    result, restored = setup_roundtrip(quarantine_source(damage))
    projected = result["document"]["sections"]["configuration"]
    assert projected["functions"] == []
    assert projected["function_groups"] == []
    assert effective_tool_names(restored) == set()
    assert result["security"]["omitted"]
