"""Tests for the cheap Overview setup-health facts."""

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
    build_setup_health_facts,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    MANAGEMENT_FRONTEND_MODULES,
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
    assert facts["web_search"]["effective_api_mode"] == "chat_completions"
    assert facts["web_search"]["available"] is False
    assert facts["web_search"]["reason"] == "requires_responses"


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
    assert "overview-health.js" in MANAGEMENT_FRONTEND_MODULES
    assert "overview-onboarding.js" in MANAGEMENT_FRONTEND_MODULES
