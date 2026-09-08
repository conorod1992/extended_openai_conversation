"""Tests for Voice Identity runtime source-device normalization."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    voice_identity_runtime,
)
from custom_components.extended_openai_conversation_responses import conversation
from custom_components.extended_openai_conversation_responses.const import (
    CONF_VOICE_DEFAULT_USER_ID,
    CONF_VOICE_DEVICE_MAPPINGS,
    CONF_VOICE_SCOPE_POLICY,
    CONF_VOICE_UNMAPPED_POLICY,
    VOICE_POLICY_DEFAULT_USER,
    VOICE_POLICY_DEVICE_MAPPING,
    VOICE_POLICY_SHARED,
)
from custom_components.extended_openai_conversation_responses.scope import (
    SHARED_HOUSEHOLD_SCOPE_ID,
    resolve_data_scope,
)


def test_registry_device_temporarily_overrides_satellite_source() -> None:
    user_input = SimpleNamespace(
        device_id="device-registry-id",
        satellite_id="assist_satellite.kitchen",
    )

    with voice_identity_runtime._prefer_registry_device_source(user_input):
        assert user_input.device_id == "device-registry-id"
        assert user_input.satellite_id is None

    assert user_input.satellite_id == "assist_satellite.kitchen"


def test_satellite_fallback_is_preserved_without_registry_device() -> None:
    user_input = SimpleNamespace(
        device_id=None,
        satellite_id="assist_satellite.kitchen",
    )

    with voice_identity_runtime._prefer_registry_device_source(user_input):
        assert user_input.satellite_id == "assist_satellite.kitchen"


@pytest.mark.asyncio
async def test_installed_wrapper_uses_registry_device_and_restores_satellite(
    monkeypatch,
) -> None:
    observed: list[tuple[str | None, str | None]] = []

    async def original_process(_self, user_input):
        observed.append((user_input.device_id, user_input.satellite_id))
        return "processed"

    monkeypatch.setattr(
        conversation.ExtendedOpenAIAgentEntity,
        "_async_process",
        original_process,
    )
    monkeypatch.setattr(voice_identity_runtime, "_INSTALLED", False)

    voice_identity_runtime.install_voice_identity_runtime()

    auth_lookup = AsyncMock()
    agent = SimpleNamespace(
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=auth_lookup)),
        subentry=SimpleNamespace(data={}),
    )
    user_input = SimpleNamespace(
        device_id="device-registry-id",
        satellite_id="assist_satellite.kitchen",
    )
    result = await conversation.ExtendedOpenAIAgentEntity._async_process(
        agent, user_input
    )

    assert result == "processed"
    assert observed == [("device-registry-id", None)]
    assert user_input.satellite_id == "assist_satellite.kitchen"
    auth_lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_stale_mapping_follows_unmapped_policy(monkeypatch) -> None:
    options = {
        CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
        CONF_VOICE_DEVICE_MAPPINGS: {"device-registry-id": "user:deleted-user"},
        CONF_VOICE_UNMAPPED_POLICY: VOICE_POLICY_SHARED,
    }
    observed_scopes = []

    async def original_process(_self, user_input):
        observed_scopes.append(
            resolve_data_scope(
                SimpleNamespace(
                    context=SimpleNamespace(user_id=None),
                    device_id=user_input.satellite_id or user_input.device_id,
                ),
                options,
            )
        )
        return "processed"

    monkeypatch.setattr(
        conversation.ExtendedOpenAIAgentEntity,
        "_async_process",
        original_process,
    )
    monkeypatch.setattr(voice_identity_runtime, "_INSTALLED", False)
    voice_identity_runtime.install_voice_identity_runtime()

    auth_lookup = AsyncMock(return_value=None)
    agent = SimpleNamespace(
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=auth_lookup)),
        subentry=SimpleNamespace(data=options),
    )
    user_input = SimpleNamespace(
        device_id="device-registry-id",
        satellite_id="assist_satellite.kitchen",
    )

    await conversation.ExtendedOpenAIAgentEntity._async_process(agent, user_input)

    assert observed_scopes[0].scope_id == SHARED_HOUSEHOLD_SCOPE_ID
    assert observed_scopes[0].source == "shared_voice_policy"
    auth_lookup.assert_awaited_once_with("deleted-user")
    assert user_input.satellite_id == "assist_satellite.kitchen"


@pytest.mark.asyncio
async def test_runtime_user_validation_excludes_inactive_user() -> None:
    options = {
        CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
        CONF_VOICE_DEVICE_MAPPINGS: {"kitchen": "user:mapped-user"},
        CONF_VOICE_UNMAPPED_POLICY: VOICE_POLICY_DEFAULT_USER,
        CONF_VOICE_DEFAULT_USER_ID: "default-user",
    }

    async def get_user(user_id):
        return SimpleNamespace(is_active=user_id == "mapped-user")

    agent = SimpleNamespace(
        hass=SimpleNamespace(
            auth=SimpleNamespace(async_get_user=AsyncMock(side_effect=get_user))
        ),
        subentry=SimpleNamespace(data=options),
    )
    user_input = SimpleNamespace(device_id="kitchen", satellite_id=None)

    active = await voice_identity_runtime._active_configured_users(agent, user_input)

    assert active == frozenset({"mapped-user"})
    assert agent.hass.auth.async_get_user.await_count == 2
