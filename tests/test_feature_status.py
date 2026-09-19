"""Tests for management-facing effective feature status."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_loading_performance,
    management_ui,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_MEMORY_MODE,
    CONF_SHARED_MEMORY_MODE,
    DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT,
    MEMORY_MODE_AUTOMATIC,
    MEMORY_MODE_MANUAL,
    MEMORY_MODE_OFF,
    SHARED_MEMORY_DISABLED,
    SHARED_MEMORY_EXPLICIT,
)
from custom_components.extended_openai_conversation_responses.feature_status import (
    management_feature_status,
)


def test_disabled_features_explain_retained_data() -> None:
    status = management_feature_status(
        {
            CONF_MEMORY_MODE: MEMORY_MODE_OFF,
            CONF_KNOWLEDGE_ENABLED: False,
        },
        knowledge_source_count=3,
    )

    assert status["memory"]["state"] == "disabled"
    assert "retained" in status["memory"]["detail"]
    assert status["knowledge"]["state"] == "disabled"
    assert status["knowledge"]["source_count"] == 3
    assert "retained" in status["knowledge"]["summary"]


def test_manual_memory_reports_tool_only_and_shared_scope_limits() -> None:
    status = management_feature_status(
        {
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_MEMORY_AUTO_RETRIEVE_LIMIT: 0,
            CONF_SHARED_MEMORY_MODE: SHARED_MEMORY_DISABLED,
        },
        knowledge_source_count=0,
    )["memory"]

    assert status["state"] == "enabled"
    assert status["label"] == "Manual"
    assert status["automatic_inclusion_enabled"] is False
    assert status["shared_memory_enabled"] is False
    assert "search tools remain available" in status["detail"]
    assert "shared household scope cannot use persistent memory" in status["detail"]


def test_automatic_memory_reports_inclusion_and_household_availability() -> None:
    status = management_feature_status(
        {
            CONF_MEMORY_MODE: MEMORY_MODE_AUTOMATIC,
            CONF_MEMORY_AUTO_RETRIEVE_LIMIT: 4,
            CONF_SHARED_MEMORY_MODE: SHARED_MEMORY_EXPLICIT,
        },
        knowledge_source_count=0,
    )["memory"]

    assert status["label"] == "Automatic"
    assert status["automatic_inclusion_limit"] == 4
    assert status["shared_memory_enabled"] is True
    assert "up to 4 relevant memories" in status["summary"]


def test_enabled_empty_knowledge_is_not_reported_available() -> None:
    status = management_feature_status(
        {CONF_KNOWLEDGE_ENABLED: True}, knowledge_source_count=0
    )["knowledge"]

    assert status["state"] == "empty"
    assert status["label"] == "Needs sources"
    assert status["available"] is False


def test_populated_knowledge_is_reported_available() -> None:
    status = management_feature_status(
        {CONF_KNOWLEDGE_ENABLED: True}, knowledge_source_count=2
    )["knowledge"]

    assert status["state"] == "available"
    assert status["available"] is True
    assert status["source_count"] == 2


def test_invalid_auto_retrieve_limit_falls_back_to_default() -> None:
    """Malformed persisted limits should not break management status rendering."""
    status = management_feature_status(
        {
            CONF_MEMORY_MODE: MEMORY_MODE_AUTOMATIC,
            CONF_MEMORY_AUTO_RETRIEVE_LIMIT: object(),
        },
        knowledge_source_count=0,
    )["memory"]

    assert status["automatic_inclusion_limit"] == DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT
    assert status["automatic_inclusion_enabled"] is (
        DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT > 0
    )


def test_snapshot_enriches_from_loaded_knowledge(hass, management_agent, monkeypatch):
    entry, subentry = management_agent
    subentry.data.update(
        {CONF_MEMORY_MODE: MEMORY_MODE_MANUAL, CONF_KNOWLEDGE_ENABLED: True}
    )
    monkeypatch.setattr(
        management_loading_performance,
        "get_loaded_knowledge",
        lambda *_: SimpleNamespace(source_count=3),
    )
    result = management_loading_performance._agent_snapshot(hass, entry, subentry)
    assert result["knowledge_source_count"] == 3
    assert result["feature_status"]["memory"]["label"] == "Manual"
    assert result["feature_status"]["knowledge"]["state"] == "available"
    assert result["feature_status"]["knowledge"]["source_count"] == 3


async def test_knowledge_get_does_not_add_list_feature_status(
    hass, management_message, monkeypatch
):
    source = {"id": "a", "title": "A"}
    library = SimpleNamespace(async_get=AsyncMock(return_value=source))
    monkeypatch.setattr(
        management_ui, "async_get_knowledge", AsyncMock(return_value=library)
    )
    monkeypatch.setattr(management_ui, "knowledge_source_as_dict", lambda value: value)
    result = await management_ui.async_management_command(
        hass, "admin", True, management_message("knowledge", "get", source_id="a")
    )
    assert result == {"source": source}
    library.async_get.assert_awaited_once_with("a")


@pytest.mark.parametrize(
    ("stats", "expected_count"),
    [({"source_count": "4"}, 4), ({"source_count": "invalid"}, 2)],
)
async def test_knowledge_list_uses_safe_source_count(
    hass, management_agent, management_message, monkeypatch, stats, expected_count
):
    management_agent[1].data[CONF_KNOWLEDGE_ENABLED] = True
    library = SimpleNamespace(
        async_list=AsyncMock(return_value=[{"id": "a"}, {"id": "b"}]),
        stats=lambda: stats,
    )
    monkeypatch.setattr(
        management_ui, "async_get_knowledge", AsyncMock(return_value=library)
    )
    monkeypatch.setattr(management_ui, "knowledge_source_as_dict", lambda value: value)
    result = await management_ui.async_management_command(
        hass, "admin", True, management_message("knowledge", "list")
    )
    assert result["feature_status"]["state"] == "available"
    assert result["feature_status"]["source_count"] == expected_count
    assert result["feature_status"]["available"] is True
