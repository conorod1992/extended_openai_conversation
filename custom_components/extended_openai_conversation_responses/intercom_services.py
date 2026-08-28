"""Home Assistant actions for targeted intercom announcements."""

from __future__ import annotations

from typing import Any, cast

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv, selector

from .const import DOMAIN
from .intercom import DEFAULT_TTL_SECONDS, async_get_intercom

SERVICE_BROADCAST = "broadcast"

_TARGET_FIELDS: dict[Any, Any] = {
    vol.Optional("entity_id"): vol.All(cv.ensure_list, [cv.entity_id]),
    vol.Optional("device_id"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("area_id"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("floor_id"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("label_id"): vol.All(cv.ensure_list, [cv.string]),
}

BROADCAST_SCHEMA = vol.Schema(
    {
        vol.Required("message"): cv.string,
        vol.Optional("whole_home", default=False): cv.boolean,
        **_TARGET_FIELDS,
        vol.Optional("origin_entity_id"): cv.entity_id,
        vol.Optional("origin_device_id"): cv.string,
        vol.Optional("ttl_seconds", default=DEFAULT_TTL_SECONDS): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=3600)
        ),
    }
)


async def async_setup_intercom_services(hass: HomeAssistant) -> None:
    """Register the intercom action once."""
    if hass.services.has_service(DOMAIN, SERVICE_BROADCAST):
        return

    async def broadcast(call: ServiceCall) -> ServiceResponse:
        manager = await async_get_intercom(hass)
        result = await manager.async_send(
            call.data["message"],
            whole_home=call.data["whole_home"],
            entity_ids=call.data.get("entity_id"),
            device_ids=call.data.get("device_id"),
            area_ids=call.data.get("area_id"),
            floor_ids=call.data.get("floor_id"),
            label_ids=call.data.get("label_id"),
            origin_entity_id=call.data.get("origin_entity_id"),
            origin_device_id=call.data.get("origin_device_id"),
            source="service",
            ttl_seconds=call.data["ttl_seconds"],
        )
        return cast(ServiceResponse, result)

    hass.services.async_register(
        DOMAIN,
        SERVICE_BROADCAST,
        broadcast,
        schema=BROADCAST_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
