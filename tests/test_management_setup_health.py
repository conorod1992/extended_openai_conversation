"""Tests for the cheap Overview setup-health facts."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import management_setup_health
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_MODE,
    CONF_KNOWLEDGE_ENABLED,
    CONF_PROMPT,
    CONF_WEB_SEARCH,
)
from custom_components.extended_openai_conversation_responses.management_setup_health import (
    build_setup_health_facts,
)


def _entry(*, runtime_loaded: bool = True):
    return SimpleNamespace(
        data={},
        runtime_data=object() if runtime_loaded else None,
    )


def _subentry(config=None):
    return SimpleNamespace(data=config or agent_config_defaults())


def _facts(config=None, *, runtime_loaded=True, **kwargs):
    return build_setup_health_facts(
        object(),
        _entry(runtime_loaded=runtime_loaded),
        _subentry(config),
        memory_available=kwargs.get("memory_available", True),
        knowledge_source_count=kwargs.get("knowledge_source_count", 0),
        knowledge_available=kwargs.get("knowledge_available", True),
        is_admin=kwargs.get("is_admin", True),
    )


def test_default_facts_are_side_effect_free_and_descriptive(monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_setup_health._exposed_entity_count",
        lambda _hass: 1,
    )

    facts = _facts()

    assert facts["provider_runtime"]["client_loaded"] is True
    assert facts["provider_runtime"]["configured_api_mode"] == "auto"
    assert facts["prompt_state"] == "starter"
    assert facts["exposed_entity_count"] == 1
    assert facts["memory"] == {"mode": "off", "available": True}
    assert facts["knowledge"] == {
        "enabled": False,
        "source_count": 0,
        "available": True,
    }
    assert facts["web_search"]["enabled"] is False
    assert facts["can_manage"] is True
    assert facts["live_provider_tested"] is False


def test_prompt_classification_distinguishes_empty_and_custom(monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_setup_health._exposed_entity_count",
        lambda _hass: 0,
    )
    empty = agent_config_defaults()
    empty[CONF_PROMPT] = ""
    custom = agent_config_defaults()
    custom[CONF_PROMPT] = "You are the kitchen assistant."

    assert _facts(empty)["prompt_state"] == "empty"
    assert _facts(custom)["prompt_state"] == "custom"


def test_web_search_facts_reuse_runtime_compatibility(monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_setup_health._exposed_entity_count",
        lambda _hass: 2,
    )
    config = agent_config_defaults()
    config[CONF_WEB_SEARCH] = True
    config[CONF_API_MODE] = "auto"

    facts = _facts(config)

    assert facts["web_search"]["enabled"] is True
    assert facts["web_search"]["effective_api_mode"] == "responses"
    assert facts["web_search"]["available"] is True
    assert facts["web_search"]["reason"] is None


def test_unavailable_counts_remain_unknown_facts(monkeypatch) -> None:
    def fail(_hass):
        raise RuntimeError("exposure unavailable")

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_setup_health._exposed_entity_count",
        fail,
    )
    config = agent_config_defaults()
    config[CONF_KNOWLEDGE_ENABLED] = True

    facts = _facts(
        config,
        runtime_loaded=False,
        memory_available=False,
        knowledge_available=False,
        is_admin=False,
    )

    assert facts["provider_runtime"]["client_loaded"] is False
    assert facts["exposed_entity_count"] is None
    assert facts["memory"]["available"] is False
    assert facts["knowledge"]["enabled"] is True
    assert facts["knowledge"]["available"] is False
    assert facts["can_manage"] is False


def test_overview_health_frontend_helpers_are_registered() -> None:
    frontend = (
        Path(__file__).parents[1]
        / "custom_components"
        / "extended_openai_conversation_responses"
        / "frontend"
    )
    assert (frontend / "overview-health.js").is_file()
    assert (frontend / "overview-onboarding.js").is_file()


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
        function_tools_health=None,
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
