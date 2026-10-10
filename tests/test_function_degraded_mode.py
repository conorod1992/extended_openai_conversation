"""Focused coverage for isolated invalid Function Tool degraded mode."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import yaml
import pytest

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


@pytest.fixture(autouse=True)
def ha_schema_context(hass):
    """HA template validation requires a real instance even in pure seam tests."""
    return hass


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


def test_effective_configuration_quarantines_tool_but_retains_group_assignment() -> (
    None
):
    """Effective groups omit invalid members without rewriting persisted membership."""
    data, valid, invalid = _mixed_grouped_data()

    safe, invalid_tools, group_issues, persisted_groups, issue = (
        repair.effective_function_configuration(data)
    )

    assert issue is not None
    assert [
        tool["spec"]["name"] for tool in yaml.safe_load(safe[CONF_FUNCTION_TOOLS])
    ] == [valid["spec"]["name"]]
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


def test_usable_tools_records_names_for_invalid_siblings() -> None:
    data, valid, invalid = _mixed_grouped_data()
    token = quarantine._QUARANTINED_FUNCTION_NAMES.set(frozenset())
    try:
        usable = quarantine._usable_function_tools(data)
        assert [tool["spec"]["name"] for tool in usable] == [valid["spec"]["name"]]
        assert quarantine._QUARANTINED_FUNCTION_NAMES.get() == frozenset(
            {invalid["spec"]["name"]}
        )
    finally:
        quarantine._QUARANTINED_FUNCTION_NAMES.reset(token)


def test_tolerant_group_validation_ignores_only_quarantined_members(
    monkeypatch,
) -> None:
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


def test_group_quarantine_keeps_non_list_input_for_strict_validation(
    monkeypatch,
) -> None:
    observed: list[object] = []
    monkeypatch.setattr(
        quarantine,
        "_STRICT_VALIDATE_FUNCTION_GROUPS",
        lambda value, _tools: observed.append(value) or value,
    )
    allow_token = quarantine._ALLOW_QUARANTINED_TOOLS.set(True)
    names_token = quarantine._QUARANTINED_FUNCTION_NAMES.set(frozenset({"broken"}))
    try:
        value = {"unexpected": "shape"}
        assert quarantine._management_validate_function_groups(value, []) == value
    finally:
        quarantine._QUARANTINED_FUNCTION_NAMES.reset(names_token)
        quarantine._ALLOW_QUARANTINED_TOOLS.reset(allow_token)

    assert observed == [{"unexpected": "shape"}]


def test_management_merge_uses_strict_path_for_valid_functions(monkeypatch) -> None:
    data = agent_config_defaults()
    delegated: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(
        quarantine,
        "_STRICT_MERGE_AGENT_CONFIG",
        lambda source, updates: (
            delegated.append((source, updates)) or {**source, **updates}
        ),
    )
    allow_token = quarantine._ALLOW_QUARANTINED_TOOLS.set(True)
    try:
        merged = quarantine._management_merge_agent_config(
            data, {"guest_mode_enabled": True}
        )
    finally:
        quarantine._ALLOW_QUARANTINED_TOOLS.reset(allow_token)

    assert merged["guest_mode_enabled"] is True
    assert delegated == [(data, {"guest_mode_enabled": True})]


def test_management_merge_keeps_missing_group_key_absent(monkeypatch) -> None:
    data, _valid, _invalid = _mixed_grouped_data()
    original_tools = data[CONF_FUNCTION_TOOLS]
    data.pop(CONF_FUNCTION_GROUPS)
    monkeypatch.setattr(
        quarantine,
        "_safe_function_configuration",
        lambda raw: dict(raw),
    )
    monkeypatch.setattr(
        quarantine,
        "_STRICT_MERGE_AGENT_CONFIG",
        lambda _source, _updates: {
            CONF_FUNCTION_TOOLS: "safe normalized tools",
            CONF_FUNCTION_GROUPS: [{"id": "normalized"}],
        },
    )
    allow_token = quarantine._ALLOW_QUARANTINED_TOOLS.set(True)
    try:
        merged = quarantine._management_merge_agent_config(
            data, {"guest_mode_enabled": True}
        )
    finally:
        quarantine._ALLOW_QUARANTINED_TOOLS.reset(allow_token)

    assert merged[CONF_FUNCTION_TOOLS] == original_tools
    assert CONF_FUNCTION_GROUPS not in merged


def test_unchanged_merge_does_not_add_an_omitted_group_key(monkeypatch) -> None:
    raw_tools = "raw invalid tool configuration"
    candidate = {
        CONF_FUNCTION_TOOLS: [{"spec": {"name": "safe"}}],
        CONF_FUNCTION_GROUPS: [{"id": "generated"}],
        "guest_mode_enabled": True,
    }
    monkeypatch.setattr(
        quarantine,
        "persisted_config_projection",
        lambda _subentry: SimpleNamespace(
            snapshot={CONF_FUNCTION_TOOLS: [], CONF_FUNCTION_GROUPS: []}
        ),
    )
    monkeypatch.setattr(
        quarantine,
        "_STRICT_MERGE_AGENT_CONFIG",
        lambda *_args, **_kwargs: deepcopy(candidate),
    )
    subentry = SimpleNamespace(data={CONF_FUNCTION_TOOLS: raw_tools})

    persisted, normalized = quarantine.merge_unchanged_function_configuration(
        subentry, {"guest_mode_enabled": True}
    )

    assert normalized == candidate
    assert persisted[CONF_FUNCTION_TOOLS] == raw_tools
    assert CONF_FUNCTION_GROUPS not in persisted


def test_group_restoration_skips_malformed_normalized_siblings() -> None:
    malformed = [None, {"id": "reminders", "functions": ["safe"]}]

    restored = quarantine._restore_quarantined_group_members(
        malformed,
        [{"id": "reminders", "functions": ["safe", "broken"]}],
        frozenset({"broken"}),
    )

    assert restored == [
        None,
        {"id": "reminders", "functions": ["safe", "broken"]},
    ]


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
        def async_update_subentry(
            _entry: Any, target: Any, *, data: dict[str, Any], **_kwargs: Any
        ) -> None:
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
