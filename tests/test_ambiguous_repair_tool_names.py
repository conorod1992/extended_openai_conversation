"""Duplicate raw Tool names require explicit collection repair."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
import yaml
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import management_ui as ui
from tests.test_group_assignment_transitions import duplicate_assignment_agent
from tests.test_group_repair_command_contracts import message


@pytest.mark.parametrize("valid_sibling", [False, True])
@pytest.mark.parametrize("action", ["read", "save_one", "delete_one", "bulk"])
async def test_ambiguous_raw_names_cannot_authorize_reference_changes(
    duplicate_assignment_agent, monkeypatch, valid_sibling, action
):
    hass, entry, subentry = duplicate_assignment_agent
    valid = yaml.safe_load(subentry.data["functions"])[0]
    sibling = deepcopy(valid)
    if not valid_sibling:
        sibling["function"]["name"] = "missing_first_implementation"
    invalid = deepcopy(valid)
    invalid["function"]["name"] = "missing_second_implementation"
    subentry.data = {**subentry.data, "functions": yaml.safe_dump([sibling, invalid])}
    before = deepcopy(subentry.data)
    if action == "read":
        result = await ui.async_management_command(
            hass, "admin", True, message(entry, subentry, "configuration", "get")
        )
        assert result["function_repair"]["isolatable"] is False
        assert result["function_repair"]["tools_valid"] is False
        assert "duplicate tool name" in result["function_repair"]["validation_error"]
        assert result["function_repair"]["tools"] == [sibling, invalid]
    elif action == "bulk":
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
        return
    else:
        candidate = deepcopy(valid)
        candidate["spec"]["name"] = "renamed_probe"
        with pytest.raises(HomeAssistantError, match="cannot be isolated"):
            await ui.async_management_command(
                hass,
                "admin",
                True,
                message(
                    entry, subentry, "function_repair", action, index=1, tool=candidate
                ),
            )
        ui._function_reference_state.assert_not_awaited()
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()
