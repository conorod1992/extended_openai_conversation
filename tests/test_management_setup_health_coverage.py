"""Focused residual coverage for management setup-health integration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_setup_health,
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

    entry, subentry = _entry(), _subentry()

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

    hass = object()
    result = management_setup_health.add_setup_health(
        hass, entry, subentry, original_result, is_admin=True
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


def test_overview_setup_health_failure_is_additive_and_keeps_provider_facts(
    monkeypatch,
):
    original = {
        "agent": {"knowledge_source_count": 2},
        "load_errors": [],
        "overview_marker": "still-usable",
    }
    monkeypatch.setattr(
        management_setup_health,
        "build_setup_health_facts",
        Mock(side_effect=RuntimeError("registry unavailable")),
    )
    result = management_setup_health.add_setup_health(
        object(), _entry(), _subentry(), original, is_admin=False
    )
    assert result["overview_marker"] == "still-usable"
    assert result["agent"] == {"knowledge_source_count": 2}
    assert result["setup_health"]["unavailable"] is True
    assert result["setup_health"]["can_manage"] is False
    assert result["setup_health"]["live_provider_tested"] is False
    assert result["setup_health"]["provider_runtime"]["client_loaded"] is True
    assert "setup_health" not in original


def test_overview_setup_health_handles_malformed_agent_snapshot():
    result = management_setup_health.add_setup_health(
        object(),
        _entry(),
        _subentry(),
        {"agent": None, "load_errors": []},
        is_admin=True,
    )
    assert result["setup_health"]["unavailable"] is True
    assert result["setup_health"]["can_manage"] is True
    assert result["setup_health"]["live_provider_tested"] is False
