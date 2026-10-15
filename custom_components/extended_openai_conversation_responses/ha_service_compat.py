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


def _entity_target_identity(
    handler: Any, processed: Any, domain: str, service: str
) -> Any:
    """Normalize only selectors hidden from the downstream service handler."""
    target = handler.job.target
    if not isinstance(processed, Mapping):
        return processed
    generic_power = (
        domain.lower() == "homeassistant"
        and service.lower() in {"turn_on", "turn_off", "toggle"}
        and (getattr(target, "__module__", None), getattr(target, "__qualname__", None))
        in {
            (
                "homeassistant.components.homeassistant",
                "async_setup.<locals>.async_handle_turn_service",
            ),
            (
                "homeassistant.components.homeassistant.services",
                "_async_handle_turn_service",
            ),
        }
    )
    stripped_selectors = isinstance(target, partial) and (
        target.func is getattr(service_helpers, "batched_entity_service_call", None)
        or (
            target.func is service_helpers.entity_service_call
            and isinstance(
                target.args[2] if len(target.args) > 2 else target.keywords.get("func"),
                str,
            )
        )
    )
    if not generic_power and not stripped_selectors:
        return processed
    identity = dict(processed)
    keys = (
        (ATTR_ENTITY_ID,)
        if generic_power
        else (
            ATTR_ENTITY_ID,
            ATTR_AREA_ID,
            ATTR_DEVICE_ID,
            ATTR_FLOOR_ID,
            ATTR_LABEL_ID,
        )
    )
    for key in keys:
        value = identity.get(key)
        if isinstance(value, (list, tuple, set, frozenset)) and all(
            isinstance(item, str) for item in value
        ):
            if value:
                identity[key] = sorted(set(value))
            else:
                identity.pop(key, None)
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
        "data": _entity_target_identity(handler, processed, domain, service),
        "target": {},
    }
