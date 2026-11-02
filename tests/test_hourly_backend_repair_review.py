"""Independent adversarial journeys for persisted recovery editors."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import management_ui as ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.function_configuration_repair import (
    function_configuration_repair_state,
)
from custom_components.extended_openai_conversation_responses.management_function_repair import (
    repair_revision,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)
from custom_components.extended_openai_conversation_responses.skill_availability import (
    skill_loader_status,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_request_rules import MemoryStore


def _tool():
    return {
        "spec": {
            "name": "review_tool",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "Done"},
    }


@pytest.fixture
async def review_agent(hass, management_agent, monkeypatch):
    entry, subentry = management_agent
    manager = RequestRules(MemoryStore())
    await manager.async_initialize()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=manager))
    return entry, subentry


async def _save(hass, entry, subentry, tools, *, revision=None):
    return await ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "function_repair",
            "action": "save",
            "tools": tools,
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "revision": repair_revision(subentry) if revision is None else revision,
        },
    )


async def test_bulk_tool_repair_editor_can_save_newer_edits_after_first_commit(
    hass, review_agent
):
    entry, subentry = review_agent
    tool = _tool()
    subentry.data = {
        **subentry.data,
        "functions": yaml.safe_dump([tool, deepcopy(tool)]),
        "function_groups": [],
    }
    assert (
        function_configuration_repair_state(subentry.data).field_updates_allowed
        is False
    )
    await _save(hass, entry, subentry, [tool])
    assert function_configuration_repair_state(subentry.data) is None
    later = deepcopy(tool)
    later["spec"]["description"] = "Later edit authored while first save was pending"
    await _save(hass, entry, subentry, [later])
    assert yaml.safe_load(subentry.data["functions"]) == [later]


async def test_bulk_tool_repair_cannot_remove_available_selected_skill_loader(
    hass, review_agent
):
    entry, subentry = review_agent
    loader = next(
        tool
        for tool in yaml.safe_load(agent_config_defaults()["functions"])
        if tool["spec"]["name"] == "load_skill"
    )
    broken = _tool()
    broken["spec"]["name"] = "broken"
    broken["function"] = {"type": "native", "name": "unknown_legacy"}
    subentry.data = {
        **subentry.data,
        "functions": yaml.safe_dump([loader, _tool(), broken]),
        "function_groups": [],
        "skills": ["selected"],
    }
    state = function_configuration_repair_state(subentry.data)
    assert skill_loader_status(["selected"], state.valid, state.groups).available
    before = deepcopy(subentry.data)
    with pytest.raises(HomeAssistantError, match="load_skill"):
        await _save(hass, entry, subentry, [_tool()])
    assert subentry.data == before


@pytest.mark.parametrize("remove_loader", [True, False])
async def test_fallback_repair_then_healthy_followup_preserves_selected_skill(
    hass, review_agent, remove_loader
):
    entry, subentry = review_agent
    loader = next(
        tool
        for tool in yaml.safe_load(agent_config_defaults()["functions"])
        if tool["spec"]["name"] == "load_skill"
    )
    subentry.data = {
        **subentry.data,
        "functions": yaml.safe_dump([loader, deepcopy(loader)]),
        "function_groups": [],
        "skills": ["selected"],
        "prompt": "Retained prompt",
        "future_option": {"retained": True},
    }
    state = function_configuration_repair_state(subentry.data)
    assert not state.field_updates_allowed
    before = deepcopy(subentry.data)
    first = await _save(hass, entry, subentry, [loader, _tool()])
    assert function_configuration_repair_state(subentry.data) is None
    later = _tool()
    later["spec"]["description"] = "Later edit made while first repair was pending"
    second = await _save(
        hass, entry, subentry, [loader, later], revision=first["revision"]
    )
    assert second["valid"] is True and second["revision"] != first["revision"]
    assert {
        key: value for key, value in subentry.data.items() if key != "functions"
    } == {key: value for key, value in before.items() if key != "functions"}
    committed = deepcopy(subentry.data)
    with pytest.raises(HomeAssistantError, match="changed"):
        await _save(
            hass, entry, subentry, [loader, _tool()], revision=first["revision"]
        )
    assert subentry.data == committed
    disabled = deepcopy(loader)
    disabled["enabled"] = False
    with pytest.raises(HomeAssistantError, match="load_skill"):
        await _save(
            hass, entry, subentry, [later] if remove_loader else [disabled, later]
        )
    assert subentry.data == committed


@pytest.mark.parametrize("duplicate_loader", [True, False])
async def test_collection_repair_can_retain_explicitly_disabled_selected_loader(
    hass, review_agent, duplicate_loader
):
    entry, subentry = review_agent
    loader = next(
        tool
        for tool in yaml.safe_load(agent_config_defaults()["functions"])
        if tool["spec"]["name"] == "load_skill"
    )
    loader["enabled"] = False
    subentry.data = {
        **subentry.data,
        "functions": yaml.safe_dump(
            [loader, deepcopy(loader)]
            if duplicate_loader
            else [loader, _tool(), _tool()]
        ),
        "function_groups": [],
        "skills": ["selected"],
    }
    assert not function_configuration_repair_state(subentry.data).field_updates_allowed
    await _save(hass, entry, subentry, [loader, _tool()])
    saved = yaml.safe_load(subentry.data["functions"])
    assert saved[0]["enabled"] is False
    assert subentry.data["skills"] == ["selected"]
