"""Tests for provider credential rotation and its dedicated WebSocket boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from openai import AuthenticationError
import pytest
import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import HomeAssistantError, Unauthorized

from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_PROVIDER,
    CONF_BASE_URL,
    CONF_ORGANIZATION,
    CONF_SKIP_AUTHENTICATION,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    MANAGEMENT_FRONTEND_MODULES,
)
from custom_components.extended_openai_conversation_responses.provider_credentials import (
    WS_UPDATE_API_KEY,
    _async_update_api_key_command,
    async_replace_api_key,
    setup_provider_credentials_websocket,
    websocket_update_api_key,
)


def _entry(
    *,
    skip_authentication: bool = False,
    state: ConfigEntryState = ConfigEntryState.LOADED,
    disabled_by=None,
    domain: str = DOMAIN,
):
    return SimpleNamespace(
        entry_id="entry-1",
        domain=domain,
        data={
            CONF_API_KEY: "old-key",
            CONF_API_PROVIDER: "openai",
            CONF_BASE_URL: "https://example.invalid/v1",
            CONF_ORGANIZATION: "org-1",
            CONF_SKIP_AUTHENTICATION: skip_authentication,
        },
        state=state,
        disabled_by=disabled_by,
    )


async def test_replacement_is_validated_before_persistence() -> None:
    entry = _entry()
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock(return_value=True)
    validate = AsyncMock(return_value=object())

    with patch(
        "custom_components.extended_openai_conversation_responses.provider_credentials.get_authenticated_client",
        validate,
    ):
        result = await async_replace_api_key(hass, entry, "new-key")

    validate.assert_awaited_once_with(
        hass=hass,
        api_key="new-key",
        base_url="https://example.invalid/v1",
        api_version=None,
        organization="org-1",
        skip_authentication=False,
        api_provider="openai",
    )
    hass.config_entries.async_update_entry.assert_called_once()
    saved = hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert saved[CONF_API_KEY] == "new-key"
    assert saved[CONF_BASE_URL] == "https://example.invalid/v1"
    assert entry.data[CONF_API_KEY] == "old-key"
    assert result == {
        "updated": True,
        "validation_performed": True,
        "reload_requested": True,
        "provider": "openai",
    }
    hass.config_entries.async_schedule_reload.assert_not_called()
    assert "new-key" not in repr(result)


async def test_invalid_replacement_leaves_existing_key_untouched() -> None:
    entry = _entry()
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()
    response = MagicMock(status_code=401, request=MagicMock())
    error = AuthenticationError("provider echoed something", response=response, body=None)

    with (
        patch(
            "custom_components.extended_openai_conversation_responses.provider_credentials.get_authenticated_client",
            AsyncMock(side_effect=error),
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await async_replace_api_key(hass, entry, "rejected-key")

    assert str(raised.value) == (
        "The provider rejected the new API key. The existing API key was not changed."
    )
    assert "provider echoed something" not in str(raised.value)
    assert entry.data[CONF_API_KEY] == "old-key"
    hass.config_entries.async_update_entry.assert_not_called()
    hass.config_entries.async_schedule_reload.assert_not_called()


async def test_unexpected_validation_error_cannot_reflect_candidate_secret() -> None:
    entry = _entry()
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()

    with (
        patch(
            "custom_components.extended_openai_conversation_responses.provider_credentials.get_authenticated_client",
            AsyncMock(side_effect=RuntimeError("candidate-secret appeared here")),
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await async_replace_api_key(hass, entry, "candidate-secret")

    assert "candidate-secret" not in str(raised.value)
    assert str(raised.value) == (
        "The new API key could not be validated. The existing API key was not changed."
    )
    hass.config_entries.async_update_entry.assert_not_called()


async def test_skip_authentication_is_preserved_and_reported() -> None:
    entry = _entry(skip_authentication=True)
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock(return_value=True)
    validate = AsyncMock(return_value=object())

    with patch(
        "custom_components.extended_openai_conversation_responses.provider_credentials.get_authenticated_client",
        validate,
    ):
        result = await async_replace_api_key(hass, entry, "new-key")

    assert validate.await_args.kwargs["skip_authentication"] is True
    assert result["validation_performed"] is False
    assert result["reload_requested"] is True
    hass.config_entries.async_update_entry.assert_called_once()
    hass.config_entries.async_schedule_reload.assert_not_called()


async def test_startup_failure_schedules_recovery_reload() -> None:
    entry = _entry(state=ConfigEntryState.SETUP_ERROR)
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock(return_value=True)

    with patch(
        "custom_components.extended_openai_conversation_responses.provider_credentials.get_authenticated_client",
        AsyncMock(return_value=object()),
    ):
        result = await async_replace_api_key(hass, entry, "new-key")

    assert result["updated"] is True
    assert result["reload_requested"] is True
    hass.config_entries.async_schedule_reload.assert_called_once_with("entry-1")


async def test_disabled_entry_is_not_reloaded_implicitly() -> None:
    entry = _entry(
        state=ConfigEntryState.NOT_LOADED,
        disabled_by="user",
    )
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock(return_value=True)

    with patch(
        "custom_components.extended_openai_conversation_responses.provider_credentials.get_authenticated_client",
        AsyncMock(return_value=object()),
    ):
        result = await async_replace_api_key(hass, entry, "new-key")

    assert result["updated"] is True
    assert result["reload_requested"] is False
    hass.config_entries.async_schedule_reload.assert_not_called()


async def test_nonrecoverable_entry_is_not_reloaded_implicitly() -> None:
    entry = _entry(state=ConfigEntryState.MIGRATION_ERROR)
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock(return_value=True)

    with patch(
        "custom_components.extended_openai_conversation_responses.provider_credentials.get_authenticated_client",
        AsyncMock(return_value=object()),
    ):
        result = await async_replace_api_key(hass, entry, "new-key")

    assert result["updated"] is True
    assert result["reload_requested"] is False
    hass.config_entries.async_schedule_reload.assert_not_called()


async def test_unchanged_key_retries_failed_entry_after_successful_validation() -> None:
    entry = _entry(state=ConfigEntryState.SETUP_ERROR)
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock(return_value=False)

    with patch(
        "custom_components.extended_openai_conversation_responses.provider_credentials.get_authenticated_client",
        AsyncMock(return_value=object()),
    ):
        result = await async_replace_api_key(hass, entry, "old-key")

    assert result["updated"] is False
    assert result["reload_requested"] is True
    hass.config_entries.async_schedule_reload.assert_called_once_with("entry-1")


async def test_unchanged_validated_key_reloads_loaded_entry() -> None:
    entry = _entry()
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock(return_value=False)

    with patch(
        "custom_components.extended_openai_conversation_responses.provider_credentials.get_authenticated_client",
        AsyncMock(return_value=object()),
    ):
        result = await async_replace_api_key(hass, entry, "old-key")

    assert result["updated"] is False
    assert result["validation_performed"] is True
    assert result["reload_requested"] is True
    hass.config_entries.async_schedule_reload.assert_called_once_with("entry-1")


async def test_unchanged_unvalidated_key_does_not_reload_loaded_entry() -> None:
    entry = _entry(skip_authentication=True)
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock(return_value=False)

    with patch(
        "custom_components.extended_openai_conversation_responses.provider_credentials.get_authenticated_client",
        AsyncMock(return_value=object()),
    ):
        result = await async_replace_api_key(hass, entry, "old-key")

    assert result["updated"] is False
    assert result["validation_performed"] is False
    assert result["reload_requested"] is False
    hass.config_entries.async_schedule_reload.assert_not_called()


def test_credential_websocket_schema_is_strict_and_accepts_api_key() -> None:
    schema = websocket_update_api_key._ws_schema
    message = {
        "id": 1,
        "type": WS_UPDATE_API_KEY,
        "entry_id": "entry-1",
        "api_key": "candidate-secret",
    }

    assert schema(message) == message
    with pytest.raises(vol.Invalid):
        schema({**message, "unexpected": "not allowed"})


def test_credential_websocket_rejects_non_admin_before_handler() -> None:
    hass = MagicMock()
    connection = MagicMock()
    connection.user = SimpleNamespace(is_admin=False)
    message = {
        "id": 1,
        "type": WS_UPDATE_API_KEY,
        "entry_id": "entry-1",
        "api_key": "candidate-secret",
    }

    with pytest.raises(Unauthorized):
        websocket_update_api_key(hass, connection, message)


async def test_credential_command_resolves_parent_entry_directly() -> None:
    hass = MagicMock()
    entry = _entry()
    hass.config_entries.async_get_entry.return_value = entry
    message = {
        "entry_id": "entry-1",
        "api_key": "new-key",
    }

    with patch(
        "custom_components.extended_openai_conversation_responses.provider_credentials.async_replace_api_key",
        AsyncMock(return_value={"updated": True}),
    ) as replace:
        result = await _async_update_api_key_command(hass, message)

    assert result == {"updated": True}
    hass.config_entries.async_get_entry.assert_called_once_with("entry-1")
    replace.assert_awaited_once_with(hass, entry, "new-key")


async def test_credential_command_rejects_non_integration_entry() -> None:
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = _entry(domain="other")

    with pytest.raises(HomeAssistantError, match="Integration entry not found"):
        await _async_update_api_key_command(
            hass,
            {"entry_id": "entry-1", "api_key": "new-key"},
        )


def test_credential_websocket_registration_is_idempotent() -> None:
    hass = SimpleNamespace(data={})
    with patch(
        "custom_components.extended_openai_conversation_responses.provider_credentials.websocket_api.async_register_command"
    ) as register:
        assert setup_provider_credentials_websocket(hass) is True
        assert setup_provider_credentials_websocket(hass) is False

    register.assert_called_once_with(hass, websocket_update_api_key)


def test_provider_credential_frontend_module_is_registered() -> None:
    assert "management-provider-credentials.js" in MANAGEMENT_FRONTEND_MODULES
