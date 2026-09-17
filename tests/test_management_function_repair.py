"""Focused edge-case coverage for persisted Function Tool recovery."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.management_function_repair import (
    _export_safe_subentry,
    async_function_repair,
    function_tools_issue,
    isolated_function_tools,
)


class _FakeConfigEntries:
    def __init__(self) -> None:
        self.updates = 0

    def async_update_subentry(
        self, _entry: Any, subentry: Any, *, data: dict[str, Any], **_kwargs: Any
    ) -> None:
        subentry.data = data
        self.updates += 1


def _invalid_legacy_tool_data() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    defaults = agent_config_defaults()
    tools = yaml.safe_load(defaults[CONF_FUNCTION_TOOLS])
    assert isinstance(tools, list) and tools
    parameters = tools[0]["spec"].setdefault(
        "parameters", {"type": "object", "properties": {}}
    )
    parameters["description"] = 123
    return (
        {
            CONF_FUNCTION_TOOLS: yaml.safe_dump(
                tools, sort_keys=False, allow_unicode=True
            ),
            CONF_FUNCTION_GROUPS: deepcopy(defaults[CONF_FUNCTION_GROUPS]),
        },
        tools,
    )


def _mixed_legacy_tool_data() -> tuple[
    dict[str, Any], list[dict[str, Any]], dict[str, Any]
]:
    defaults = agent_config_defaults()
    tools = yaml.safe_load(defaults[CONF_FUNCTION_TOOLS])
    assert isinstance(tools, list) and tools
    valid_tool = deepcopy(tools[0])
    broken_tool = deepcopy(tools[0])
    broken_tool["spec"]["name"] = f"{broken_tool['spec']['name']}_broken"
    broken_tool["spec"].setdefault(
        "parameters", {"type": "object", "properties": {}}
    )["description"] = 123
    mixed = [valid_tool, broken_tool]
    return (
        {
            CONF_FUNCTION_TOOLS: yaml.safe_dump(
                mixed, sort_keys=False, allow_unicode=True
            ),
            CONF_FUNCTION_GROUPS: deepcopy(defaults[CONF_FUNCTION_GROUPS]),
        },
        mixed,
        valid_tool,
    )


def _entry_and_subentry(data: dict[str, Any]) -> tuple[Any, Any]:
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Broken agent",
        data=data,
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        title="Extended OpenAI",
        data={},
        subentries={subentry.subentry_id: subentry},
    )
    return entry, subentry


def test_function_tools_issue_isolates_malformed_yaml() -> None:
    """Malformed persisted YAML must not collapse the agent catalogue."""
    configured, issue = function_tools_issue({CONF_FUNCTION_TOOLS: "[unterminated"})

    assert configured == []
    assert issue is not None


def test_isolated_function_tools_keeps_valid_siblings() -> None:
    """Per-tool repair isolates a bad tool without presenting valid siblings as broken."""
    data, _mixed, valid_tool = _mixed_legacy_tool_data()

    valid, invalid, issue = isolated_function_tools(data)

    assert issue is not None
    assert len(valid) == 1
    assert valid[0]["spec"]["name"] == valid_tool["spec"]["name"]
    assert len(invalid) == 1
    assert invalid[0]["index"] == 1
    assert invalid[0]["name"].endswith("_broken")
    assert "description" in invalid[0]["validation_error"]


def test_function_tools_issue_returns_valid_subset_when_one_tool_is_bad() -> None:
    """A persisted bad tool is excluded while independently valid tools remain usable."""
    data, _mixed, valid_tool = _mixed_legacy_tool_data()

    configured, issue = function_tools_issue(data)

    assert issue is not None
    assert [tool["spec"]["name"] for tool in configured] == [
        valid_tool["spec"]["name"]
    ]


@pytest.mark.asyncio
async def test_function_repair_get_returns_only_invalid_tool_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repair metadata identifies only bad tools while retaining the raw collection."""
    data, mixed, _valid_tool = _mixed_legacy_tool_data()
    entry, subentry = _entry_and_subentry(data)
    hass = SimpleNamespace(data={}, config_entries=_FakeConfigEntries())
    monkeypatch.setattr(
        management_ui,
        "entry_and_agent",
        lambda *_args, **_kwargs: (entry, subentry),
    )

    repair = await async_function_repair(
        hass,
        "admin",
        True,
        {
            "action": "get",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
        },
    )

    assert repair["tools"] == mixed
    assert len(repair["invalid_tools"]) == 1
    assert repair["invalid_tools"][0]["index"] == 1
    assert repair["invalid_tools"][0]["tool"] == mixed[1]


