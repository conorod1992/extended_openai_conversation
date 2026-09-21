"""Reusable Management backend projections.

These helpers own frontend-facing data projections that are shared by the
Management command orchestrator and loading/catalog code. They intentionally
depend only on domain/configuration owners, never on management_ui.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
from types import MappingProxyType
from typing import Any

from homeassistant.core import HomeAssistant

from .agent_config import agent_config_defaults
from .const import (
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
    CONF_ARCHIVE_RETENTION_DAYS,
    CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
    CONF_SHARED_ARCHIVE_ENABLED,
    CONF_SHARED_MEMORY_MODE,
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
    CONF_VOICE_DEFAULT_USER_ID,
    CONF_VOICE_DEVICE_MAPPINGS,
    CONF_VOICE_SCOPE_POLICY,
    CONF_VOICE_UNMAPPED_POLICY,
)
from .memory import ANONYMOUS_USER_ID
from .scope import SHARED_HOUSEHOLD_SCOPE_ID


def _memory_scope(scope_id: str) -> str:
    """Return the persistent-memory owner key represented by a Management scope."""
    return scope_id.removeprefix("user:") if scope_id.startswith("user:") else scope_id


async def async_scope_catalog_projection(
    hass: HomeAssistant,
    user_id: str,
    is_admin: bool,
    memory_counts: dict[str, int] | None = None,
    conversation_counts: dict[str, int] | None = None,
    temporary_memory_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Project visible Management scopes and their aggregate counts."""
    memory_counts = memory_counts or {}
    conversation_counts = conversation_counts or {}
    temporary_memory_counts = temporary_memory_counts or {}

    def scope_item(scope_id: str, scope_type: str, display_name: str) -> dict[str, Any]:
        owner = _memory_scope(scope_id)
        return {
            "scope_id": scope_id,
            "scope_type": scope_type,
            "display_name": display_name,
            "is_current_user": scope_id == f"user:{user_id}",
            "memory_count": memory_counts.get(owner, 0),
            "conversation_count": conversation_counts.get(scope_id, 0),
            "temporary_memory_count": temporary_memory_counts.get(scope_id, 0),
        }

    if not is_admin:
        user = await hass.auth.async_get_user(user_id)
        return [
            scope_item(
                f"user:{user_id}", "user", (user.name or user_id) if user else user_id
            )
        ]

    users = await hass.auth.async_get_users()
    scopes = [
        scope_item(f"user:{user.id}", "user", user.name or user.id) for user in users
    ]
    scopes.append(scope_item(SHARED_HOUSEHOLD_SCOPE_ID, "shared", "Shared household"))
    legacy = scope_item(ANONYMOUS_USER_ID, "anonymous_legacy", "Legacy anonymous")
    if legacy["memory_count"] or legacy["conversation_count"]:
        scopes.append(legacy)
    return scopes


@lru_cache(maxsize=1)
def _settings_defaults() -> Mapping[str, Any]:
    """Build invariant Management settings defaults once."""
    defaults = agent_config_defaults()
    # Management historically presents an unconfigured voice owner as null even
    # though the broader agent configuration normalization has its own defaults.
    defaults[CONF_VOICE_DEFAULT_USER_ID] = None
    return MappingProxyType(defaults)


def settings_snapshot(options: Mapping[str, Any]) -> dict[str, Any]:
    """Project the Management conversation/archive settings shape."""
    defaults = _settings_defaults()
    keys = (
        CONF_ARCHIVE_ENABLED,
        CONF_ARCHIVE_RETENTION_DAYS,
        CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
        CONF_SHARED_ARCHIVE_ENABLED,
        CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
        CONF_VOICE_SCOPE_POLICY,
        CONF_VOICE_DEFAULT_USER_ID,
        CONF_VOICE_DEVICE_MAPPINGS,
        CONF_VOICE_UNMAPPED_POLICY,
        CONF_SHARED_MEMORY_MODE,
        CONF_USAGE_REQUEST_RETENTION_DAYS,
        CONF_USAGE_RUN_RETENTION_DAYS,
    )
    return {
        key: options[key] if key in options else deepcopy(defaults[key]) for key in keys
    }
