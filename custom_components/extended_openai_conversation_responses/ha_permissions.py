"""Home Assistant user permission boundary for model-visible entities and actions."""

from __future__ import annotations

from collections.abc import Iterable
from contextvars import ContextVar
from typing import Any

from homeassistant.auth import EVENT_USER_ADDED, EVENT_USER_REMOVED, EVENT_USER_UPDATED
from homeassistant.auth.permissions import filter_entity_ids_by_permission
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN

_ACTIVE_HA_CONTEXT: ContextVar[Context | None] = ContextVar(
    "extended_openai_active_ha_context", default=None
)
_USER_CACHE_KEY = f"{DOMAIN}.permission_users"
_SETUP_KEY = f"{DOMAIN}.permission_users_setup"


def set_active_ha_context(context: Context | None) -> None:
    """Bind the real Home Assistant caller context to the current request task."""
    _ACTIVE_HA_CONTEXT.set(context)


def get_active_ha_context() -> Context | None:
    """Return the real Home Assistant caller context for the current task."""
    return _ACTIVE_HA_CONTEXT.get()


async def async_setup_ha_permissions(hass: HomeAssistant) -> None:
    """Cache user objects needed by the synchronous prompt exposure path."""
    cache: dict[str, Any] = hass.data.setdefault(_USER_CACHE_KEY, {})
    users = await hass.auth.async_get_users()
    cache.clear()
    cache.update({user.id: user for user in users})

    if hass.data.get(_SETUP_KEY):
        return
    hass.data[_SETUP_KEY] = True

    async def _async_refresh_user(user_id: str) -> None:
        user = await hass.auth.async_get_user(user_id)
        if user is not None:
            cache[user_id] = user

    @callback
    def _auth_changed(event: Event) -> None:
        user_id = event.data.get("user_id")
        if not isinstance(user_id, str) or not user_id:
            return
        # Fail closed immediately while a changed permission set is refreshed.
        cache.pop(user_id, None)
        if event.event_type != EVENT_USER_REMOVED:
            hass.async_create_task(
                _async_refresh_user(user_id),
                f"Refresh {DOMAIN} permissions for {user_id}",
            )

    for event_type in (EVENT_USER_ADDED, EVENT_USER_UPDATED, EVENT_USER_REMOVED):
        hass.bus.async_listen(event_type, _auth_changed)


def filter_entities_for_active_user(
    hass: HomeAssistant,
    entities: list[dict[str, Any]],
    *,
    policy: str = POLICY_READ,
) -> list[dict[str, Any]]:
    """Intersect Assist exposure with the authenticated HA user's permissions."""
    context = get_active_ha_context()
    user_id = context.user_id if context is not None else None
    if not user_id:
        # Voice/system calls without an authenticated HA user keep the existing
        # Assist exposure boundary. Identity mappings are not authorization.
        return entities

    user = hass.data.get(_USER_CACHE_KEY, {}).get(user_id)
    if user is None or not getattr(user, "is_active", True):
        return []

    entity_ids = [
        str(entity["entity_id"])
        for entity in entities
        if isinstance(entity.get("entity_id"), str)
    ]
    allowed = set(filter_entity_ids_by_permission(user, entity_ids, policy))
    return [entity for entity in entities if entity.get("entity_id") in allowed]


async def async_require_control_permission(
    hass: HomeAssistant,
    entity_ids: Iterable[str],
    *,
    context: Context | None = None,
) -> Context | None:
    """Require CONTROL permission for every resolved target of an HA action."""
    context = context or get_active_ha_context()
    user_id = context.user_id if context is not None else None
    if not user_id:
        return context

    user = await hass.auth.async_get_user(user_id)
    if user is None or not getattr(user, "is_active", True):
        raise HomeAssistantError("Home Assistant user is unavailable or inactive")

    targets = sorted(set(entity_ids))
    if not targets:
        return context
    denied = [
        entity_id
        for entity_id in targets
        if not user.permissions.check_entity(entity_id, POLICY_CONTROL)
    ]
    if denied:
        raise HomeAssistantError(
            "Home Assistant user does not have permission to control: "
            + ", ".join(denied)
        )
    return context
