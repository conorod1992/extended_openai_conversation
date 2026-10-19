"""Single-Tool repair must preserve collection-level uniqueness."""

from copy import deepcopy

import pytest
import yaml
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import management_ui as ui
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    new_reference_tool,
)
from tests.test_group_assignment_transitions import duplicate_assignment_agent
from tests.test_group_repair_command_contracts import message
from tests.test_ha_llm_tools import reference


@pytest.mark.parametrize("other_invalid", [False, True])
@pytest.mark.parametrize("conflict", [False, True])
async def test_single_repair_checks_candidate_against_valid_siblings(
    duplicate_assignment_agent, other_invalid, conflict
):
    hass, entry, subentry = duplicate_assignment_agent
    native = yaml.safe_load(subentry.data["functions"])[0]
    invalid = deepcopy(native)
    invalid["spec"]["name"] = "legacy_probe"
    invalid["function"]["name"] = "missing_legacy_implementation"
    first = new_reference_tool(reference(), set())
    tools = [first, invalid]
    if other_invalid:
        sibling = deepcopy(invalid)
        sibling["spec"]["name"] = "other_legacy"
        tools.append(sibling)
    subentry.data = {**subentry.data, "functions": yaml.safe_dump(tools)}
    before = deepcopy(subentry.data)
    candidate = deepcopy(first) if conflict else deepcopy(native)
    candidate["spec"]["name"] = "repaired_probe"
    command = message(
        entry, subentry, "function_repair", "save_one", index=1, tool=candidate
    )
    if conflict:
        with pytest.raises(
            HomeAssistantError, match="HA tool reference already configured"
        ):
            await ui.async_management_command(hass, "admin", True, command)
        assert subentry.data == before
        hass.config_entries.async_update_subentry.assert_not_called()
    else:
        result = await ui.async_management_command(hass, "admin", True, command)
        assert yaml.safe_load(subentry.data["functions"]) == [
            first,
            candidate,
            *tools[2:],
        ]
        assert subentry.data["function_groups"] == before["function_groups"]
        assert len(result["function_repair"]["invalid_tools"]) == int(other_invalid)
        assert {tool["spec"]["name"] for tool in result["config"]["functions"]} == {
            first["spec"]["name"],
            "repaired_probe",
        }
