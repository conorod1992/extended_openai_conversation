"""Tests for side-effect-free management configuration guidance."""

from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_MODE,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
)
from custom_components.extended_openai_conversation_responses.management_configuration_guidance import (
    configuration_guidance_snapshot,
    wrap_management_configuration_guidance,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    MANAGEMENT_FRONTEND_MODULES,
)


def test_web_search_guidance_reuses_runtime_compatibility_rules() -> None:
    config = agent_config_defaults()
    config[CONF_CHAT_MODEL] = "gpt-5-mini"
    config[CONF_API_MODE] = "auto"

    chat = configuration_guidance_snapshot({}, config)
    assert chat["effective_api_mode"] == "chat_completions"
    assert chat["web_search"]["available"] is False
    assert chat["web_search"]["reason"] == "requires_responses"
    assert "Responses API" in chat["web_search"]["message"]

    config[CONF_API_MODE] = "responses"
    direct = configuration_guidance_snapshot({}, config)
    assert direct["effective_api_mode"] == "responses"
    assert direct["web_search"]["available"] is True
    assert direct["web_search"]["reason"] is None
    assert direct["web_search"]["message"] is None

    custom_endpoint = configuration_guidance_snapshot(
        {CONF_BASE_URL: "https://provider.example/v1"}, config
    )
    assert custom_endpoint["web_search"]["available"] is False
    assert custom_endpoint["web_search"]["reason"] == "direct_openai_only"
    assert "direct OpenAI Responses API" in custom_endpoint["web_search"]["message"]


def test_configuration_clarity_and_guidance_frontend_modules_are_registered() -> None:
    assert "management-configuration-clarity.js" in MANAGEMENT_FRONTEND_MODULES
    assert "management-configuration-guidance.js" in MANAGEMENT_FRONTEND_MODULES


async def test_management_wrapper_adds_guidance_to_successful_config(
    monkeypatch,
) -> None:
    config = agent_config_defaults()
    config[CONF_API_MODE] = "responses"
    entry = SimpleNamespace(data={})
    subentry = SimpleNamespace()
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_configuration_guidance.management_ui.entry_and_agent",
        lambda _hass, _entry_id, _subentry_id: (entry, subentry),
    )

    async def original(_hass, _user_id, _is_admin, _message):
        return {"valid": True, "config": config}

    wrapped = wrap_management_configuration_guidance(original)
    result = await wrapped(
        object(),
        "admin",
        True,
        {
            "section": "configuration",
            "action": "validate",
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
        },
    )
    assert result["configuration_guidance"]["web_search"]["available"] is True


async def test_management_wrapper_leaves_invalid_validation_untouched() -> None:
    async def original(_hass, _user_id, _is_admin, _message):
        return {"valid": False, "errors": {"prompt": "invalid"}}

    wrapped = wrap_management_configuration_guidance(original)
    result = await wrapped(
        object(),
        "admin",
        True,
        {
            "section": "configuration",
            "action": "validate",
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
        },
    )
    assert "configuration_guidance" not in result
