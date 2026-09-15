"""Regression coverage for invalid persisted Function Tool management recovery."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
yaml = pytest.importorskip("yaml")

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
    validate_function_tools,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.management_function_repair import (
    async_function_repair,
    function_tools_issue,
    install_management_function_repair,
)
from custom_components.extended_openai_conversation_responses.management_loading_performance import (
    async_agent_catalog,
)


class _FakeConfigEntries:
    def __init__(self, entry: Any) -> None:
        self.entry = entry
        self.updates: list[dict[str, Any]] = []

    def async_entries(self, domain: str) -> list[Any]:
        assert domain == DOMAIN
        return [self.entry]

    def async_update_subentry(
        self, entry: Any, subentry: Any, *, data: dict[str, Any], **kwargs: Any
    ) -> None:
        assert entry is self.entry
        subentry.data = data
        if "title" in kwargs:
            subentry.title = kwargs["title"]
        self.updates.append(deepcopy(data))


class _FakeHass:
    def __init__(self, entry: Any) -> None:
        self.data: dict[str, Any] = {}
        self.config_entries = _FakeConfigEntries(entry)


def _invalid_legacy_tool_data() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    defaults = agent_config_defaults()
    tools = yaml.safe_load(defaults[CONF_FUNCTION_TOOLS])
    assert isinstance(tools, list) and tools
    parameters = tools[0]["spec"].setdefault(
        "parameters", {"type": "object", "properties": {}}
    )
    parameters["enumNames"] = ["Legacy display label"]
    return (
        {
            CONF_FUNCTION_TOOLS: yaml.safe_dump(
                tools, sort_keys=False, allow_unicode=True
            ),
            CONF_FUNCTION_GROUPS: deepcopy(defaults[CONF_FUNCTION_GROUPS]),
        },
        tools,
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


def test_function_tools_issue_isolates_legacy_schema_keyword() -> None:
    """A newly unsupported persisted schema keyword becomes repair metadata."""
    data, _tools = _invalid_legacy_tool_data()

    configured, issue = function_tools_issue(data)

    assert configured == []
    assert issue is not None
    assert "enumNames" in issue


def test_function_tools_issue_isolates_malformed_yaml() -> None:
    """Malformed persisted YAML cannot collapse the agent catalogue either."""
    configured, issue = function_tools_issue({CONF_FUNCTION_TOOLS: "[unterminated"})

    assert configured == []
    assert issue is not None


@pytest.mark.asyncio
async def test_agent_catalog_keeps_invalid_function_tool_agent_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One invalid Function Tool must not make an otherwise existing agent vanish."""
    data, _tools = _invalid_legacy_tool_data()
    entry, _subentry = _entry_and_subentry(data)
    hass = _FakeHass(entry)
    install_management_function_repair()

    async def _scope_catalog(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(management_ui, "_scope_catalog", _scope_catalog)

    result = await async_agent_catalog(hass, "user-1", True)

    assert len(result["agents"]) == 1
    agent = result["agents"][0]
    assert agent["subentry_id"] == "agent-1"
    assert agent["function_count"] == 0
    assert agent["configuration_issue"]["field"] == CONF_FUNCTION_TOOLS
    assert agent["configuration_issue"]["repairable"] is True
    assert "enumNames" in agent["configuration_issue"]["message"]


@pytest.mark.asyncio
async def test_function_repair_get_and_save_bypass_strict_broken_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repair reads raw state and updates only Function Tools after validation."""
    data, tools = _invalid_legacy_tool_data()
    data["preserve_me"] = {"nested": True}
    entry, subentry = _entry_and_subentry(data)
    hass = _FakeHass(entry)
    install_management_function_repair()
    monkeypatch.setattr(
        management_ui,
        "entry_and_agent",
        lambda *_args, **_kwargs: (entry, subentry),
    )

    repair = await async_function_repair(
        hass,
        "user-1",
        True,
        {
            "section": "function_repair",
            "action": "get",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
        },
    )

    assert isinstance(repair["revision"], str)
    assert "enumNames" in repair["validation_error"]
    assert repair["tools"][0]["spec"]["parameters"]["enumNames"] == [
        "Legacy display label"
    ]

    repaired_tools = deepcopy(tools)
    repaired_tools[0]["spec"]["parameters"].pop("enumNames")
    saved = await async_function_repair(
        hass,
        "user-1",
        True,
        {
            "section": "function_repair",
            "action": "save",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "revision": repair["revision"],
            "tools": repaired_tools,
        },
    )

    assert saved["valid"] is True
    assert len(hass.config_entries.updates) == 1
    assert subentry.data["preserve_me"] == {"nested": True}
    validate_function_tools(yaml.safe_load(subentry.data[CONF_FUNCTION_TOOLS]))
    assert "configuration_issue" not in saved["agent"]


@pytest.mark.asyncio
async def test_function_repair_rejects_stale_raw_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent unrelated config change invalidates the repair write revision."""
    data, tools = _invalid_legacy_tool_data()
    entry, subentry = _entry_and_subentry(data)
    hass = _FakeHass(entry)
    monkeypatch.setattr(
        management_ui,
        "entry_and_agent",
        lambda *_args, **_kwargs: (entry, subentry),
    )

    repair = await async_function_repair(
        hass,
        "user-1",
        True,
        {
            "action": "get",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
        },
    )
    subentry.data = {**subentry.data, "concurrent_change": True}
    repaired_tools = deepcopy(tools)
    repaired_tools[0]["spec"]["parameters"].pop("enumNames")

    with pytest.raises(HomeAssistantError, match="changed in another tab"):
        await async_function_repair(
            hass,
            "user-1",
            True,
            {
                "action": "save",
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                "revision": repair["revision"],
                "tools": repaired_tools,
            },
        )

    assert hass.config_entries.updates == []


@pytest.mark.asyncio
async def test_function_repair_requires_admin() -> None:
    """Repairing persisted global agent configuration remains admin-only."""
    data, _tools = _invalid_legacy_tool_data()
    entry, subentry = _entry_and_subentry(data)
    hass = _FakeHass(entry)

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
