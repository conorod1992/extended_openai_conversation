"""Preview/save share admission, without treating a display candidate as storage."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_function_repair as repair,
    management_ui as ui,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_group_assignment_transitions import duplicate_assignment_agent  # noqa: F401
from tests.test_group_repair_command_contracts import message


@pytest.mark.parametrize("action", ["configuration_validate", "configuration_save"])
@pytest.mark.parametrize("candidate", [None, [], "bad", 1])
async def test_field_candidate_requires_a_mapping(
    duplicate_assignment_agent, action, candidate
):
    hass, entry, subentry = duplicate_assignment_agent
    before = deepcopy(subentry.data)
    with pytest.raises(HomeAssistantError, match="config must be an object"):
        await repair.async_function_repair(
            hass, "admin", True,
            message(entry, subentry, "function_repair", action, config=candidate),
        )
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize("action", ["configuration_validate", "configuration_save"])
@pytest.mark.parametrize("field", ["functions", "function_groups"])
async def test_field_candidate_cannot_take_ownership_of_quarantined_fields(
    duplicate_assignment_agent, action, field
):
    hass, entry, subentry = duplicate_assignment_agent
    before = deepcopy(subentry.data)
    result = await repair.async_function_repair(
        hass, "admin", True,
        message(entry, subentry, "function_repair", action, config={field: []}),
    )
    assert result["valid"] is False
    assert "Repair invalid Function Tools" in result["errors"]["functions"]
    assert subentry.data == before
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_preview_and_save_agree_but_only_owned_raw_fields_are_written(
    duplicate_assignment_agent, monkeypatch
):
    hass, entry, subentry = duplicate_assignment_agent
    subentry.data = {**subentry.data, "future_option": {"opaque": [1, 2]}}
    subentry.data.pop("reasoning_effort", None)
    before = deepcopy(subentry.data)
    # Keep this focused on field admission/persistence; model validation and
    # post-await concurrency have separate public-command regressions.
    monkeypatch.setattr(ui, "_async_validate_configuration_request", AsyncMock())
    outputs = []
    for action in ("configuration_validate", "configuration_save"):
        outputs.append(await repair.async_function_repair(
            hass, "admin", True,
            message(entry, subentry, "function_repair", action, config={"max_tokens": 700}),
        ))
        if action == "configuration_validate":
            assert subentry.data == before
    preview, saved = outputs
    assert preview["valid"] is saved["valid"] is True
    assert preview["config"] == saved["config"]
    assert subentry.data == {**before, "max_tokens": 700}
    assert subentry.data["future_option"] is not before["future_option"]
    assert subentry.data["functions"] == before["functions"]
    assert subentry.data["function_groups"] == before["function_groups"]
    assert "reasoning_effort" not in subentry.data