@pytest.mark.asyncio
async def test_function_repair_rejects_stale_raw_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrelated concurrent config change invalidates a repair write."""
    data, tools = _invalid_legacy_tool_data()
    entry, subentry = _entry_and_subentry(data)
    config_entries = _FakeConfigEntries()
    hass = SimpleNamespace(data={}, config_entries=config_entries)
    monkeypatch.setattr(
        management_ui,
        "entry_and_agent",
        lambda *_args, **_kwargs: (entry, subentry),
    )

    repair = await async_function_repair(
        hass,
        "admin",
        True,
        {
            "action": "get",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
        },
    )
    subentry.data = {**subentry.data, "concurrent_change": True}
    repaired_tools = deepcopy(tools)
    repaired_tools[0]["spec"]["parameters"].pop("description")

    with pytest.raises(HomeAssistantError, match="changed in another tab"):
        await async_function_repair(
            hass,
            "admin",
            True,
            {
                "action": "save",
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                "revision": repair["revision"],
                "tools": repaired_tools,
            },
        )

    assert config_entries.updates == 0


@pytest.mark.asyncio
async def test_function_repair_requires_admin() -> None:
    """Persisted agent repair remains an administrator-only operation."""
    data, _tools = _invalid_legacy_tool_data()
    entry, subentry = _entry_and_subentry(data)
    hass = SimpleNamespace(data={}, config_entries=_FakeConfigEntries())

    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await async_function_repair(
            hass,
            "user-1",
            False,
            {
                "action": "get",
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
            },
        )


def test_management_websocket_schema_accepts_function_tool_index() -> None:
    """The repair command index must survive WebSocket schema validation."""
    schema = management_ui.websocket_management._ws_schema

    validated = schema(
        {
            "id": 1,
            "type": management_ui.WS_COMMAND,
            "section": "function_repair",
            "action": "delete_one",
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "index": 36,
        }
    )

    assert validated["index"] == 36


@pytest.mark.asyncio
async def test_function_repair_delete_one_removes_invalid_tool_and_group_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting a quarantined tool uses its raw index and cleans group membership."""
    data, mixed, valid_tool = _mixed_legacy_tool_data()
    valid_name = valid_tool["spec"]["name"]
    broken_name = mixed[1]["spec"]["name"]
    data[CONF_FUNCTION_GROUPS] = [
        {
            "id": "repair_tools",
            "name": "Repair tools",
            "description": "Tools used by the repair regression test",
            "loading_mode": "always",
            "functions": [valid_name, broken_name],
            "guest_allowed": False,
            "enabled": True,
        }
    ]
    entry, subentry = _entry_and_subentry(data)
    config_entries = _FakeConfigEntries()
    hass = SimpleNamespace(data={}, config_entries=config_entries)
    monkeypatch.setattr(
        management_ui,
        "entry_and_agent",
        lambda *_args, **_kwargs: (entry, subentry),
    )

    repair = await async_function_repair(
        hass,
        "admin",
        True,
        {
            "action": "get",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
        },
    )
    result = await async_function_repair(
        hass,
        "admin",
        True,
        {
            "action": "delete_one",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "revision": repair["revision"],
            "index": 1,
        },
    )

    persisted_tools = yaml.safe_load(subentry.data[CONF_FUNCTION_TOOLS])
    assert [tool["spec"]["name"] for tool in persisted_tools] == [valid_name]
    assert subentry.data[CONF_FUNCTION_GROUPS][0]["functions"] == [valid_name]
    assert result["function_repair"]["invalid_count"] == 0
    assert config_entries.updates == 1


def test_export_safe_subentry_omits_only_invalid_tools_and_warns() -> None:
    """Exports preserve valid siblings while quarantining invalid Function Tools."""
    data, mixed, valid_tool = _mixed_legacy_tool_data()
    valid_name = valid_tool["spec"]["name"]
    broken_name = mixed[1]["spec"]["name"]
    data[CONF_FUNCTION_GROUPS] = [
        {
            "id": "export_tools",
            "name": "Export tools",
            "description": "Tools used by the export regression test",
            "loading_mode": "always",
            "functions": [valid_name, broken_name],
            "guest_allowed": False,
            "enabled": True,
        }
    ]
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Broken agent",
        data=data,
    )

    safe_subentry, warnings = _export_safe_subentry(subentry)

    exported_tools = yaml.safe_load(safe_subentry.data[CONF_FUNCTION_TOOLS])
    assert [tool["spec"]["name"] for tool in exported_tools] == [valid_name]
    assert safe_subentry.data[CONF_FUNCTION_GROUPS][0]["functions"] == [valid_name]
    assert warnings == [
        {
            "code": "invalid_function_tools_omitted",
            "message": warnings[0]["message"],
            "count": 1,
            "names": [broken_name],
        }
    ]
    assert broken_name in warnings[0]["message"]
    assert "omitted" in warnings[0]["message"]
