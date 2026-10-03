"""Runtime normalization for Voice Identity source-device semantics."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from .const import (
    CONF_VOICE_DEFAULT_USER_ID,
    CONF_VOICE_DEVICE_MAPPINGS,
    CONF_VOICE_SCOPE_POLICY,
    CONF_VOICE_UNMAPPED_POLICY,
    DEFAULT_VOICE_SCOPE_POLICY,
    DEFAULT_VOICE_UNMAPPED_POLICY,
    VOICE_POLICY_DEFAULT_USER,
    VOICE_POLICY_DEVICE_MAPPING,
    VOICE_POLICY_SHARED,
)
from .scope import (
    SHARED_HOUSEHOLD_SCOPE_ID,
    UNRETAINED_SCOPE_ID,
    bind_active_voice_identity_users,
)


def voice_source_device_id(user_input: Any) -> str | None:
    """Resolve data ownership without modifying the physical Assist origin."""
    return getattr(user_input, "device_id", None) or getattr(
        user_input, "satellite_id", None
    )


async def _user_is_active(agent: Any, user_id: str) -> bool:
    """Return whether one configured HA user still exists and is active."""
    user = await agent.hass.auth.async_get_user(user_id)
    return user is not None and user.is_active


async def _active_configured_users(agent: Any, user_input: Any) -> frozenset[str]:
    """Validate only configured users that can own this request's selected scope."""
    request_context = getattr(user_input, "context", None)
    if getattr(request_context, "user_id", None):
        # Authenticated Home Assistant identity wins before Voice Identity policy.
        return frozenset()

    options = getattr(agent.subentry, "data", {})
    policy = str(options.get(CONF_VOICE_SCOPE_POLICY, DEFAULT_VOICE_SCOPE_POLICY))
    device_id = voice_source_device_id(user_input)

    if policy == VOICE_POLICY_DEVICE_MAPPING:
        mappings = options.get(CONF_VOICE_DEVICE_MAPPINGS, {})
        if not device_id or not isinstance(mappings, Mapping):
            return frozenset()
        mapped = mappings.get(device_id)
        if isinstance(mapped, str) and mapped:
            if mapped in (VOICE_POLICY_SHARED, SHARED_HOUSEHOLD_SCOPE_ID):
                return frozenset()
            if mapped not in {"unretained", UNRETAINED_SCOPE_ID}:
                mapped_user = mapped.removeprefix("user:")
                if mapped_user and await _user_is_active(agent, mapped_user):
                    return frozenset({mapped_user})
        # Missing, explicit-unretained, malformed, deleted, or inactive mappings
        # are all treated exactly like an unmapped source device.
        policy = str(
            options.get(CONF_VOICE_UNMAPPED_POLICY, DEFAULT_VOICE_UNMAPPED_POLICY)
        )

    if policy == VOICE_POLICY_DEFAULT_USER:
        default_user = options.get(CONF_VOICE_DEFAULT_USER_ID)
        if (
            isinstance(default_user, str)
            and default_user
            and await _user_is_active(agent, default_user)
        ):
            return frozenset({default_user})

    return frozenset()


@asynccontextmanager
async def voice_identity_scope(agent: Any, user_input: Any) -> AsyncIterator[None]:
    """Validate request ownership using the registry source, then restore all context.

    Voice Identity selects data ownership only; it never replaces the authenticated
    Home Assistant caller context used to authorize tools and entity access.
    """
    active_users = await _active_configured_users(agent, user_input)
    with bind_active_voice_identity_users(active_users):
        yield
