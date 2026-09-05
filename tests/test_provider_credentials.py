"""Tests for provider credential rotation and management routing."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from openai import AuthenticationError
import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_PROVIDER,
    CONF_BASE_URL,
    CONF_ORGANIZATION,
    CONF_SKIP_AUTHENTICATION,
)
from custom_components.extended_openai_conversation_responses.management_permissions import (
    wrap_management_permissions,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    MANAGEMENT_FRONTEND_MODULES,
)
from custom_components.extended_openai_conversation_responses.provider_credentials import (
    async_replace_api_key,
)


def _entry(
    *,
    skip_authentication: bool = False,
    state: ConfigEntryState = ConfigEntryState.LOADED,
    update_listener: bool = True,
    disabled_by=None,
):
    return SimpleNamespace(
        entry_id="entry-1",
        data={
            CONF_API_KEY: "old-key",
            CONF_API_PROVIDER: "openai",
            CONF_BASE_URL: "https://example.invalid/v1",
            CONF_ORGANIZATION: "org-1",
            CONF_SKIP_AUTHENTICATION: skip_authentication,
        },
        state=state,
        update_listeners=[object()] if update_listener else [],
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


async def test_startup_failure_schedules_recovery_reload_without_listener() -> None:
    entry = _entry(state=ConfigEntryState.SETUP_ERROR, update_listener=False)
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
        update_listener=False,
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


async def test_identical_key_does_not_claim_or_schedule_reload() -> None:
    entry = _entry(state=ConfigEntryState.SETUP_ERROR, update_listener=False)
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock(return_value=False)

    with patch(
        "custom_components.extended_openai_conversation_responses.provider_credentials.get_authenticated_client",
        AsyncMock(return_value=object()),
    ):
        result = await async_replace_api_key(hass, entry, "old-key")

    assert result["updated"] is False
    assert result["reload_requested"] is False
    hass.config_entries.async_schedule_reload.assert_not_called()


async def test_management_action_is_admin_only_and_validates_agent_scope() -> None:
    original = AsyncMock(return_value={"unexpected": True})
    wrapped = wrap_management_permissions(original)
    hass = MagicMock()
    entry = _entry()
    message = {
        "section": "diagnostics",
        "action": "update_api_key",
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "api_key": "new-key",
    }

    with (
        patch(
            "custom_components.extended_openai_conversation_responses.management_permissions.management_ui.entry_and_agent",
            return_value=(entry, SimpleNamespace(subentry_id="agent-1")),
        ) as resolve,
        patch(
            "custom_components.extended_openai_conversation_responses.management_permissions.async_replace_api_key",
            AsyncMock(return_value={"updated": True}),
        ) as replace,
    ):
        result = await wrapped(hass, "admin", True, message)

    assert result == {"updated": True}
    resolve.assert_called_once_with(hass, "entry-1", "agent-1")
    replace.assert_awaited_once_with(hass, entry, "new-key")
    original.assert_not_awaited()

    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await wrapped(hass, "normal-user", False, message)


async def test_other_diagnostics_actions_still_use_existing_dispatcher() -> None:
    original = AsyncMock(return_value={"status": "Passed"})
    wrapped = wrap_management_permissions(original)
    hass = MagicMock()
    message = {"section": "diagnostics", "action": "test_agent"}

    assert await wrapped(hass, "admin", True, message) == {"status": "Passed"}
    original.assert_awaited_once_with(hass, "admin", True, message)


def test_provider_credential_frontend_module_is_registered() -> None:
    assert "management-provider-credentials.js" in MANAGEMENT_FRONTEND_MODULES
