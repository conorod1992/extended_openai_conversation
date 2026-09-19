"""Tests for Voice Identity runtime source-device normalization."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    voice_identity_runtime,
)
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


async def test_request_owner_uses_registry_device_and_restores_satellite(
    entry_agent, entry_input,
) -> None:
    observed = []
    async def process(request):
        observed.append((request.device_id, request.satellite_id))
        return "processed"
    entry_agent._async_process_with_continuity = process
    result = await entry_agent.async_process(entry_input)
    assert result == "processed"
    assert observed == [("device-registry-id", None)]
    assert entry_input.satellite_id == "assist_satellite.kitchen"
    entry_agent.hass.auth.async_get_user.assert_not_awaited()


async def test_runtime_stale_mapping_follows_unmapped_policy(
    entry_agent, entry_input,
) -> None:
    options = {
        CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
        CONF_VOICE_DEVICE_MAPPINGS: {"device-registry-id": "user:deleted-user"},
        CONF_VOICE_UNMAPPED_POLICY: VOICE_POLICY_SHARED,
    }
    observed_scopes = []
    async def process(request):
        observed_scopes.append(resolve_data_scope(
            SimpleNamespace(context=request.context, device_id=request.satellite_id or request.device_id),
            options,
        ))
        return "processed"
    entry_agent._async_process_with_continuity = process
    entry_agent.subentry.data = options
    entry_agent.hass.auth.async_get_user.return_value = None
    await entry_agent.async_process(entry_input)
    assert observed_scopes[0].scope_id == SHARED_HOUSEHOLD_SCOPE_ID
    assert observed_scopes[0].source == "shared_voice_policy"
    entry_agent.hass.auth.async_get_user.assert_awaited_once_with("deleted-user")
    assert entry_input.satellite_id == "assist_satellite.kitchen"


@pytest.mark.asyncio
async def test_runtime_user_validation_falls_through_inactive_mapping() -> None:
    options = {
        CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
        CONF_VOICE_DEVICE_MAPPINGS: {"kitchen": "user:mapped-user"},
        CONF_VOICE_UNMAPPED_POLICY: VOICE_POLICY_DEFAULT_USER,
        CONF_VOICE_DEFAULT_USER_ID: "default-user",
    }

    async def get_user(user_id):
        return SimpleNamespace(is_active=user_id == "default-user")

    auth_lookup = AsyncMock(side_effect=get_user)
    agent = SimpleNamespace(
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=auth_lookup)),
        subentry=SimpleNamespace(data=options),
    )
    user_input = SimpleNamespace(device_id="kitchen", satellite_id=None)

    active = await voice_identity_runtime._active_configured_users(agent, user_input)

    assert active == frozenset({"default-user"})
    assert [call.args[0] for call in auth_lookup.await_args_list] == [
        "mapped-user",
        "default-user",
    ]


@pytest.mark.asyncio
async def test_authenticated_request_skips_configured_user_validation() -> None:
    options = {
        CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
        CONF_VOICE_DEVICE_MAPPINGS: {"kitchen": "user:deleted-user"},
    }
    auth_lookup = AsyncMock()
    agent = SimpleNamespace(
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=auth_lookup)),
        subentry=SimpleNamespace(data=options),
    )
    user_input = SimpleNamespace(
        context=SimpleNamespace(user_id="authenticated-user"),
        device_id="kitchen",
        satellite_id=None,
    )

    active = await voice_identity_runtime._active_configured_users(agent, user_input)

    assert active == frozenset()
    auth_lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("device_id", "mappings"),
    [
        (None, {"kitchen": "user:mapped-user"}),
        ("kitchen", ["not", "a", "mapping"]),
    ],
)
async def test_device_mapping_requires_device_and_mapping_shape(
    device_id, mappings
) -> None:
    """Incomplete or malformed mapping configuration must not confer user scope."""
    auth_lookup = AsyncMock()
    agent = SimpleNamespace(
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=auth_lookup)),
        subentry=SimpleNamespace(
            data={
                CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
                CONF_VOICE_DEVICE_MAPPINGS: mappings,
            }
        ),
    )
    user_input = SimpleNamespace(device_id=device_id, satellite_id=None)

    active = await voice_identity_runtime._active_configured_users(agent, user_input)

    assert active == frozenset()
    auth_lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_device_mapping_never_validates_or_binds_a_user() -> None:
    """An explicitly shared source remains outside any configured user scope."""
    auth_lookup = AsyncMock()
    agent = SimpleNamespace(
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=auth_lookup)),
        subentry=SimpleNamespace(
            data={
                CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
                CONF_VOICE_DEVICE_MAPPINGS: {"kitchen": VOICE_POLICY_SHARED},
            }
        ),
    )
    user_input = SimpleNamespace(device_id="kitchen", satellite_id=None)

    active = await voice_identity_runtime._active_configured_users(agent, user_input)

    assert active == frozenset()
    auth_lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_mapped_user_is_the_only_bound_identity() -> None:
    """A valid active device mapping selects exactly that configured user."""
    auth_lookup = AsyncMock(return_value=SimpleNamespace(is_active=True))
    agent = SimpleNamespace(
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=auth_lookup)),
        subentry=SimpleNamespace(
            data={
                CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
                CONF_VOICE_DEVICE_MAPPINGS: {"kitchen": "user:mapped-user"},
            }
        ),
    )
    user_input = SimpleNamespace(device_id="kitchen", satellite_id=None)

    active = await voice_identity_runtime._active_configured_users(agent, user_input)

    assert active == frozenset({"mapped-user"})
    auth_lookup.assert_awaited_once_with("mapped-user")


@pytest.mark.asyncio
async def test_explicit_unretained_mapping_uses_configured_unmapped_fallback() -> None:
    """Unretained mappings do not become user IDs and follow the fallback policy."""
    auth_lookup = AsyncMock(return_value=SimpleNamespace(is_active=True))
    agent = SimpleNamespace(
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=auth_lookup)),
        subentry=SimpleNamespace(
            data={
                CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEVICE_MAPPING,
                CONF_VOICE_DEVICE_MAPPINGS: {"kitchen": "unretained"},
                CONF_VOICE_UNMAPPED_POLICY: VOICE_POLICY_DEFAULT_USER,
                CONF_VOICE_DEFAULT_USER_ID: "default-user",
            }
        ),
    )
    user_input = SimpleNamespace(device_id="kitchen", satellite_id=None)

    active = await voice_identity_runtime._active_configured_users(agent, user_input)

    assert active == frozenset({"default-user"})
    auth_lookup.assert_awaited_once_with("default-user")


@pytest.mark.asyncio
async def test_inactive_default_user_cannot_be_bound() -> None:
    """A stale default-user setting fails closed rather than retaining user scope."""
    auth_lookup = AsyncMock(return_value=SimpleNamespace(is_active=False))
    agent = SimpleNamespace(
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=auth_lookup)),
        subentry=SimpleNamespace(
            data={
                CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEFAULT_USER,
                CONF_VOICE_DEFAULT_USER_ID: "inactive-user",
            }
        ),
    )
    user_input = SimpleNamespace(device_id="kitchen", satellite_id=None)

    active = await voice_identity_runtime._active_configured_users(agent, user_input)

    assert active == frozenset()
    auth_lookup.assert_awaited_once_with("inactive-user")


async def test_voice_scope_restores_satellite_when_user_validation_is_cancelled():
    import asyncio
    agent = SimpleNamespace(
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=AsyncMock(side_effect=asyncio.CancelledError))),
        subentry=SimpleNamespace(data={
            CONF_VOICE_SCOPE_POLICY: VOICE_POLICY_DEFAULT_USER,
            CONF_VOICE_DEFAULT_USER_ID: "alice",
        }),
    )
    request = SimpleNamespace(device_id="kitchen", satellite_id="satellite", context=None)
    with pytest.raises(asyncio.CancelledError):
        async with voice_identity_runtime.voice_identity_scope(agent, request):
            pytest.fail("cancelled identity validation reached processing")
    assert request.satellite_id == "satellite"


