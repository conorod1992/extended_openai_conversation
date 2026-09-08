"""Runtime normalization for Voice Identity source-device semantics."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from functools import wraps
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

_INSTALLED = False


@contextmanager
def _prefer_registry_device_source(user_input: Any) -> Iterator[None]:
    """Prefer HA's registry device ID while preserving satellite metadata afterwards.

    Home Assistant supplies both ``device_id`` and ``satellite_id`` for Assist
    satellite requests. Voice Identity mappings and device-scoped continuity are
    keyed by the device-registry ID, while ``satellite_id`` identifies the Assist
    satellite entity. The conversation engine historically preferred the latter.

    Temporarily hide only the satellite source when a registry device ID is also
    available so the existing resolver follows its normal ``device_id`` path. Keep
    the satellite fallback unchanged for callers that genuinely have no device ID.
    """
    device_id = getattr(user_input, "device_id", None)
    satellite_id = getattr(user_input, "satellite_id", None)
    if not device_id or not satellite_id:
        yield
        return

    user_input.satellite_id = None
    try:
        yield
    finally:
        user_input.satellite_id = satellite_id


def _configured_user_candidates(
    user_input: Any, options: Mapping[str, Any]
) -> frozenset[str]:
    """Return configured HA user IDs that could own this unidentified request."""
    policy = str(options.get(CONF_VOICE_SCOPE_POLICY, DEFAULT_VOICE_SCOPE_POLICY))
    device_id = getattr(user_input, "satellite_id", None) or getattr(
        user_input, "device_id", None
    )
    candidates: set[str] = set()

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
                if mapped_user:
                    candidates.add(mapped_user)
        policy = str(
            options.get(CONF_VOICE_UNMAPPED_POLICY, DEFAULT_VOICE_UNMAPPED_POLICY)
        )

    if policy == VOICE_POLICY_DEFAULT_USER:
        default_user = options.get(CONF_VOICE_DEFAULT_USER_ID)
        if isinstance(default_user, str) and default_user:
            candidates.add(default_user)

    return frozenset(candidates)


async def _active_configured_users(
    agent: Any, user_input: Any
) -> frozenset[str]:
    """Resolve configured candidate IDs against Home Assistant's current users."""
    options = getattr(agent.subentry, "data", {})
    candidates = _configured_user_candidates(user_input, options)
    active: set[str] = set()
    for user_id in sorted(candidates):
        user = await agent.hass.auth.async_get_user(user_id)
        if user is not None and user.is_active:
            active.add(user_id)
    return frozenset(active)


def install_voice_identity_runtime() -> None:
    """Install Voice Identity runtime checks on the effective conversation entry point."""
    global _INSTALLED
    if _INSTALLED:
        return

    from . import conversation

    current = conversation.ExtendedOpenAIAgentEntity._async_process
    if getattr(current, "_extended_openai_voice_identity_device", False):
        _INSTALLED = True
        return

    @wraps(current)
    async def process_with_registry_device(self: Any, user_input: Any) -> Any:
        with _prefer_registry_device_source(user_input):
            active_users = await _active_configured_users(self, user_input)
            with bind_active_voice_identity_users(active_users):
                return await current(self, user_input)

    process_with_registry_device._extended_openai_voice_identity_device = True  # type: ignore[attr-defined]
    conversation.ExtendedOpenAIAgentEntity._async_process = (  # type: ignore[method-assign]
        process_with_registry_device
    )
    _INSTALLED = True
