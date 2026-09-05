"""Regression tests for standard provider timeout/connection failures."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.extended_openai_conversation_responses import async_setup_entry
from custom_components.extended_openai_conversation_responses.config_flow import (
    ExtendedOpenAIConversationConfigFlow,
)
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.helpers import (
    get_authenticated_client,
)
from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderTransportError,
    classify_config_provider_error,
    provider_transport_error,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import ConfigEntryNotReady


@pytest.mark.parametrize(
    ("raw_error", "expected_message"),
    [
        (TimeoutError("upstream secret timeout detail"), "Provider request timed out"),
        (ConnectionResetError("upstream secret connection detail"), "Could not connect to provider"),
    ],
)
def test_standard_transport_errors_normalize_safely(
    raw_error: TimeoutError | ConnectionError, expected_message: str
) -> None:
    """Raw standard transport failures become bounded provider errors."""
    error = provider_transport_error(raw_error)

    assert isinstance(error, ProviderTransportError)
    assert str(error) == expected_message
    assert classify_config_provider_error(error) == "cannot_connect"
    assert "secret" not in str(error)


async def test_authentication_probe_timeout_normalizes_before_config_callers() -> None:
    """Raw timeout from the models probe should enter the OpenAIError-compatible path."""
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(side_effect=TimeoutError("socket timed out"))
    )
    client = SimpleNamespace(models=SimpleNamespace(list=MagicMock()))

    with (
        patch(
            "custom_components.extended_openai_conversation_responses.helpers.AsyncOpenAI",
            return_value=client,
        ),
        patch(
            "custom_components.extended_openai_conversation_responses.helpers.get_async_client",
            return_value=MagicMock(),
        ),
        pytest.raises(ProviderTransportError, match="timed out") as caught,
    ):
        await get_authenticated_client(
            hass=hass,
            api_key="sk-test",
            base_url=None,
            api_version=None,
            organization=None,
            api_provider="openai",
        )

    assert isinstance(caught.value.__cause__, TimeoutError)


async def test_setup_treats_normalized_timeout_as_retryable() -> None:
    """Startup transport failures should ask HA to retry instead of failing config."""
    entry = SimpleNamespace(data={CONF_API_KEY: "sk-test"})
    error = ProviderTransportError("Provider request timed out")

    with (
        patch(
            "custom_components.extended_openai_conversation_responses.get_authenticated_client",
            AsyncMock(side_effect=error),
        ),
        pytest.raises(ConfigEntryNotReady) as caught,
    ):
        await async_setup_entry(MagicMock(), entry)

    assert caught.value.__cause__ is error


async def test_config_flow_reports_normalized_timeout_as_cannot_connect() -> None:
    """Interactive setup should retain the existing transient connection category."""
    flow = SimpleNamespace(
        hass=MagicMock(),
        async_show_form=MagicMock(side_effect=lambda **kwargs: kwargs),
        async_create_entry=MagicMock(),
    )

    with patch(
        "custom_components.extended_openai_conversation_responses.config_flow.validate_input",
        AsyncMock(side_effect=ProviderTransportError("Provider request timed out")),
    ):
        result = await ExtendedOpenAIConversationConfigFlow.async_step_user(
            flow, {CONF_API_KEY: "sk-test"}
        )

    assert result["errors"]["base"] == "cannot_connect"


async def test_provider_request_raw_timeout_is_normalized() -> None:
    """A raw runtime timeout should leave the provider loop as ProviderTransportError."""
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(
        data={},
        runtime_data=SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock(side_effect=TimeoutError()))
        ),
    )
    entity.subentry = SimpleNamespace(
        data={
            CONF_CHAT_MODEL: "gpt-5.6-mini",
            CONF_API_MODE: API_MODE_RESPONSES,
        }
    )
    entity._async_add_attachments = AsyncMock()
    chat_log = SimpleNamespace(content=[])

    with pytest.raises(ProviderTransportError, match="timed out") as caught:
        await entity._async_handle_chat_log(chat_log, [], [])

    assert isinstance(caught.value.__cause__, TimeoutError)


class _Usage:
    def __init__(self) -> None:
        self.failed: list[str] = []

    def mark_current_run_failed(self, name: str) -> None:
        self.failed.append(name)


async def test_conversation_returns_retryable_provider_error_for_timeout() -> None:
    """Normalized transport failures should use the normal provider-facing error path."""
    error = ProviderTransportError("Provider request timed out")
    entity = SimpleNamespace(
        hass=MagicMock(),
        entry=SimpleNamespace(async_start_reauth=MagicMock()),
        subentry=SimpleNamespace(data={}),
        _usage=_Usage(),
        _get_exposed_entities=lambda: [],
        _get_function_tools=lambda: [],
        _async_retrieve_memories=AsyncMock(return_value=[]),
        _async_retrieve_temporary_memories=AsyncMock(return_value=[]),
        _build_system_prompt=lambda *_args: "system",
        _async_handle_chat_log=AsyncMock(side_effect=error),
        _fire_conversation_finished=MagicMock(),
    )
    user_input = SimpleNamespace(
        language="en",
        conversation_id="conversation",
        text="hello",
        as_llm_context=lambda _domain: SimpleNamespace(),
    )
    chat_log = SimpleNamespace(content=[None], conversation_id="conversation")

    result = await ExtendedOpenAIAgentEntity._async_handle_message(
        entity, user_input, chat_log
    )

    assert entity._usage.failed == ["ProviderTransportError"]
    assert result.conversation_id == "conversation"
    assert "Provider request timed out" in result.response.speech["plain"]["speech"]
    entity.entry.async_start_reauth.assert_not_called()
