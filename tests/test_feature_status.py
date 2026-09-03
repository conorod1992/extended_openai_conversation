"""Tests for management-facing effective feature status."""

from custom_components.extended_openai_conversation_responses.const import (
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_MEMORY_MODE,
    CONF_SHARED_MEMORY_MODE,
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
