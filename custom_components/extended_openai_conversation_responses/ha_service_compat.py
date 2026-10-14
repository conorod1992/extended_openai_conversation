"""Prepare genuine HA services once without changing the global registry.

HA has no public prepared-call dispatch API. The sole private dependency here
installs a copied registration into a call-local registry, preserving HA's job,
events, context and response handling. Adapters use their own dispatch path and
remain opaque to replay matching. No traceback or frame inspection is needed:
validation finishes before a receipt starts; later failures cannot prove refusal.
"""

from copy import copy
from typing import Any

from homeassistant.core import HomeAssistant, ServiceRegistry
from homeassistant.exceptions import HomeAssistantError

from .ha_schema import schema_for_ha


def prepare_service_dispatch(
    hass: HomeAssistant, domain: str, service: str, kwargs: dict[str, Any]
) -> tuple[Any, dict[str, Any]]:
    """Return a prepared local registry, or the original opaque adapter."""
    registry = hass.services
    if (
        type(registry) is not ServiceRegistry
        or getattr(registry.async_call, "__func__", None)
        is not ServiceRegistry.async_call
    ):
        return registry, {
            "data": dict(kwargs["service_data"]),
            "target": dict(kwargs.get("target", {})),
        }
    handler = registry.async_services_for_domain(domain.lower())[service.lower()]
    raw = dict(kwargs["service_data"])
    raw.update(kwargs.get("target", {}))
    processed = handler.schema(raw) if handler.schema is not None else raw
    if (
        registry.async_services_for_domain(domain.lower()).get(service.lower())
        is not handler
    ):
        raise HomeAssistantError(
            "Home Assistant service changed during preparation; retry"
        )
    prepared_handler = copy(handler)
    prepared_handler.schema = schema_for_ha(lambda _data: processed)
    prepared_registry = ServiceRegistry(hass)
    prepared_registry._services = {domain.lower(): {service.lower(): prepared_handler}}
    # HA's service event contains raw input, including validator mutations.
    kwargs["service_data"] = raw
    kwargs.pop("target", None)
    return prepared_registry, {"data": processed, "target": {}}
