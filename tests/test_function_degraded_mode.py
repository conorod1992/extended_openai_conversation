"""Focused coverage for isolated invalid Function Tool degraded mode."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import yaml

from custom_components.extended_openai_conversation_responses import (
    management_function_quarantine as quarantine,
    management_function_repair as repair,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
)


def _mixed_grouped_data() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    defaults = agent_config_defaults()
    tools = yaml.safe_load(defaults[CONF_FUNCTION_TOOLS])
    assert isinstance(tools, list) and tools
    valid = deepcopy(tools[0])
    invalid = deepcopy(valid)
    invalid["spec"]["name"] = "create_recurring_reminder"
    invalid["function"] = {
        "type": "native",
        "name": "reminders.create_recurring",
    }
    valid_name = valid["spec"]["name"]
    return (
        {
            **defaults,
            CONF_FUNCTION_TOOLS: yaml.safe_dump(
                [valid, invalid], sort_keys=False, allow_unicode=True
            ),
            CONF_FUNCTION_GROUPS: [
                {
                    "id": "reminders",
                    "name": "Reminders",
                    "description": "Reminder tools",
                    "functions": [valid_name, "create_recurring_reminder"],
                }
            ],
        },
        valid,
        invalid,
    )


def test_effective_configuration_quarantines_tool_but_retains_group_assignment() -> None:
    """Effective groups omit invalid members without rewriting persisted membership."""
    data, valid, invalid = _mixed_grouped_data()

    safe, invalid_tools, group_issues, persisted_groups, issue = (
        repair.effective_function_configuration(data)
    )

    assert issue is not None
    assert [tool["spec"]["name"] for tool in yaml.safe_load(safe[CONF_FUNCTION_TOOLS])] == [
        valid["spec"]["name"]
    ]
    assert safe[CONF_FUNCTION_GROUPS][0]["functions"] == [valid["spec"]["name"]]
    assert persisted_groups[0]["functions"] == [
        valid["spec"]["name"],
        invalid["spec"]["name"],
    ]
    assert invalid_tools[0]["name"] == invalid["spec"]["name"]
    assert invalid_tools[0]["yaml"]
    assert group_issues == [
        {
            "id": "reminders",
            "name": "Reminders",
            "unavailable_functions": [invalid["spec"]["name"]],
        }
    ]


def test_tolerant_group_validation_ignores_only_quarantined_members(monkeypatch) -> None:
    """Guest Mode and Functions validate the usable group view, not broken members."""
    observed: dict[str, Any] = {}

    def strict(value: Any, function_tools: list[dict[str, Any]]) -> Any:
        observed["value"] = deepcopy(value)
        observed["tools"] = function_tools
        return value

    monkeypatch.setattr(quarantine, "_STRICT_VALIDATE_FUNCTION_GROUPS", strict)
    allow_token = quarantine._ALLOW_QUARANTINED_TOOLS.set(True)
    names_token = quarantine._QUARANTINED_FUNCTION_NAMES.set(
        frozenset({"create_recurring_reminder"})
    )
    try:
        groups = quarantine._management_validate_function_groups(
            [
                {
                    "id": "reminders",
                    "functions": ["valid_tool", "create_recurring_reminder"],
                }
            ],
            [{"spec": {"name": "valid_tool"}}],
        )
    finally:
        quarantine._QUARANTINED_FUNCTION_NAMES.reset(names_token)
        quarantine._ALLOW_QUARANTINED_TOOLS.reset(allow_token)

    assert groups[0]["functions"] == ["valid_tool"]
    assert observed["value"][0]["functions"] == ["valid_tool"]


def test_tolerant_function_persist_keeps_invalid_raw_sibling_and_membership() -> None:
    """Editing valid tools/groups cannot silently delete a quarantined sibling."""
    data, valid, invalid = _mixed_grouped_data()
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        title="Assistant",
        data=deepcopy(data),
    )

    class ConfigEntries:
        @staticmethod
        def async_update_subentry(_entry: Any, target: Any, *, data: dict[str, Any], **_kwargs: Any) -> None:
            target.data = data

    hass = SimpleNamespace(config_entries=ConfigEntries())
    effective_groups = [
        {
            "id": "reminders",
            "name": "Reminders edited",
            "description": "Edited while one member is unavailable",
            "functions": [valid["spec"]["name"]],
        }
    ]

    result = quarantine._tolerant_persist_function_configuration(
        hass,
        entry,
        subentry,
        [valid],
        effective_groups,
    )

    persisted_tools = yaml.safe_load(subentry.data[CONF_FUNCTION_TOOLS])
    assert {tool["spec"]["name"] for tool in persisted_tools} == {
        valid["spec"]["name"],
        invalid["spec"]["name"],
    }
    assert subentry.data[CONF_FUNCTION_GROUPS][0]["name"] == "Reminders edited"
    assert subentry.data[CONF_FUNCTION_GROUPS][0]["functions"] == [
        valid["spec"]["name"],
        invalid["spec"]["name"],
    ]
    assert result["functions"] == [valid]
    assert result["function_groups"] == effective_groups
