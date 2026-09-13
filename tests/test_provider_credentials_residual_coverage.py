"""Focused residual coverage for provider credential management."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import provider_credentials


def _undecorated_handler() -> Any:
    """Return the underlying websocket handler beneath HA decorators."""
    handler: Any = provider_credentials.websocket_update_api_key
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__
    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key", ["", "   ", None, 123])
async def test_replace_api_key_rejects_empty_or_non_string_before_validation(
    monkeypatch: pytest.MonkeyPatch, api_key: Any
) -> None:
    """Invalid management input must not reach provider auth or mutate config."""
    authenticate = AsyncMock()
    monkeypatch.setattr(provider_credentials, "get_authenticated_client", authenticate)

    config_entries = SimpleNamespace(async_update_entry=MagicMock())
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(data={"api_key": "existing-key"})

    with pytest.raises(HomeAssistantError, match="Enter a new API key"):
        await provider_credentials.async_replace_api_key(hass, entry, api_key)

    authenticate.assert_not_awaited()
    config_entries.async_update_entry.assert_not_called()
    assert entry.data == {"api_key": "existing-key"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        HomeAssistantError("provider settings changed"),
        RuntimeError("runtime unavailable"),
        ValueError("invalid request"),
    ],
)
async def test_websocket_update_api_key_translates_expected_failures(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """Credential failures are returned as bounded websocket request errors."""
    command = AsyncMock(side_effect=error)
    monkeypatch.setattr(provider_credentials, "_async_update_api_key_command", command)
    connection = SimpleNamespace(send_error=MagicMock(), send_result=MagicMock())
    hass = SimpleNamespace()
    msg = {"id": 17, "entry_id": "entry-1", "api_key": "candidate-key"}

    await _undecorated_handler()(hass, connection, msg)

    command.assert_awaited_once_with(hass, msg)
    connection.send_error.assert_called_once_with(17, "invalid_request", str(error))
    connection.send_result.assert_not_called()


@pytest.mark.asyncio
async def test_websocket_update_api_key_returns_safe_success_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful rotation returns only the command result through websocket."""
    result = {
        "updated": True,
        "validation_performed": True,
        "reload_requested": True,
        "provider": "openai",
    }
    command = AsyncMock(return_value=result)
    monkeypatch.setattr(provider_credentials, "_async_update_api_key_command", command)
    connection = SimpleNamespace(send_error=MagicMock(), send_result=MagicMock())
    hass = SimpleNamespace()
    msg = {"id": 23, "entry_id": "entry-1", "api_key": "candidate-key"}

    await _undecorated_handler()(hass, connection, msg)

    command.assert_awaited_once_with(hass, msg)
    connection.send_result.assert_called_once_with(23, result)
    connection.send_error.assert_not_called()
    assert "api_key" not in result
