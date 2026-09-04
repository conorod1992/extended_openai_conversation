"""Tests for the cheap Overview setup-health snapshot."""

from types import SimpleNamespace

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
    build_setup_health_snapshot,
)


def _entry(*, runtime_loaded: bool = True):
    return SimpleNamespace(
        data={},
        runtime_data=object() if runtime_loaded else None,
    )


def _subentry(config=None):
    return SimpleNamespace(data=config or agent_config_defaults())


def _by_id(snapshot):
    return {check["id"]: check for check in snapshot["checks"]}


def test_default_setup_is_ready_without_treating_optional_features_as_errors(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_setup_health.get_exposed_entities",
        lambda _hass: [{"entity_id": "light.kitchen"}],
    )

    snapshot = build_setup_health_snapshot(
        object(),
        _entry(),
        _subentry(),
        knowledge_source_count=0,
        knowledge_available=True,
        is_admin=True,
    )
    checks = _by_id(snapshot)

    assert snapshot["state"] == "ready"
    assert snapshot["summary"] == "Ready"
    assert snapshot["live_provider_tested"] is False
    assert checks["provider_runtime"]["state"] == "ready"
    assert checks["instructions"]["value"] == "Starter instructions"
    assert checks["home_assistant_exposure"]["state"] == "ready"
    assert checks["memory"]["state"] == "neutral"
    assert checks["memory"]["value"] == "Off by choice"
    assert checks["knowledge"]["state"] == "neutral"
    assert checks["web_search"]["state"] == "neutral"


def test_real_configuration_problems_are_warnings(monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_setup_health.get_exposed_entities",
        lambda _hass: [],
    )
    config = agent_config_defaults()
    config[CONF_PROMPT] = ""
    config[CONF_KNOWLEDGE_ENABLED] = True
    config[CONF_WEB_SEARCH] = True
    config[CONF_API_MODE] = "auto"

    snapshot = build_setup_health_snapshot(
        object(),
        _entry(),
        _subentry(config),
        knowledge_source_count=0,
        knowledge_available=True,
        is_admin=True,
    )
    checks = _by_id(snapshot)

    assert snapshot["state"] == "warning"
    assert snapshot["warning_count"] == 4
    assert checks["instructions"]["state"] == "warning"
    assert checks["home_assistant_exposure"]["state"] == "warning"
    assert checks["knowledge"]["value"] == "Enabled, no sources"
    assert checks["web_search"]["state"] == "warning"
    assert checks["web_search"]["action"]["target"] == "config-api_mode"


def test_unavailable_runtime_is_an_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_setup_health.get_exposed_entities",
        lambda _hass: [{"entity_id": "switch.test"}],
    )
    snapshot = build_setup_health_snapshot(
        object(),
        _entry(runtime_loaded=False),
        _subentry(),
        knowledge_source_count=0,
        knowledge_available=True,
        is_admin=False,
    )

    assert snapshot["state"] == "error"
    assert snapshot["error_count"] == 1
    assert snapshot["can_manage"] is False
    assert _by_id(snapshot)["provider_runtime"]["state"] == "error"


def test_failed_knowledge_count_is_unknown_not_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_setup_health.get_exposed_entities",
        lambda _hass: [{"entity_id": "light.kitchen"}],
    )
    config = agent_config_defaults()
    config[CONF_KNOWLEDGE_ENABLED] = True

    snapshot = build_setup_health_snapshot(
        object(),
        _entry(),
        _subentry(config),
        knowledge_source_count=0,
        knowledge_available=False,
        is_admin=True,
    )
    knowledge = _by_id(snapshot)["knowledge"]

    assert knowledge["state"] == "unknown"
    assert knowledge["value"] == "Unable to determine"
    assert snapshot["unknown_count"] == 1
    assert snapshot["state"] == "warning"
