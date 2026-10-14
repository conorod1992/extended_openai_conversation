"""Prepare genuine HA services once without changing the global registry.

HA has no public prepared-call dispatch API. The sole private dependency here
installs a copied registration into a call-local registry, preserving HA's job,
events, context and response handling. Adapters use their own dispatch path and
remain opaque to replay matching. No traceback or frame inspection is needed:
validation finishes before a receipt starts; later failures cannot prove refusal.
"""

from collections.abc import Mapping
from copy import copy
from functools import partial
from typing import Any

from homeassistant.const import (
    ATTR_AREA_ID,
    ATTR_DEVICE_ID,
    ATTR_ENTITY_ID,
    ATTR_FLOOR_ID,
    ATTR_LABEL_ID,
)
from homeassistant.core import HomeAssistant, ServiceRegistry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import service as service_helpers

from .ha_schema import schema_for_ha


def _entity_target_identity(handler: Any, processed: Any) -> Any:
    """Match HA entity selectors as sets without changing dispatch or payloads."""
    target = handler.job.target
    if (
        not isinstance(target, partial)
        or target.func
        not in (
            service_helpers.entity_service_call,
            getattr(service_helpers, "batched_entity_service_call", None),
        )
        or not isinstance(processed, Mapping)
    ):
        return processed
    identity = dict(processed)
    for key in (
        ATTR_ENTITY_ID,
        ATTR_AREA_ID,
        ATTR_DEVICE_ID,
        ATTR_FLOOR_ID,
        ATTR_LABEL_ID,
    ):
        value = identity.get(key)
        if isinstance(value, (list, tuple, set, frozenset)) and all(
            isinstance(item, str) for item in value
        ):
            identity[key] = sorted(set(value))
    return identity


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
    return prepared_registry, {
        "data": _entity_target_identity(handler, processed),
        "target": {},
    }
