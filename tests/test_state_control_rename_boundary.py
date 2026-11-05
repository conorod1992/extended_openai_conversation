"""State-control consumers must never become stale after a supported rename."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.function_dependency_integrity import (
    async_require_unreferenced_state_control_rename,
    state_control_references,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_pr15_function_tool_config_references import _native_tool, _setup_entry


def rule(field, verb, *, agent="agent-1", entry="entry-1", values=None):
    suffix = "tools" if field == "functions" else "groups"
    return {
        "id": "consumer",
        "name": "Keep working",
        "action": {
            "actions": [
                {
                    "choose": [
                        {
                            "conditions": [],
                            "sequence": [
                                {
                                    "service": f"{DOMAIN}.{verb}_function_{suffix}",
                                    "data": {
                                        "config_entry": entry,
                                        "agent_id": agent,
                                        field: values or ["old_tool"],
                                    },
                                }
                            ],
                        }
                    ]
                }
            ]
        },
    }


@pytest.mark.parametrize("field", ["functions", "function_groups"])
@pytest.mark.parametrize("verb", ["enable", "disable"])
@pytest.mark.parametrize(
    "target,expected",
    [
        ("agent-1", True),
        ("other-agent", False),
        ("conversation.owner", True),
        ("{{ owner }}", True),
    ],
)
def test_nested_state_consumers_respect_target_ownership(field, verb, target, expected):
    source = rule(field, verb, agent=target)
    original = deepcopy(source)

    def resolve(reference):
        return ("entry-1", "agent-1") if reference == "conversation.owner" else None

    assert (
        bool(
            state_control_references(
                [source], field, "old_tool", "entry-1", "agent-1", resolve
            )
        )
        is expected
    )
    assert source == original
    source["action"]["actions"][0]["choose"][0]["sequence"][0]["data_template"] = {
        "config_entry": "foreign-entry"
    }
    assert (
        state_control_references(
            [source], field, "old_tool", "entry-1", "agent-1", resolve
        )
        == []
    )


@pytest.mark.parametrize(
    "field,old", [("functions", "old_tool"), ("function_groups", "group")]
)
@pytest.mark.parametrize("verb", ["enable", "disable"])
async def test_management_refuses_dependent_rename_without_any_write(
    hass, monkeypatch, field, old, verb
):
    entry, subentry = _setup_entry(hass)
    document = {"rules": [rule(field, verb, values=[old])]}
    manager = SimpleNamespace(
        async_backup_data=AsyncMock(return_value=document),
        revision=lambda: "rules-version",
        function_references=lambda name: [],
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.async_get_request_rules",
        AsyncMock(return_value=manager),
    )
    original = deepcopy(subentry.data)
    message = {
        "section": "tools",
        "action": "save",
        "entry_id": entry.entry_id,
        "subentry_id": subentry.subentry_id,
        "original_name": old,
        "tool": _native_tool("new_tool"),
    }
    if field == "function_groups":
        message = {
            **message,
            "action": "save_group",
            "original_id": old,
            "group": {**original["function_groups"][0], "id": "new-group"},
        }
    with pytest.raises(HomeAssistantError, match="Keep working"):
        await async_management_command(hass, "admin", True, message)
    hass.config_entries.async_update_subentry.assert_not_called()
    assert subentry.data == original
    assert document["rules"][0]["action"]["actions"][0]["choose"][0]["sequence"][0][
        "data"
    ][field] == [old]


def test_winning_template_payload_and_dynamic_lists_are_reviewed():
    source = rule("functions", "disable")
    action = source["action"]["actions"][0]["choose"][0]["sequence"][0]
    action["data_template"] = {"functions": ["different"]}
    assert (
        state_control_references(
            [source], "functions", "old_tool", "entry-1", "agent-1", lambda _: None
        )
        == []
    )
    for value in ("{{ selected }}", ["{selected}"], {"unexpected": "{{ value }}"}):
        action["data_template"] = {"functions": value}
        assert state_control_references(
            [source], "functions", "old_tool", "entry-1", "agent-1", lambda _: None
        ) == ["Keep working"]


async def test_cross_agent_consumers_are_checked_and_storage_failure_propagates(hass):
    entry, target = _setup_entry(hass)
    source = SimpleNamespace(
        subentry_id="source", subentry_type="conversation", title="Other assistant"
    )
    entry.subentries[source.subentry_id] = source
    getter = AsyncMock(
        side_effect=[
            SimpleNamespace(async_backup_data=AsyncMock(return_value={"rules": []})),
            SimpleNamespace(
                async_backup_data=AsyncMock(
                    return_value={"rules": [rule("functions", "disable")]}
                )
            ),
        ]
    )
    with pytest.raises(HomeAssistantError, match="Other assistant: Keep working"):
        await async_require_unreferenced_state_control_rename(
            hass, entry, target, "functions", "old_tool", "new_tool", get_rules=getter
        )
    getter = AsyncMock(side_effect=HomeAssistantError("Storage unavailable"))
    with pytest.raises(HomeAssistantError, match="Storage unavailable"):
        await async_require_unreferenced_state_control_rename(
            hass, entry, target, "functions", "old_tool", "new_tool", get_rules=getter
        )
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_existing_rule_owner_is_reused_for_rename_validation(hass):
    entry, target = _setup_entry(hass)
    manager = SimpleNamespace(async_backup_data=AsyncMock(return_value={"rules": []}))
    getter = AsyncMock(
        side_effect=AssertionError("Rule owner was unnecessarily reloaded")
    )
    await async_require_unreferenced_state_control_rename(
        hass,
        entry,
        target,
        "functions",
        "old_tool",
        "new_tool",
        get_rules=getter,
        rules_manager=manager,
    )
    getter.assert_not_called()
    manager.async_backup_data.assert_awaited_once()
