"""Focused invalid-credential and reauthentication tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from openai import APIConnectionError, AuthenticationError
import pytest

from custom_components.extended_openai_conversation_responses import async_setup_entry
from custom_components.extended_openai_conversation_responses.config_flow import (
    ExtendedOpenAIConversationConfigFlow,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_PROVIDER,
    CONF_API_VERSION,
    CONF_BASE_URL,
    CONF_SKIP_AUTHENTICATION,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady


def _authentication_error() -> AuthenticationError:
    return AuthenticationError(
        "invalid", response=MagicMock(status_code=401, request=MagicMock()), body=None
    )


async def test_setup_invalid_credentials_requests_reauthentication() -> None:
    entry = SimpleNamespace(
        data={CONF_API_KEY: "expired"}, entry_id="entry", runtime_data=None
    )
    with (
        patch(
            "custom_components.extended_openai_conversation_responses.get_authenticated_client",
            AsyncMock(side_effect=_authentication_error()),
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_setup_entry(MagicMock(), entry)


async def test_setup_transient_connection_failure_remains_not_ready() -> None:
    entry = SimpleNamespace(
        data={CONF_API_KEY: "valid"}, entry_id="entry", runtime_data=None
    )
    with (
        patch(
            "custom_components.extended_openai_conversation_responses.get_authenticated_client",
            AsyncMock(side_effect=APIConnectionError(request=MagicMock())),
        ),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(MagicMock(), entry)


async def test_reauthentication_preserves_azure_and_skip_auth_settings() -> None:
    entry = SimpleNamespace(
        data={
            CONF_API_KEY: "expired",
            CONF_API_PROVIDER: "azure",
            CONF_BASE_URL: "https://example.openai.azure.com",
            CONF_API_VERSION: "2025-01-01-preview",
            CONF_SKIP_AUTHENTICATION: False,
            "organization": "org",
        }
    )
    flow = SimpleNamespace(
        _reauth_entry=entry,
        hass=MagicMock(),
        async_update_reload_and_abort=MagicMock(return_value={"type": "abort"}),
        async_show_form=MagicMock(),
    )
    with patch(
        "custom_components.extended_openai_conversation_responses.config_flow.validate_input",
        AsyncMock(),
    ) as validate:
        result = await ExtendedOpenAIConversationConfigFlow.async_step_reauth_confirm(
            flow, {CONF_API_KEY: "replacement"}
        )

    updated = validate.await_args.args[1]
    assert updated[CONF_API_KEY] == "replacement"
    assert updated[CONF_API_PROVIDER] == "azure"
    assert updated[CONF_BASE_URL] == "https://example.openai.azure.com"
    assert updated[CONF_API_VERSION] == "2025-01-01-preview"
    assert updated[CONF_SKIP_AUTHENTICATION] is False
    assert result == {"type": "abort"}
