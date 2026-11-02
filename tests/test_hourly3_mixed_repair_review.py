"""Collection-wide Functions and malformed Groups have a complete repair path."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import management_ui as ui
from custom_components.extended_openai_conversation_responses.function_configuration_repair import (
    function_configuration_repair_state,
)
from custom_components.extended_openai_conversation_responses.management_function_repair import (
    repair_revision,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_function_group_collection_repair import _group, _source
from tests.test_request_rules import MemoryStore


@pytest.mark.parametrize("duplicate_groups", [False, True])
async def test_function_collection_can_be_repaired_before_malformed_group_collection(
    hass, management_agent, monkeypatch, duplicate_groups
):
    entry, subentry = management_agent
    raw_groups = (
        [_group("duplicate"), _group("duplicate", name="Other duplicate")]
        if duplicate_groups
        else 42
    )
    subentry.data = _source(raw_groups)
    tools = yaml.safe_load(subentry.data["functions"])
    subentry.data["functions"] = yaml.safe_dump(
        [tools[0], deepcopy(tools[0]), *tools[1:]]
    )
    subentry.data["guest_allowed_group_ids"] = ["duplicate"] if duplicate_groups else []
    manager = RequestRules(MemoryStore())
    await manager.async_initialize()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=manager))

    async def command(action, **fields):
        return await ui.async_management_command(
            hass,
            "admin",
            True,
            {
                "section": "function_repair",
                "action": action,
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                "revision": repair_revision(subentry),
                **fields,
            },
        )

    assert not function_configuration_repair_state(subentry.data).field_updates_allowed
    before = deepcopy(subentry.data)
    with pytest.raises(HomeAssistantError, match="malformed Function Tool collection"):
        await command("save_groups", groups=[_group()], guest_allowed_group_ids=[])
    assert subentry.data == before
    first = await command("save", tools=tools)
    assert first["valid"] is True
    assert subentry.data == {
        **before,
        "functions": yaml.safe_dump(tools, sort_keys=False),
    }
    assert first["function_repair"]["tools_valid"] is True
    assert first["function_repair"]["group_collection_repair"] is True
    assert yaml.safe_load(first["function_repair"]["groups_yaml"]) == raw_groups
    saved_functions = subentry.data["functions"]
    if duplicate_groups:
        with pytest.raises(HomeAssistantError, match="Explicit Guest Group references"):
            await command("save_groups", groups=[_group()])
    final = await command("save_groups", groups=[_group()], guest_allowed_group_ids=[])
    assert final["valid"] is True
    assert function_configuration_repair_state(subentry.data) is None
    assert subentry.data["functions"] == saved_functions
    assert subentry.data["guest_allowed_group_ids"] == []
    assert subentry.data["prompt"] == before["prompt"]
    assert subentry.data["future_option"] == before["future_option"]
