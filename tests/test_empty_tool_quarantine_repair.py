"""Public export, quarantine and nested-entry regressions from the follow-up audit."""

from copy import deepcopy

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import management_ui as ui
from tests.test_group_assignment_transitions import duplicate_assignment_agent  # noqa: F401
from tests.test_group_repair_command_contracts import message


@pytest.mark.parametrize("name", ["execute_services", "renamed_private_tool"])
@pytest.mark.parametrize("operation", ["create", "repair", "delete"])
async def test_empty_usable_tools_do_not_become_defaults(
    duplicate_assignment_agent, name, operation
):
    hass, entry, subentry = duplicate_assignment_agent
    original_tool = yaml.safe_load(subentry.data["functions"])[0]
    broken = deepcopy(original_tool)
    broken["spec"]["name"] = name
    broken["spec"]["parameters"]["description"] = 123
    group = {**subentry.data["function_groups"][0], "functions": [name]}
    raw_tools = yaml.safe_dump([broken])
    subentry.data = {
        **subentry.data,
        "functions": raw_tools,
        "function_groups": [group],
    }
    if operation == "create":
        command = message(entry, subentry, "tools", "save", tool=original_tool)
    elif operation == "repair":
        command = message(
            entry,
            subentry,
            "tools",
            "save_group",
            original_id=group["id"],
            repair=True,
            group={**group, "functions": []},
        )
    else:
        command = message(
            entry, subentry, "tools", "delete_group", group_id=group["id"], confirm=True
        )
    result = await ui.async_management_command(hass, "admin", True, command)
    assert result.get("valid", True), result
    if operation == "create":
        assert subentry.data["function_groups"] == [group]
        assert len(yaml.safe_load(subentry.data["functions"])) == 2
    else:
        assert subentry.data["functions"] == raw_tools
        assert subentry.data["function_groups"] == (
            []
            if operation == "delete"
            else [
                {
                    k: v
                    for k, v in {**group, "functions": []}.items()
                    if k != "guest_allowed"
                }
            ]
        )


@pytest.mark.parametrize("repair", [False, True])
def test_public_group_repair_schema_accepts_boolean_flag(repair):
    payload = {
        "id": 1,
        "type": ui.WS_COMMAND,
        "section": "tools",
        "action": "save_group",
        "original_id": "repair-group",
        "repair": repair,
        "group": {},
    }
    assert ui.websocket_management._ws_schema(payload) == payload
