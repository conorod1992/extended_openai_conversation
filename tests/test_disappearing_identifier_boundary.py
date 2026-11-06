"""Every removal route preserves retained state-control dependencies."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    management_ui as ui,
    transfer,
)
from custom_components.extended_openai_conversation_responses.function_dependency_integrity import (
    async_validate_disappearing_identifiers,
)
from custom_components.extended_openai_conversation_responses.management_function_repair import (
    persisted_config_projection,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_pr15_function_tool_config_references import _native_tool, _setup_entry
from tests.test_request_rules import MemoryStore, RequestRules, local_rule
from tests.test_state_control_rename_boundary import rule


async def manager_for(field, verb, old):
    manager = RequestRules(MemoryStore())
    await manager.async_initialize()
    saved = local_rule()
    saved["name"] = "Keep working"
    saved["action"]["actions"] = rule(field, verb, values=[old])["action"]["actions"]
    await manager.async_create(saved)
    return manager


@pytest.mark.parametrize(
    "field,old", [("functions", "old_tool"), ("function_groups", "group")]
)
@pytest.mark.parametrize("verb", ["enable", "disable"])
@pytest.mark.parametrize("route", ["delete", "update", "import", "import_preview"])
async def test_management_removal_is_refused_before_writing(
    hass, monkeypatch, field, old, verb, route
):
    entry, agent = _setup_entry(hass)
    agent.data["guest_allowed_function_names"] = []
    manager = await manager_for(field, verb, old)
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=manager))
    candidate = deepcopy(agent.data)
    candidate[field] = (
        yaml.safe_dump([_native_tool("survivor")]) if field == "functions" else []
    )
    if field == "functions":
        candidate["function_groups"] = []
    message = {
        "entry_id": entry.entry_id,
        "subentry_id": agent.subentry_id,
        "section": "configuration",
        "action": route,
        "revision": persisted_config_projection(agent).revision,
    }
    if route == "delete":
        message.update(
            section="tools",
            action="delete" if field == "functions" else "delete_group",
            confirm=True,
            name=old,
            group_id=old,
        )
    elif route == "update":
        message["config"] = {
            field: candidate[field],
            "function_groups": candidate["function_groups"],
        }
    else:
        message["document"] = {
            "schema": "extended_openai_conversation.agent",
            "version": 2,
            "title": "Replacement",
            "config": candidate,
        }
    before = deepcopy(agent.data)
    with pytest.raises(HomeAssistantError, match="Keep working"):
        await ui.async_management_command(hass, "admin", True, message)
    assert agent.data == before
    hass.config_entries.async_update_subentry.assert_not_called()


@pytest.mark.parametrize("foreign", [False, True])
async def test_cross_agent_bulk_scan_and_quarantined_siblings(hass, foreign):
    entry, agent = _setup_entry(hass)
    entry.subentries["other"] = SimpleNamespace(
        subentry_id="other", subentry_type="conversation", title="Other"
    )
    document = {
        "rules": [
            rule("functions", "enable", entry="foreign" if foreign else entry.entry_id)
        ]
    }
    manager = SimpleNamespace(async_backup_data=AsyncMock(return_value=document))
    getter = AsyncMock(return_value=manager)
    before = deepcopy(agent.data)
    before["functions"] = yaml.safe_dump(
        [
            _native_tool("old_tool"),
            _native_tool("second"),
            {"spec": {"name": "broken"}, "function": {"type": "unknown"}},
        ]
    )
    after = {
        **before,
        "functions": yaml.safe_dump(
            [{"spec": {"name": "broken"}, "function": {"type": "unknown"}}]
        ),
    }

    async def check():
        await async_validate_disappearing_identifiers(
            hass,
            entry,
            agent,
            before,
            after,
            get_rules=getter,
            retained_document={"rules": []},
        )

    if foreign:
        await check()
    else:
        with pytest.raises(HomeAssistantError, match="Other: Keep working"):
            await check()
    getter.assert_awaited_once()
    manager.async_backup_data.assert_awaited_once()


@pytest.mark.parametrize("replace_rules", [False, True])
async def test_restore_validates_final_rules_before_journal(monkeypatch, replace_rules):
    from tests.test_selective_restore_concurrency import RestoreHarness

    tx = RestoreHarness(monkeypatch)
    old = "old_tool"
    tx.subentry.subentry_type = "conversation"
    tx.entry.subentries = {tx.subentry.subentry_id: tx.subentry}
    tx.hass.config_entries.async_entries = lambda _: [tx.entry]
    tx.subentry.data["functions"] = yaml.safe_dump([_native_tool(old)])
    tx.subentry.data["function_groups"] = []
    manager = await manager_for("functions", "disable", old)
    tx.state.request_rules = await manager.async_backup_data()
    tx.state.request_rules["rules"][0]["action"]["actions"][0]["choose"][0]["sequence"][
        0
    ]["data"]["agent_id"] = tx.subentry.subentry_id
    tx.imported.available_sections = frozenset(
        {transfer.SECTION_CONFIGURATION, transfer.SECTION_REQUEST_RULES}
    )
    tx.imported.config = {
        **tx.subentry.data,
        "functions": yaml.safe_dump([_native_tool("survivor")]),
    }
    tx.imported.request_rules = {**tx.state.request_rules, "rules": []}
    sections = [transfer.SECTION_CONFIGURATION] + (
        [transfer.SECTION_REQUEST_RULES] if replace_rules else []
    )
    if replace_rules:
        await transfer.async_restore_transfer(
            tx.hass, tx.entry, tx.subentry, tx.imported, sections=sections
        )
        assert tx.journal.saved[-1]["phase"] == "committed"
    else:
        with pytest.raises(HomeAssistantError, match="Keep working"):
            await transfer.async_restore_transfer(
                tx.hass, tx.entry, tx.subentry, tx.imported, sections=sections
            )
        assert tx.journal.saved == []
        assert tx.writes == []


@pytest.mark.parametrize("verb", ["enable", "disable"])
async def test_quarantine_delete_obeys_shared_guard(hass, monkeypatch, verb):
    from custom_components.extended_openai_conversation_responses.management_function_repair import (
        async_function_repair,
        repair_revision,
    )
    from tests.test_management_function_repair import _mixed_legacy_tool_data

    data, mixed, _ = _mixed_legacy_tool_data()
    entry, agent = _setup_entry(hass)
    agent.data = data
    agent.data["guest_allowed_function_names"] = []
    old = mixed[1]["spec"]["name"]
    manager = await manager_for("functions", verb, old)
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=manager))
    before = deepcopy(agent.data)
    with pytest.raises(HomeAssistantError, match="Keep working"):
        await async_function_repair(
            hass,
            "admin",
            True,
            {
                "action": "delete_one",
                "entry_id": entry.entry_id,
                "subentry_id": agent.subentry_id,
                "revision": repair_revision(agent),
                "index": 1,
            },
        )
    assert agent.data == before
    hass.config_entries.async_update_subentry.assert_not_called()
