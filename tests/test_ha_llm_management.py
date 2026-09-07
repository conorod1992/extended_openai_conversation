"""Management selection snapshots and canonical transfer preserve HA references."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_ha_llm_tools import Echo, TestAPI, reference, register

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

    def persist(hass, entry, subentry, tools, groups):
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
    exported = management_ui._export_agent(subentry)
    imported = management_ui._parse_import_document(exported)
    assert configured_function_tools_from_data(imported["config"]) == [saved]


def test_existing_full_backup_validation_keeps_unavailable_references():
    from test_backup import _document

    from custom_components.extended_openai_conversation_responses.backup import (
        inspect_backup,
    )

    saved = new_reference_tool(reference(source="not_installed"), set())
    document = _document()
    document["agent"]["config"]["functions"] = [saved]
    document["agent"]["config"]["function_groups"] = []
    prepared = inspect_backup(document, "destination")
    assert configured_function_tools_from_data(prepared.config) == [saved]
