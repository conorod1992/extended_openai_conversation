"""Legacy Tool writers must not overwrite changes made during validation."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
import yaml
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import management_ui as ui
from tests.test_group_assignment_transitions import duplicate_assignment_agent
from tests.test_group_repair_command_contracts import message


@pytest.mark.parametrize("quarantined", [False, True])
@pytest.mark.parametrize("with_revision", [False, True])
@pytest.mark.parametrize(
    "action", ["save", "set_enabled", "save_group", "delete_group"]
)
async def test_tool_writes_recheck_revision_after_dependency_validation(
    duplicate_assignment_agent, monkeypatch, quarantined, with_revision, action
):
    hass, entry, subentry = duplicate_assignment_agent
    if not quarantined:
        subentry.data = {
            **subentry.data,
            "function_groups": [
                subentry.data["function_groups"][0],
                subentry.data["function_groups"][2],
            ],
        }
    tool = yaml.safe_load(subentry.data["functions"])[0]
    group = deepcopy(subentry.data["function_groups"][0])
    options = (
        {"original_name": "native_probe", "tool": {**tool, "enabled": False}}
        if action == "save"
        else {"name": "native_probe", "enabled": False}
        if action == "set_enabled"
        else {"original_id": "alpha", "group": {**group, "description": "Stale update"}}
        if action == "save_group"
        else {"group_id": "alpha", "confirm": True}
    )
    command = message(entry, subentry, "tools", action, **options)
    if not with_revision:
        command.pop("revision")
    newer = deepcopy(subentry.data)
    another_tool = deepcopy(tool)
    another_tool["spec"]["name"] = "concurrently_added"
    newer["functions"] = yaml.safe_dump([tool, another_tool])
    newer["function_groups"][0]["description"] = "Newer edit"

    async def concurrent_edit(*_args, **_kwargs):
        subentry.data = deepcopy(newer)

    monkeypatch.setattr(
        ui,
        "_async_validate_configuration_dependencies",
        AsyncMock(side_effect=concurrent_edit),
    )
    with pytest.raises(
        HomeAssistantError, match="Configuration changed in another tab"
    ):
        await ui.async_management_command(hass, "admin", True, command)
    assert subentry.data == newer
    hass.config_entries.async_update_subentry.assert_not_called()
