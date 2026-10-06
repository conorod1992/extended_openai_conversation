"""Repair must preserve invalid siblings and reject unrelated invalid edits."""

from collections import OrderedDict
from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_function_repair as repair,
    management_ui as ui,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from tests.test_management_function_repair import (
    _entry_and_subentry,
    _invalid_legacy_tool_data,
    _mixed_legacy_tool_data,
)


@pytest.fixture
def broken_agent(hass, monkeypatch):
    data, _ = _invalid_legacy_tool_data()
    entry, subentry = _entry_and_subentry(data)
    monkeypatch.setattr(ui, "entry_and_agent", lambda *_: (entry, subentry))
    return hass, entry, subentry


async def _command(agent, action, **values):
    hass, entry, subentry = agent
    return await repair.async_function_repair(
        hass,
        "admin",
        True,
        {
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "action": action,
            **values,
        },
    )


@pytest.mark.parametrize("field", [CONF_FUNCTION_TOOLS, CONF_FUNCTION_GROUPS])
async def test_repair_configuration_save_rejects_tool_field_edits(broken_agent, field):
    result = await _command(
        broken_agent, "configuration_save", config={field: "changed by user"}
    )
    assert result["valid"] is False
    assert "Repair invalid Function Tools" in result["errors"][CONF_FUNCTION_TOOLS]
    broken_agent[0].config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize("updates", [{"unknown_option": True}, {"max_tokens": -1}])
async def test_repair_configuration_save_rejects_unrelated_invalid_config(
    broken_agent, updates
):
    result = await _command(broken_agent, "configuration_save", config=updates)
    assert result["valid"] is False
    assert result["errors"]
    broken_agent[0].config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize("title", ["", "  ", 3])
async def test_repair_save_requires_nonempty_text_title(broken_agent, title):
    result = await _command(broken_agent, "configuration_save", config={}, title=title)
    assert result == {"valid": False, "errors": {"title": "must not be empty"}}
    broken_agent[0].config_entries.async_update_subentry.assert_not_called()


async def test_repair_save_one_requires_object_without_persisting(broken_agent):
    before = await _command(broken_agent, "get")
    with pytest.raises(HomeAssistantError, match="tool must be an object"):
        await _command(
            broken_agent,
            "save_one",
            revision=before["revision"],
            index=0,
            tool="not a tool",
        )
    broken_agent[0].config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize("current", ["malformed", {"spec": {"name": 7}}])
async def test_repair_deletes_nameless_tool_without_rewriting_valid_group_references(
    hass, monkeypatch, current
):
    _, _, sibling = _mixed_legacy_tool_data()
    data = {
        CONF_FUNCTION_TOOLS: [sibling, current],
        CONF_FUNCTION_GROUPS: [
            {
                "id": "retained",
                "name": "Retained",
                "description": "Retained sibling",
                "functions": [sibling["spec"]["name"]],
                "loading_mode": "on_demand",
                "enabled": True,
            }
        ],
    }
    entry, subentry = _entry_and_subentry(data)
    monkeypatch.setattr(ui, "entry_and_agent", lambda *_: (entry, subentry))

    def update(_hass, _entry, target, *, data):
        target.data = data

    monkeypatch.setattr(repair, "update_live_subentry", update)
    agent = (hass, entry, subentry)
    before = await _command(agent, "get")
    await _command(agent, "delete_one", revision=before["revision"], index=1)
    assert repair.editable_function_tools(subentry.data) == [sibling]
    assert subentry.data[CONF_FUNCTION_GROUPS] == data[CONF_FUNCTION_GROUPS]


async def test_prewarm_preserves_malformed_yaml_for_repair(hass, monkeypatch):
    entry, subentry = _entry_and_subentry({CONF_FUNCTION_TOOLS: "[unterminated"})
    entry.state = ConfigEntryState.LOADED
    await repair.async_prewarm_persisted_config_projection(hass, entry, subentry)
    projection = repair.persisted_config_projection(subentry)
    assert projection.repair_state is not None
    assert projection.repair_state.issue
    assert subentry.data[CONF_FUNCTION_TOOLS] == "[unterminated"
    hass.async_add_executor_job.reset_mock()
    await repair.async_prewarm_persisted_config_projection(hass, entry, subentry)
    hass.async_add_executor_job.assert_not_awaited()


async def test_prewarm_does_not_hide_non_function_configuration_error(hass):
    entry, subentry = _entry_and_subentry({CONF_FUNCTION_TOOLS: [], "max_tokens": -1})
    entry.state = ConfigEntryState.LOADED
    with pytest.raises(AgentConfigError):
        await repair.async_prewarm_persisted_config_projection(hass, entry, subentry)
    assert repair.persisted_config_projection(subentry).repair_state is None


def test_seeded_projection_eviction_preserves_revision_lineage(monkeypatch):
    monkeypatch.setattr(repair, "_persisted_projections", OrderedDict())
    monkeypatch.setattr(repair, "_revision_lineages", OrderedDict())
    monkeypatch.setattr(repair, "_PROJECTION_CACHE_LIMIT", 1)
    first_entry, first = _entry_and_subentry(agent_config_defaults())

    class Agent(SimpleNamespace):
        pass

    first = Agent(**vars(first))
    first_entry.subentries[first.subentry_id] = first
    second_entry, second = _entry_and_subentry(agent_config_defaults())
    snapshot = deepcopy(repair._cached_repair_defaults())
    repair.seed_persisted_config_projection(first_entry, first, snapshot, "first")
    repair.seed_persisted_config_projection(second_entry, second, snapshot, "second")
    assert id(first) not in repair._persisted_projections
    assert repair.persisted_config_projection(first).revision == "first"
    assert len(repair._persisted_projections) == 1


def test_safe_repair_payload_rejects_healthy_projection(hass):
    entry, subentry = _entry_and_subentry({CONF_FUNCTION_TOOLS: []})
    projection = repair.persisted_config_projection(subentry)
    with pytest.raises(HomeAssistantError, match="do not require repair"):
        repair.safe_configuration_payload(hass, entry, subentry, projection=projection)


def test_safe_repair_payload_without_projection_preserves_quarantine(hass):
    data, _ = _invalid_legacy_tool_data()
    entry, subentry = _entry_and_subentry(data)
    result = repair.safe_configuration_payload(hass, entry, subentry)
    assert result["function_repair"]["invalid_count"] > 0
    assert subentry.data == data
