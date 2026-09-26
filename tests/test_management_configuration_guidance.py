"""Tests for side-effect-free management configuration guidance."""

from pathlib import Path

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_MODE,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
)
from custom_components.extended_openai_conversation_responses.management_configuration_guidance import (
    configuration_guidance_snapshot,
)


def test_web_search_guidance_reuses_runtime_compatibility_rules() -> None:
    config = agent_config_defaults()
    config[CONF_CHAT_MODEL] = "gpt-5-mini"
    config[CONF_API_MODE] = "auto"
    config[CONF_REASONING_EFFORT] = "low"

    chat = configuration_guidance_snapshot({}, config)
    assert chat["effective_api_mode"] == "responses"
    assert chat["web_search"]["available"] is True

    config[CONF_API_MODE] = "chat_completions"
    explicit_chat = configuration_guidance_snapshot({}, config)
    assert explicit_chat["web_search"]["available"] is False
    assert explicit_chat["web_search"]["reason"] == "requires_responses"
    assert "Responses API" in explicit_chat["web_search"]["message"]

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


def test_configuration_guidance_frontend_modules_are_build_inputs() -> None:
    frontend = (
        Path(__file__).parents[1]
        / "custom_components"
        / "extended_openai_conversation_responses"
        / "frontend"
    )
    for name in (
        "management-configuration-guidance.js",
        "management-decision-guidance.js",
    ):
        assert (frontend / name).is_file()


async def test_management_adds_guidance_to_successful_config(hass, management_agent):
    from custom_components.extended_openai_conversation_responses import management_ui

    result = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "configuration",
            "action": "validate",
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "config": {CONF_API_MODE: "responses"},
        },
    )
    assert result["valid"] is True
    assert result["configuration_guidance"]["web_search"]["available"] is True


async def test_management_leaves_invalid_validation_untouched(hass, management_agent):
    from custom_components.extended_openai_conversation_responses import management_ui

    result = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "configuration",
            "action": "validate",
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "config": {
                "speech_regex_replacements": [{"pattern": "[", "replacement": ""}]
            },
        },
    )
    assert result["valid"] is False
    assert "configuration_guidance" not in result
    hass.config_entries.async_update_subentry.assert_not_called()
