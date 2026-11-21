"""Management selection snapshots and canonical transfer preserve HA references."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
    configured_function_tools_from_data,
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    new_reference_tool,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_ha_llm_tools import Echo, TestAPI, reference, register


def setup_agent(hass, monkeypatch):
    subentry = SimpleNamespace(
        subentry_id="agent",
        title="Agent",
        subentry_type="conversation",
        data=normalize_agent_config({"functions": []}),
    )
    entry = SimpleNamespace(entry_id="entry", subentries={"agent": subentry})
    hass.config_entries.async_get_entry.return_value = entry
    hass.config.language = "en"
    monkeypatch.setattr(
        management_ui, "entry_and_agent", lambda *args: (entry, subentry)
    )

    async def persist(hass, entry, subentry, tools, groups, *, expected_revision=None):
        subentry.data = normalize_agent_config(
            {**subentry.data, "functions": tools, "function_groups": groups}
        )
        return {
            "functions": agent_config_snapshot(subentry.data)["functions"],
            "function_groups": groups,
        }

    monkeypatch.setattr(management_ui, "_persist_function_configuration", persist)
    return entry, subentry


async def command(hass, action, admin=True, **kwargs):
    return await management_ui.async_management_command(
        hass,
        "admin",
        admin,
        {
            "entry_id": "entry",
            "subentry_id": "agent",
            "section": "tools",
            "action": action,
            **kwargs,
        },
    )


async def test_add_all_is_explicit_snapshot_idempotent_and_preserves_groups(
    hass, monkeypatch
):
    _, subentry = setup_agent(hass, monkeypatch)
    api = register(hass, TestAPI(hass))
    catalog = await command(hass, "ha_catalog")
    selected = [item["reference"] for item in catalog["tools"]]
    future = Echo()
    future.name = "future"
    api.tools.append(future)
    added = await command(hass, "ha_add", tools=selected)
    assert len(added["functions"]) == 1
    assert added["functions"][0]["function"]["tool_name"] == "echo"
    saved_name = added["functions"][0]["spec"]["name"]
    assert added["ha_saved"][saved_name]["available"] is True
    assert added["ha_saved"][saved_name]["name"] == "echo"
    assert added["ha_saved"][saved_name]["description"] == catalog["tools"][0]["description"]
    again = await command(hass, "ha_add", tools=selected)
    assert again == added
    refreshed = await command(hass, "ha_catalog")
    assert {item["name"]: item["already_added"] for item in refreshed["tools"]} == {
        "echo": True,
        "future": False,
    }
    assert configured_function_tools_from_data(subentry.data) == added["functions"]


async def test_nonadmin_cannot_discover_or_add(hass, monkeypatch):
    setup_agent(hass, monkeypatch)
    for action in ("ha_catalog", "ha_add"):
        with pytest.raises(HomeAssistantError, match="Administrator"):
            await command(hass, action, admin=False, tools=[])


async def test_adding_second_ha_tool_only_updates_checked_references(hass, monkeypatch):
    setup_agent(hass, monkeypatch)
    first, second = Echo(), Echo()
    second.name = "second"
    register(hass, TestAPI(hass, tools=[first, second]))
    catalog = await command(hass, "ha_catalog")
    refs = {item["name"]: item["reference"] for item in catalog["tools"]}

    added_first = await command(hass, "ha_add", tools=[refs[first.name]])
    added_second = await command(hass, "ha_add", tools=[refs[second.name]])
    assert len(added_second["functions"]) == 2
    assert [item["name"] for item in added_second["ha_saved"].values()] == ["second"]
    assert {
        **added_first["ha_saved"],
        **added_second["ha_saved"],
    } == (await command(hass, "ha_catalog"))["saved"]


async def test_satellite_timer_can_be_discovered_and_added(hass, monkeypatch):
    from homeassistant.components import llm as llm_component
    from homeassistant.components.intent import llm as intent_tools, timers
    from homeassistant.helpers import entity_registry, intent

    setup_agent(hass, monkeypatch)
    register(hass, llm_component.AssistAPI(hass))
    hass.data[llm_component.DATA_PLATFORMS] = SimpleNamespace(
        async_get_platforms=AsyncMock(return_value={"intent": intent_tools})
    )
    hass.data[timers.TIMER_DATA] = timers.TimerManager(hass)
    timers.async_register_timer_handler(hass, "device-1", Mock())
    intent.async_register(hass, timers.StartTimerIntentHandler())
    satellite = SimpleNamespace(entity_id="assist_satellite.kitchen", name="Kitchen")
    hass.states.async_all.side_effect = lambda domain=None: [satellite] if domain else []
    monkeypatch.setattr(
        entity_registry,
        "async_get",
        lambda _: SimpleNamespace(
            async_get=lambda _: SimpleNamespace(device_id="device-1", disabled_by=None)
        ),
    )

    baseline = await command(hass, "ha_catalog")
    assert baseline["preview_devices"] == [{"device_id": "device-1", "name": "Kitchen"}]
    assert not any("HassStartTimer" in item["name"] for item in baseline["tools"])
    device = {"preview_device_id": "device-1"}
    catalog = await command(hass, "ha_catalog", **device)
    timer = next(item for item in catalog["tools"] if "HassStartTimer" in item["name"])
    added = await command(hass, "ha_add", tools=[timer["reference"]], **device)
    assert added["functions"][0]["function"] == timer["reference"]
    with pytest.raises(HomeAssistantError, match="satellite is unavailable"):
        await command(hass, "ha_catalog", preview_device_id="missing")


async def test_unavailable_selection_does_not_mutate_and_preview_retains_reference(
    hass, monkeypatch
):
    _, subentry = setup_agent(hass, monkeypatch)
    api = register(hass, TestAPI(hass))
    selected = [
        item["reference"] for item in (await command(hass, "ha_catalog"))["tools"]
    ]
    await command(hass, "ha_add", tools=selected)
    before = deepcopy(subentry.data)
    api.fail = True
    with pytest.raises(HomeAssistantError, match="no longer available"):
        await command(hass, "ha_add", tools=selected)
    assert subentry.data == before
    unavailable = await command(hass, "ha_catalog")
    assert all(not item["available"] for item in unavailable["saved"].values())
    api.fail = False
    available = await command(hass, "ha_catalog")
    assert all(item["available"] for item in available["saved"].values())


def test_existing_export_import_keeps_unavailable_references():
    saved = new_reference_tool(reference(source="not_installed"), set())
    subentry = SimpleNamespace(
        title="Agent", data=normalize_agent_config({"functions": [saved]})
    )
    exported = management_ui._export_agent(subentry, reviewed=True)
    imported = management_ui._parse_import_document(exported)
    assert configured_function_tools_from_data(imported["config"]) == [saved]


def test_existing_full_backup_validation_keeps_unavailable_references():
    from custom_components.extended_openai_conversation_responses.backup import (
        inspect_backup,
    )
    from tests.test_backup import _document

    saved = new_reference_tool(reference(source="not_installed"), set())
    document = _document()
    document["agent"]["config"]["functions"] = [saved]
    document["agent"]["config"]["function_groups"] = []
    prepared = inspect_backup(document, "destination")
    assert configured_function_tools_from_data(prepared.config) == [saved]
