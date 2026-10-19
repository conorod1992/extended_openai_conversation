"""Repair settings saves must reject edits made during validation."""

from copy import deepcopy

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import management_ui as ui
from tests.test_group_assignment_transitions import duplicate_assignment_agent
from tests.test_group_repair_command_contracts import message


@pytest.mark.parametrize("with_revision", [False, True])
async def test_repair_configuration_save_rechecks_revision_after_validation(
    duplicate_assignment_agent, monkeypatch, with_revision
):
    hass, entry, subentry = duplicate_assignment_agent
    latest = None

    async def concurrent_validation(*_args, **_kwargs):
        nonlocal latest
        subentry.data = {
            **subentry.data,
            "prompt": "Newest prompt",
            "function_groups": [
                *subentry.data["function_groups"],
                {"id": "new-raw-group", "opaque": True},
            ],
        }
        latest = deepcopy(subentry.data)

    monkeypatch.setattr(
        ui, "_async_validate_configuration_request", concurrent_validation
    )
    command = message(
        entry,
        subentry,
        "function_repair",
        "configuration_save",
        config={"prompt": "Stale prompt"},
    )
    if not with_revision:
        command.pop("revision")
    with pytest.raises(HomeAssistantError, match="changed"):
        await ui.async_management_command(hass, "admin", True, command)
    assert subentry.data == latest
    hass.config_entries.async_update_subentry.assert_not_called()
