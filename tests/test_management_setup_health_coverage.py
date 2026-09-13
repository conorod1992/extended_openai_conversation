"""Focused residual coverage for management setup-health integration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_loading_performance,
    management_setup_health,
    management_ui,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)


def _entry() -> SimpleNamespace:
    return SimpleNamespace(data={}, runtime_data=object())


def _subentry() -> SimpleNamespace:
    return SimpleNamespace(data=agent_config_defaults())


def test_exposed_entity_count_counts_only_assist_exposed_states(monkeypatch) -> None:
    states = [
        SimpleNamespace(entity_id="light.kitchen"),
        SimpleNamespace(entity_id="sensor.temperature"),
        SimpleNamespace(entity_id="switch.fan"),
    ]
    hass = SimpleNamespace(states=SimpleNamespace(async_all=lambda: states))
    should_expose = Mock(side_effect=[True, False, True])
    monkeypatch.setattr(management_setup_health, "async_should_expose", should_expose)

    assert management_setup_health._exposed_entity_count(hass) == 2
    assert [call.args[2] for call in should_expose.call_args_list] == [
        "light.kitchen",
        "sensor.temperature",
        "switch.fan",
    ]


@pytest.mark.asyncio
async def test_install_enriches_overview_with_load_health_and_is_idempotent(
    monkeypatch,
) -> None:
    original_result = {
        "agent": {"knowledge_source_count": "3"},
        "load_errors": [
            {"key": "memories", "message": "memory unavailable"},
            "ignored-non-dict",
        ],
        "overview_marker": "preserved",
    }

    async def original(_hass, _user_id, _is_admin, _message):
        return original_result

    monkeypatch.setattr(management_loading_performance, "async_overview_summary", original)
    monkeypatch.delattr(
        management_loading_performance,
        management_setup_health._PATCHED,
        raising=False,
    )
    entry = _entry()
    subentry = _subentry()
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_args: (entry, subentry))

    captured: dict[str, object] = {}

    def fake_build(
        hass,
        passed_entry,
        passed_subentry,
        *,
        memory_available,
        knowledge_source_count,
        knowledge_available,
        is_admin,
    ):
        captured.update(
            {
                "hass": hass,
                "entry": passed_entry,
                "subentry": passed_subentry,
                "memory_available": memory_available,
                "knowledge_source_count": knowledge_source_count,
                "knowledge_available": knowledge_available,
                "is_admin": is_admin,
            }
        )
        return {"health": "ok"}

    monkeypatch.setattr(management_setup_health, "build_setup_health_facts", fake_build)

    assert management_setup_health.install_management_setup_health() is True
    wrapped = management_loading_performance.async_overview_summary
    assert management_setup_health.install_management_setup_health() is False
    assert management_loading_performance.async_overview_summary is wrapped

    hass = object()
    result = await wrapped(
        hass,
        "user-1",
        True,
        {"entry_id": "entry-1", "subentry_id": "agent-1"},
    )

    assert result == {**original_result, "setup_health": {"health": "ok"}}
    assert captured == {
        "hass": hass,
        "entry": entry,
        "subentry": subentry,
        "memory_available": False,
        "knowledge_source_count": 3,
        "knowledge_available": True,
        "is_admin": True,
    }


@pytest.mark.asyncio
async def test_overview_setup_health_failure_is_additive_and_fail_open(monkeypatch) -> None:
    original_result = {
        "agent": {"knowledge_source_count": 2},
        "load_errors": [],
        "overview_marker": "still-usable",
    }

    async def original(_hass, _user_id, _is_admin, _message):
        return original_result

    monkeypatch.setattr(management_loading_performance, "async_overview_summary", original)
    monkeypatch.delattr(
        management_loading_performance,
        management_setup_health._PATCHED,
        raising=False,
    )
    monkeypatch.setattr(
        management_ui,
        "entry_and_agent",
        Mock(side_effect=RuntimeError("setup-health lookup failed")),
    )

    assert management_setup_health.install_management_setup_health() is True
    result = await management_loading_performance.async_overview_summary(
        object(),
        "user-1",
        False,
        {"entry_id": "entry-1", "subentry_id": "agent-1"},
    )

    assert result["overview_marker"] == "still-usable"
    assert result["agent"] == {"knowledge_source_count": 2}
    assert result["setup_health"] == {
        "unavailable": True,
        "can_manage": False,
        "live_provider_tested": False,
    }


@pytest.mark.asyncio
async def test_overview_setup_health_missing_identifiers_fails_open(monkeypatch) -> None:
    async def original(_hass, _user_id, _is_admin, _message):
        return {"agent": None, "load_errors": []}

    monkeypatch.setattr(management_loading_performance, "async_overview_summary", original)
    monkeypatch.delattr(
        management_loading_performance,
        management_setup_health._PATCHED,
        raising=False,
    )

    assert management_setup_health.install_management_setup_health() is True
    result = await management_loading_performance.async_overview_summary(
        object(), "user-1", True, {}
    )

    assert result["setup_health"] == {
        "unavailable": True,
        "can_manage": True,
        "live_provider_tested": False,
    }
