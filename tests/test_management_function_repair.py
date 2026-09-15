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
    async_function_repair,
    function_tools_issue,
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


def test_function_tools_issue_isolates_malformed_yaml() -> None:
    """Malformed persisted YAML must not collapse the agent catalogue."""
    configured, issue = function_tools_issue({CONF_FUNCTION_TOOLS: "[unterminated"})

    assert configured == []
    assert issue is not None


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
    repaired_tools[0]["spec"]["parameters"].pop("enumNames")

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
