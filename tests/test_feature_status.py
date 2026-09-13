"""Tests for management-facing effective feature status."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    feature_status as feature_status_module,
    knowledge,
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


def test_install_is_idempotent_when_already_installed(monkeypatch) -> None:
    """Repeated setup must not stack additional management wrappers."""
    original_snapshot = management_loading_performance._agent_snapshot
    original_command = management_ui.async_management_command
    monkeypatch.setattr(feature_status_module, "_INSTALLED", True)

    feature_status_module.install_management_feature_status()

    assert management_loading_performance._agent_snapshot is original_snapshot
    assert management_ui.async_management_command is original_command


def test_installed_snapshot_enriches_from_loaded_knowledge(monkeypatch) -> None:
    """Overview snapshots expose effective status even when cheap counts are stale."""

    def base_snapshot(_hass, _entry, _subentry, *args, **kwargs):
        return {"knowledge_source_count": 0, "marker": "base"}

    monkeypatch.setattr(feature_status_module, "_INSTALLED", False)
    monkeypatch.setattr(management_loading_performance, "_agent_snapshot", base_snapshot)
    monkeypatch.setattr(
        knowledge,
        "get_loaded_knowledge",
        lambda _hass, _entry_id, _subentry_id: SimpleNamespace(source_count=3),
    )

    feature_status_module.install_management_feature_status()

    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        data={
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_KNOWLEDGE_ENABLED: True,
        },
    )
    result = management_loading_performance._agent_snapshot(object(), entry, subentry)

    assert result["marker"] == "base"
    assert result["knowledge_source_count"] == 3
    assert result["feature_status"]["memory"]["label"] == "Manual"
    assert result["feature_status"]["knowledge"]["state"] == "available"
    assert result["feature_status"]["knowledge"]["source_count"] == 3
    assert feature_status_module._FRONTEND_MODULE in management_ui.MANAGEMENT_FRONTEND_MODULES


@pytest.mark.asyncio
async def test_installed_command_leaves_unrelated_actions_unchanged(monkeypatch) -> None:
    """Only Memory and Knowledge list responses should be enriched."""
    expected = {"ok": True, "payload": "unchanged"}

    async def base_command(_hass, _user_id, _is_admin, _message):
        return expected

    monkeypatch.setattr(feature_status_module, "_INSTALLED", False)
    monkeypatch.setattr(management_ui, "async_management_command", base_command)

    feature_status_module.install_management_feature_status()

    result = await management_ui.async_management_command(
        object(),
        "user-1",
        True,
        {"section": "knowledge", "action": "get"},
    )

    assert result is expected
    assert "feature_status" not in result


@pytest.mark.asyncio
async def test_installed_memory_list_adds_memory_feature_status(monkeypatch) -> None:
    """Memory list responses expose the current effective Memory policy."""

    async def base_command(_hass, _user_id, _is_admin, _message):
        return {"memories": []}

    subentry = SimpleNamespace(
        data={
            CONF_MEMORY_MODE: MEMORY_MODE_AUTOMATIC,
            CONF_MEMORY_AUTO_RETRIEVE_LIMIT: 2,
            CONF_SHARED_MEMORY_MODE: SHARED_MEMORY_DISABLED,
        }
    )
    monkeypatch.setattr(feature_status_module, "_INSTALLED", False)
    monkeypatch.setattr(management_ui, "async_management_command", base_command)
    monkeypatch.setattr(
        management_ui,
        "entry_and_agent",
        lambda _hass, _entry_id, _subentry_id: (object(), subentry),
    )

    feature_status_module.install_management_feature_status()

    result = await management_ui.async_management_command(
        object(),
        "user-1",
        True,
        {
            "section": "memories",
            "action": "list",
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
        },
    )

    assert result["memories"] == []
    assert result["feature_status"]["state"] == "enabled"
    assert result["feature_status"]["label"] == "Automatic"
    assert result["feature_status"]["automatic_inclusion_limit"] == 2
    assert result["feature_status"]["shared_memory_enabled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stats", "expected_count"),
    [
        ({"source_count": "4"}, 4),
        ({"source_count": "invalid"}, 2),
    ],
)
async def test_installed_knowledge_list_uses_safe_source_count(
    monkeypatch, stats, expected_count
) -> None:
    """Knowledge status prefers valid stats and safely falls back to listed sources."""

    async def base_command(_hass, _user_id, _is_admin, _message):
        return {"sources": [{"id": "a"}, {"id": "b"}], "stats": stats}

    subentry = SimpleNamespace(data={CONF_KNOWLEDGE_ENABLED: True})
    monkeypatch.setattr(feature_status_module, "_INSTALLED", False)
    monkeypatch.setattr(management_ui, "async_management_command", base_command)
    monkeypatch.setattr(
        management_ui,
        "entry_and_agent",
        lambda _hass, _entry_id, _subentry_id: (object(), subentry),
    )

    feature_status_module.install_management_feature_status()

    result = await management_ui.async_management_command(
        object(),
        "user-1",
        True,
        {
            "section": "knowledge",
            "action": "list",
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
        },
    )

    assert result["feature_status"]["state"] == "available"
    assert result["feature_status"]["source_count"] == expected_count
    assert result["feature_status"]["available"] is True
