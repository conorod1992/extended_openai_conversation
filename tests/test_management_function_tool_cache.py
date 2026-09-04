"""Regression coverage for management Function Tool catalogue parsing."""

from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses import management_ui
import custom_components.extended_openai_conversation_responses.management_loading_performance as loading


def test_agent_snapshot_uses_authoritative_cached_function_parser(monkeypatch) -> None:
    options = agent_config_defaults()
    cached_parser = Mock(
        return_value=[
            {
                "enabled": True,
                "spec": {"name": "cached_tool"},
                "function": {"type": "service", "service": "light.turn_on"},
            }
        ]
    )
    monkeypatch.setattr(
        loading,
        "cached_configured_function_tools_from_data",
        cached_parser,
    )
    monkeypatch.setattr(
        management_ui,
        "configured_function_tools_from_data",
        Mock(side_effect=AssertionError("uncached management alias must not be used")),
    )
    hass = SimpleNamespace(data={})
    entry = SimpleNamespace(entry_id="entry-1", title="Provider", data={})
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        title="Jarvis",
        data=options,
    )

    result = loading._agent_snapshot(hass, entry, subentry)

    cached_parser.assert_called_once_with(options)
    assert result["function_count"] == 1
