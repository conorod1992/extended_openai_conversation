"""Tests for AI Task Web Search configuration wiring."""

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_API_PROVIDER,
    CONF_WEB_SEARCH,
    DEFAULT_AI_TASK_OPTIONS,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)


def test_ai_task_web_search_uses_shared_responses_tool() -> None:
    """AI Task options should enable the existing hosted Web Search tool."""
    options = {
        **DEFAULT_AI_TASK_OPTIONS,
        CONF_API_MODE: API_MODE_RESPONSES,
        CONF_WEB_SEARCH: True,
    }

    snapshot = build_provider_request_snapshot(
        options,
        {CONF_API_PROVIDER: "openai"},
    )

    assert snapshot.provider_tools == (
        {"type": "web_search", "search_context_size": "low"},
    )


def test_ai_task_web_search_remains_disabled_by_default() -> None:
    """Existing and newly created AI Task agents should not search unless enabled."""
    snapshot = build_provider_request_snapshot(
        DEFAULT_AI_TASK_OPTIONS,
        {CONF_API_PROVIDER: "openai"},
    )

    assert snapshot.provider_tools == ()


def test_ai_task_web_search_keeps_responses_requirement() -> None:
    """AI Task Web Search should retain the shared Responses-only guardrail."""
    options = {
        **DEFAULT_AI_TASK_OPTIONS,
        CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
        CONF_WEB_SEARCH: True,
    }

    with pytest.raises(HomeAssistantError, match="requires the Responses API"):
        build_provider_request_snapshot(
            options,
            {CONF_API_PROVIDER: "openai"},
        )
