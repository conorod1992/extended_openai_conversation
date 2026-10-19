"""Changed Group names reserve every saved sibling, without blocking old damage."""

from copy import deepcopy

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import management_ui as ui
from tests.test_group_assignment_transitions import duplicate_assignment_agent
from tests.test_group_repair_command_contracts import message


@pytest.mark.parametrize("operation", ["create", "rename"])
@pytest.mark.parametrize("name", ["Beta", "bEtA", " Beta "])
@pytest.mark.parametrize("damage", ["missing-description", "missing-id"])
async def test_changed_name_reserves_quarantined_sibling_names(
    duplicate_assignment_agent, operation, name, damage
):
    hass, entry, subentry = duplicate_assignment_agent
    alpha, beta, gamma = deepcopy(subentry.data["function_groups"])
    beta.pop("description" if damage == "missing-description" else "id")
    beta["functions"] = []
    subentry.data = {
        **subentry.data,
        "function_groups": [alpha, beta, gamma],
        "guest_allowed_group_ids": ["alpha"],
    }
    before = deepcopy(subentry.data)
    candidate = {**alpha, "name": name}
    options = {"original_id": "alpha"} if operation == "rename" else {}
    if operation == "create":
        candidate.update(id="delta", functions=[])
    with pytest.raises(HomeAssistantError, match="[Dd]uplicate.*[Gg]roup name"):
        await ui.async_management_command(
            hass,
            "admin",
            True,
            message(entry, subentry, "tools", "save_group", group=candidate, **options),
        )
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize("name", ["Alpha", "ALPHA", " Alpha "])
async def test_existing_name_collision_does_not_block_metadata_edit(
    duplicate_assignment_agent, name
):
    hass, entry, subentry = duplicate_assignment_agent
    alpha, beta, gamma = deepcopy(subentry.data["function_groups"])
    beta.update(name="Alpha", functions=[])
    subentry.data = {**subentry.data, "function_groups": [alpha, beta, gamma]}
    await ui.async_management_command(
        hass,
        "admin",
        True,
        message(
            entry,
            subentry,
            "tools",
            "save_group",
            original_id="alpha",
            group={**alpha, "name": name, "description": "Metadata edit"},
        ),
    )
    assert (
        next(g for g in subentry.data["function_groups"] if g["id"] == "beta") == beta
    )
    assert (
        next(g for g in subentry.data["function_groups"] if g["id"] == "alpha")[
            "description"
        ]
        == "Metadata edit"
    )
