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
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
from homeassistant.helpers import service as service_helpers

from .ha_schema import schema_for_ha


def _entity_dispatcher(target: Any) -> str | None:
    """Only Core dispatchers with selector-stripping contracts are precise."""
    if not isinstance(target, partial):
        return None
    if target.func is getattr(service_helpers, "batched_entity_service_call", None):
        return "batched"
    func = target.args[2] if len(target.args) > 2 else target.keywords.get("func")
    if target.func is service_helpers.entity_service_call and isinstance(func, str):
        return "entity"
    return None


def _generic_power(target: Any, domain: str, service: str) -> bool:
    return (
        domain == "homeassistant"
        and service in {"turn_on", "turn_off", "toggle"}
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


async def _entity_target_identity(
    hass: HomeAssistant,
    handler: Any,
    processed: Any,
    domain: str,
    service: str,
    context: Any,
) -> tuple[Any, dict[str, Any]]:
    """Resolve complete participant sets, retaining callable handler semantics."""
    target = handler.job.target
    dispatcher = _entity_dispatcher(target)
    generic = _generic_power(target, domain, service)
    if not isinstance(processed, Mapping) or not (generic or dispatcher):
        return processed, {}
    from .ha_actions import (
        _resolve_target_entity_ids,
        explicit_service_refusal_entity_ids,
        resolve_action_entity_ids,
        service_target_entity_ids,
    )

    selected = (
        _resolve_target_entity_ids(hass, processed, None)
        if generic
        else resolve_action_entity_ids(hass, domain, service, processed)
    )
    if generic:
        # Core forwards selector data to its delegates. A callable delegate can
        # observe that representation, so it must keep the existing raw identity.
        domains = {entity_id.split(".", 1)[0] for entity_id in selected}
        if any(
            (delegate := hass.services.async_services_for_domain(item).get(service))
            is None
            or _entity_dispatcher(delegate.job.target) != "entity"
            for item in domains
        ):
            identity = dict(processed)
            if isinstance(ids := identity.get(ATTR_ENTITY_ID), list):
                identity[ATTR_ENTITY_ID] = sorted(set(ids))
            return identity, {}
    # Indirect nonparticipants are skipped, but an explicit feature/device-class
    # refusal makes the entire entity call different from its valid correction.
    explicit = processed.get(ATTR_ENTITY_ID, [])
    explicit = {explicit} if isinstance(explicit, str) else set(explicit)
    refused = set()
    for entity_domain in domains if generic else (domain,):
        refused.update(
            # Core turns even indirect generic selections into explicit delegate
            # IDs, unlike a direct domain service's own area/device selection.
            explicit_service_refusal_entity_ids(
                hass, entity_domain, service, selected if generic else explicit
            )
        )
    selected = (
        service_target_entity_ids(hass, domain, service, selected, explicit=set())
        | refused
    )
    if user_id := getattr(context, "user_id", None):
        user = await hass.auth.async_get_user(user_id)
        if user is None:
            return object(), {}  # Unknown ownership cannot establish a distinct effect.
        if not user.is_admin:
            from homeassistant.auth.permissions.const import POLICY_CONTROL

            selected = {
                item
                for item in selected
                if user.permissions.check_entity(item, POLICY_CONTROL)
            }
    if generic and refused:
        refusing_domains = {item.split(".", 1)[0] for item in refused}
        if any(item.split(".", 1)[0] not in refusing_domains for item in selected):
            # Core runs delegates concurrently: another domain may already have
            # acted. A changed target must not evade that uncertain partial effect.
            # Unidentifiable preparation retains the existing fail-closed receipt
            # contract until a successful acknowledgment clears the conversation.
            return object(), {}
    identity = {
        key: value
        for key, value in processed.items()
        if key
        not in {
            ATTR_ENTITY_ID,
            ATTR_AREA_ID,
            ATTR_DEVICE_ID,
            ATTR_FLOOR_ID,
            ATTR_LABEL_ID,
        }
    }
    identity[ATTR_ENTITY_ID] = sorted(selected)
    power = {}
    if generic or (
        dispatcher == "entity" and service in {"turn_on", "turn_off", "toggle"}
    ):
        # Opposite dispatcher families are conservatively cross-checked by full
        # target set. Their schemas/defaults may differ, so never collapse all
        # same-family calls or arbitrary service aliases.
        power = {
            "side": "generic" if generic else "entity",
            "service": service,
            "entities": sorted(selected),
        }
    return identity, power


async def prepare_service_dispatch(
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
    handler = registry.async_services_for_domain(domain.lower()).get(service.lower())
    if handler is None:
        # Script callers rely on HA's recoverable error contract as well as
        # native callers; a missing registration must not escape as KeyError.
        raise ServiceNotFound(domain, service)
    raw = dict(kwargs["service_data"])
    raw.update(kwargs.get("target", {}))
    processed = handler.schema(raw) if handler.schema is not None else raw
    identity, power = await _entity_target_identity(
        hass, handler, processed, domain.lower(), service.lower(), kwargs.get("context")
    )
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
        "data": identity,
        "target": {},
        **({"_power_recovery": power} if power else {}),
    }
