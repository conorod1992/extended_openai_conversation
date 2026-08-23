"""Shared Home Assistant action execution seam."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound

from .protected_actions import (
    async_require_protection,
    reset_protection_bypass,
    set_protection_bypass,
)


async def async_call_ha_action(
    hass: HomeAssistant,
    domain: str,
    service: str,
    *,
    data: Mapping[str, Any] | None = None,
    target: Mapping[str, Any] | None = None,
    blocking: bool = False,
) -> None:
    """Call one HA action through the integration's common authorization seam.

    Both model-driven native service calls and administrator-configured local
    Request Rules pass through this backend-enforced policy seam.
    """
    action = {
        "domain": domain,
        "service": service,
        "data": data or {},
        "target": target or {},
    }
    await async_require_protection([action])
    await _async_call_ha_action_unchecked(
        hass, domain, service, data=data, target=target, blocking=blocking
    )


async def _async_call_ha_action_unchecked(
    hass: HomeAssistant,
    domain: str,
    service: str,
    *,
    data: Mapping[str, Any] | None = None,
    target: Mapping[str, Any] | None = None,
    blocking: bool = False,
) -> None:
    """Call an action after the caller completed policy enforcement."""
    if not hass.services.has_service(domain, service):
        raise ServiceNotFound(domain, service)
    kwargs: dict[str, Any] = {"service_data": dict(data or {})}
    if target:
        kwargs["target"] = dict(target)
    if blocking:
        kwargs["blocking"] = True
    await hass.services.async_call(domain=domain, service=service, **kwargs)


async def async_execute_ha_actions(
    hass: HomeAssistant, actions: Sequence[Mapping[str, Any]]
) -> None:
    """Execute a validated sequence, stopping at the first failure."""
    await async_require_protection(actions)
    token = set_protection_bypass()
    try:
        for action in actions:
            await _async_call_ha_action_unchecked(
                hass,
                str(action["domain"]),
                str(action["service"]),
                data=action.get("data", {}),
                target=action.get("target", {}),
                blocking=True,
            )
    finally:
        reset_protection_bypass(token)
