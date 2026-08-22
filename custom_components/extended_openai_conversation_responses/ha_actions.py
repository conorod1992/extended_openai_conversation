"""Shared Home Assistant action execution seam."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound


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

    A future protected-action/PIN policy can be applied here for both model-driven
    native service calls and administrator-configured local Request Rules.
    """
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
    for action in actions:
        await async_call_ha_action(
            hass,
            str(action["domain"]),
            str(action["service"]),
            data=action.get("data", {}),
            target=action.get("target", {}),
            blocking=True,
        )
