"""Coverage for independent Management overview projections."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_loading_performance as loading,
    management_setup_health,
)


def _entry_and_subentry():
    entry = SimpleNamespace(
        entry_id="entry",
        data={},
        runtime_data=None,
    )
    subentry = SimpleNamespace(
        subentry_id="agent",
        title="Assistant",
        data={},
    )
    return entry, subentry


@pytest.mark.asyncio
async def test_overview_usage_detail_hides_latest_for_non_admin(
    hass, monkeypatch
) -> None:
    entry, subentry = _entry_and_subentry()
    manager = object()
    monkeypatch.setattr(loading, "async_get_usage", AsyncMock(return_value=manager))
    monkeypatch.setattr(
        loading,
        "usage_summary",
        lambda value: {
            "today": {"total_tokens": 42},
            "latest": {"request_id": "private"},
        },
    )

    result = await loading.async_overview_detail(
        hass,
        entry,
        subentry,
        is_admin=False,
        kind="usage",
    )

    assert result["agent"]["tokens_today"] == 42
    assert result["usage"]["latest"] is None


@pytest.mark.asyncio
async def test_overview_usage_detail_keeps_latest_for_admin(
    hass, monkeypatch
) -> None:
    entry, subentry = _entry_and_subentry()
    monkeypatch.setattr(loading, "async_get_usage", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        loading,
        "usage_summary",
        lambda _value: {
            "today": {},
            "latest": {"request_id": "visible"},
        },
    )

    result = await loading.async_overview_detail(
        hass,
        entry,
        subentry,
        is_admin=True,
        kind="usage",
    )

    assert result["agent"]["tokens_today"] == 0
    assert result["usage"]["latest"] == {"request_id": "visible"}


@pytest.mark.asyncio
async def test_overview_memory_detail_projects_count_and_health(
    hass, monkeypatch
) -> None:
    entry, subentry = _entry_and_subentry()
    monkeypatch.setattr(
        loading,
        "async_get_memory",
        AsyncMock(return_value=SimpleNamespace(memory_count=7)),
    )

    result = await loading.async_overview_detail(
        hass,
        entry,
        subentry,
        is_admin=True,
        kind="memory",
    )

    assert result["agent"]["memory_count"] == 7
    assert result["setup_health"]["memory"] == {
        "available": True,
        "loading": False,
    }


@pytest.mark.asyncio
async def test_overview_knowledge_detail_projects_feature_status(
    hass, monkeypatch
) -> None:
    entry, subentry = _entry_and_subentry()
    monkeypatch.setattr(
        loading,
        "async_get_knowledge",
        AsyncMock(return_value=SimpleNamespace(source_count=3)),
    )
    feature = Mock(return_value={"knowledge": "ready"})
    monkeypatch.setattr(loading, "management_feature_status", feature)

    result = await loading.async_overview_detail(
        hass,
        entry,
        subentry,
        is_admin=True,
        kind="knowledge",
    )

    assert result["agent"]["knowledge_source_count"] == 3
    assert result["agent"]["feature_status"] == {"knowledge": "ready"}
    assert result["setup_health"]["knowledge"]["source_count"] == 3
    feature.assert_called_once_with({}, knowledge_source_count=3)


@pytest.mark.asyncio
async def test_overview_guest_mode_detail_uses_loaded_status(
    hass, monkeypatch
) -> None:
    entry, subentry = _entry_and_subentry()
    guest = SimpleNamespace(status=Mock(return_value={"active": True}))
    monkeypatch.setattr(
        loading,
        "async_get_guest_mode",
        AsyncMock(return_value=guest),
    )

    result = await loading.async_overview_detail(
        hass,
        entry,
        subentry,
        is_admin=True,
        kind="guest_mode",
    )

    assert result["agent"]["guest_mode"] == {"active": True}
    guest.status.assert_called_once()


@pytest.mark.asyncio
async def test_overview_setup_health_detail_projects_repair_issue_and_exposure(
    hass, monkeypatch
) -> None:
    entry, subentry = _entry_and_subentry()
    monkeypatch.setattr(
        loading,
        "management_function_tool_health",
        lambda _options: {
            "enabled_count": 2,
            "validation_error": "broken Function Tool",
        },
    )
    monkeypatch.setattr(
        management_setup_health,
        "_exposed_entity_count",
        lambda _hass: 9,
    )

    result = await loading.async_overview_detail(
        hass,
        entry,
        subentry,
        is_admin=True,
        kind="setup_health",
    )

    assert result["agent"]["function_count"] == 2
    assert result["agent"]["configuration_issue"] == {
        "field": loading.CONF_FUNCTION_TOOLS,
        "message": "broken Function Tool",
        "repairable": True,
    }
    assert result["setup_health"]["exposed_entity_count"] == 9
    assert result["setup_health"]["exposed_entity_count_loading"] is False


@pytest.mark.asyncio
async def test_overview_setup_health_tolerates_exposure_projection_failure(
    hass, monkeypatch
) -> None:
    entry, subentry = _entry_and_subentry()
    monkeypatch.setattr(
        loading,
        "management_function_tool_health",
        lambda _options: {"enabled_count": 0},
    )

    def fail(_hass):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(management_setup_health, "_exposed_entity_count", fail)

    result = await loading.async_overview_detail(
        hass,
        entry,
        subentry,
        is_admin=True,
        kind="setup_health",
    )

    assert result["setup_health"]["exposed_entity_count"] is None
    assert "configuration_issue" not in result["agent"]


@pytest.mark.asyncio
async def test_overview_primary_falls_back_when_health_projection_fails(
    hass, monkeypatch
) -> None:
    entry, subentry = _entry_and_subentry()
    monkeypatch.setattr(
        loading,
        "peek_function_tool_health",
        lambda _options: None,
    )
    monkeypatch.setattr(
        loading,
        "_agent_snapshot",
        lambda *_args, **_kwargs: {
            "knowledge_source_count": 0,
        },
    )
    monkeypatch.setattr(
        loading,
        "settings_snapshot",
        lambda _options: {"timeout": 30},
    )
    monkeypatch.setattr(
        loading,
        "build_setup_health_facts",
        Mock(side_effect=RuntimeError("health unavailable")),
    )

    result = await loading.async_overview_primary(
        hass,
        entry,
        subentry,
        is_admin=True,
    )

    health = result["setup_health"]
    assert health["unavailable"] is True
    assert health["function_tools"] == {"loading": True}
    assert health["provider_runtime"]["client_loaded"] is False
    assert health["can_manage"] is True
