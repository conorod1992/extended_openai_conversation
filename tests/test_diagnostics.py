"""Tests for non-sensitive integration diagnostics."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.extended_openai_conversation_responses.const import (
    DEFAULT_CONF_FUNCTION_TOOLS,
    SUBSYSTEM_STATUS_KEY,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.diagnostics import (
    _configured_function_tools,
)


async def test_missing_function_config_uses_execution_default(hass) -> None:
    """Diagnostics must report the tools execution actually falls back to."""
    assert _configured_function_tools({}) == DEFAULT_CONF_FUNCTION_TOOLS


def test_explicit_empty_function_config_remains_empty() -> None:
    """An explicit empty YAML list must not be replaced by defaults."""
    assert _configured_function_tools({"functions": "[]"}) == []


def test_optional_subsystem_runtime_status_distinguishes_all_states(hass) -> None:
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    entity.entry = SimpleNamespace(entry_id="entry")
    entity.subentry = SimpleNamespace(subentry_id="agent")

    entity._set_subsystem_status("temporary_memory", False)
    entity._set_subsystem_status("knowledge", True, OSError("unreadable"))
    entity._set_subsystem_status("persistent_memory", True, healthy=True)

    statuses = hass.data[SUBSYSTEM_STATUS_KEY][("entry", "agent")]
    assert statuses["temporary_memory"] == {
        "configured": False,
        "status": "disabled",
    }
    assert statuses["knowledge"] == {
        "configured": True,
        "status": "failed",
        "error_type": "OSError",
    }
    assert statuses["persistent_memory"] == {
        "configured": True,
        "status": "healthy",
    }


async def test_archive_initialization_failure_degrades_and_reports_status(
    hass, caplog
) -> None:
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    entity.entry = SimpleNamespace(entry_id="entry")
    entity.subentry = SimpleNamespace(subentry_id="agent", data={})

    with patch(
        "custom_components.extended_openai_conversation_responses.conversation.async_get_archive",
        AsyncMock(side_effect=OSError("unreadable")),
    ):
        await entity._async_initialize_archive(True)

    assert entity._archive is None
    assert hass.data[SUBSYSTEM_STATUS_KEY][("entry", "agent")]["archive"] == {
        "configured": True,
        "status": "failed",
        "error_type": "OSError",
    }
    assert "archive features are unavailable" in caplog.text
