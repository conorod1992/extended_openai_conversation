"""Regression coverage for management Function Tool catalogue parsing."""

from types import SimpleNamespace
from unittest.mock import Mock

import custom_components.extended_openai_conversation_responses.management_function_repair as repair
import custom_components.extended_openai_conversation_responses.management_loading_performance as loading
from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)


def test_agent_snapshot_uses_authoritative_cached_function_metadata(monkeypatch) -> None:
    options = agent_config_defaults()
    monkeypatch.setattr(repair, "_health_cache", repair.OrderedDict())
    cached_metadata = Mock(
        return_value={"usable_count": 1, "enabled_count": 1, "total_count": 1}
    )
    monkeypatch.setattr(
        repair,
        "configured_function_tool_metadata_from_data",
        cached_metadata,
    )
    monkeypatch.setattr(
        repair,
        "configured_function_tools_from_data",
        Mock(side_effect=AssertionError("runtime Function Tool copies must not be used")),
    )
    monkeypatch.setattr(
        management_ui,
        "configured_function_tools_from_data",
        Mock(side_effect=AssertionError("management quarantine alias must not be used")),
    )
    hass = SimpleNamespace(data={})
    entry = SimpleNamespace(entry_id="entry-1", title="Provider", data={})
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        title="Jarvis",
        data=options,
    )

    cold = loading._agent_snapshot(hass, entry, subentry)
    assert cold["function_count"] is None
    cached_metadata.assert_not_called()

    repair.management_function_tool_health(options)
    result = loading._agent_snapshot(hass, entry, subentry)

    cached_metadata.assert_called_once_with(options)
    assert result["function_count"] == 1
