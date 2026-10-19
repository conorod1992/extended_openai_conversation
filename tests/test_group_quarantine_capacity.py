"""Quarantined definitions retain their slots in the saved Group limit."""

from copy import deepcopy

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import management_ui as ui
from tests.test_group_assignment_transitions import duplicate_assignment_agent
from tests.test_group_repair_command_contracts import message, mixed


@pytest.mark.parametrize("mixed_tools", [False, True])
@pytest.mark.parametrize(
    "operation", ["create-at-limit", "create-below-limit", "repair", "metadata"]
)
async def test_group_capacity_counts_raw_quarantined_slots(
    duplicate_assignment_agent, mixed_tools, operation
):
    hass, entry, subentry = duplicate_assignment_agent
    alpha, beta, _ = deepcopy(subentry.data["function_groups"])
    beta.pop("description")
    beta["functions"] = []
    count = 49 if operation == "create-below-limit" else 50
    groups = [
        alpha,
        *[
            {**alpha, "id": f"group-{i}", "name": f"Group {i}", "functions": []}
            for i in range(count - 2)
        ],
        beta,
    ]
    subentry.data = {**subentry.data, "function_groups": groups}
    if mixed_tools:
        mixed(subentry)
    before = deepcopy(subentry.data)
    if operation == "repair":
        candidate = {**beta, "description": "Repaired"}
        options = {"original_id": "beta", "repair": True}
    elif operation == "metadata":
        candidate = {**alpha, "description": "Changed metadata"}
        options = {"original_id": "alpha"}
    else:
        candidate = {**alpha, "id": "new-group", "name": "New Group", "functions": []}
        options = {}
    command = message(
        entry, subentry, "tools", "save_group", group=candidate, **options
    )
    if operation == "create-at-limit":
        with pytest.raises(HomeAssistantError, match="50"):
            await ui.async_management_command(hass, "admin", True, command)
        assert subentry.data == before
        hass.config_entries.async_update_subentry.assert_not_called()
    else:
        result = await ui.async_management_command(hass, "admin", True, command)
        assert len(subentry.data["function_groups"]) == 50
        if operation != "repair":
            assert subentry.data["function_groups"][-1] == beta
        assert subentry.data["functions"] == before["functions"]
        read = await ui.async_management_command(
            hass, "admin", True, message(entry, subentry, "configuration", "get")
        )
        assert result["function_groups"] == read["config"]["function_groups"]
