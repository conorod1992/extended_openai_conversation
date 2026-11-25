"""Bounded presentation of known Home Assistant service calls."""

from collections.abc import Callable
import re
from typing import Any

from homeassistant.const import CONF_EXCLUDE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    floor_registry as fr,
    label_registry as lr,
)

from .execution_arguments import SERVICE_TARGET_KEYS, prepare_service_arguments
from .ha_actions import (
    _resolve_target_entity_ids,
    _target_selection,
    resolve_action_entity_ids,
)

_HA_IDENTIFIER = re.compile(r"[a-z0-9_]{1,64}")
_ENTITY_ID = re.compile(r"[a-z0-9_]{1,64}\.[a-z0-9_]{1,128}")
_MAX_CALLS = 3
_MAX_TARGETS = 3
_MAX_SUMMARY_LENGTH = 480


def _indirect_name(hass: HomeAssistant, key: str, target: str) -> str | None:
    match key:
        case "area_id":
            area = ar.async_get(hass).async_get_area(target)
            kind, name = "area", area.name if area else None
        case "device_id":
            device = dr.async_get(hass).async_get(target)
            kind, name = (
                "device",
                (device.name_by_user or device.name) if device else None,
            )
        case "floor_id":
            floor = fr.async_get(hass).async_get_floor(target)
            kind, name = "floor", floor.name if floor else None
        case "label_id":
            label = lr.async_get(hass).async_get_label(target)
            kind, name = "label", label.name if label else None
        case _:
            return None
    if not name:
        return None
    # Registry names are display data. Keep them on one line and bounded.
    name = " ".join(
        "".join(char if char.isprintable() else " " for char in name).split()
    )
    return f"{kind} {name[:63]}{'…' if len(name) > 63 else ''}" if name else None


def _target_descriptions(
    hass: HomeAssistant,
    domain: str,
    service: str,
    data: dict[str, Any],
    visible: Callable[[list[str]], list[str]],
) -> list[str]:
    selection = _target_selection(data, None)
    targets = [
        (key, target)
        for key in SERVICE_TARGET_KEYS
        for target in selection.get(key, ())
    ]
    shown = []
    omitted = len(targets) > _MAX_TARGETS
    sample = targets[:_MAX_TARGETS]
    participants = (
        resolve_action_entity_ids(hass, domain, service, data)
        if CONF_EXCLUDE in data or any(key != "entity_id" for key, _ in sample)
        else None
    )
    for key, target in sample:
        if not isinstance(target, str):
            omitted = True
            continue
        if key == "entity_id":
            description = (
                target
                if (participants is None or target in participants)
                and visible([target])
                else None
            )
        else:
            # Reveal a registry name only if the selector's entire scope is
            # visible and it contains at least one effective service target.
            entities = _resolve_target_entity_ids(hass, {key: target}, None)
            description = (
                _indirect_name(hass, key, target)
                if participants
                and entities & participants
                and set(visible(sorted(entities))) == entities
                else None
            )
        if description is None:
            omitted = True
        elif description not in shown:
            shown.append(description)
    if omitted:
        shown.append("additional targets omitted")
    return shown


def _parameter_descriptions(
    domain: str, service: str, data: dict[str, Any]
) -> list[str]:
    if (domain, service) == ("light", "turn_on"):
        brightness = data.get("brightness_pct")
        if (
            isinstance(brightness, int | float)
            and not isinstance(brightness, bool)
            and 0 <= brightness <= 100
        ):
            return [f"brightness {brightness:g}%"]
        brightness = data.get("brightness")
        if type(brightness) is int and 0 <= brightness <= 255:
            return [f"brightness {brightness}/255"]
    if (domain, service) == ("climate", "set_temperature"):
        return [
            f"{label} {value:g}"
            for key, label in (
                ("temperature", "temperature"),
                ("target_temp_low", "minimum temperature"),
                ("target_temp_high", "maximum temperature"),
            )
            if isinstance(value := data.get(key), int | float)
            and not isinstance(value, bool)
            and -100 <= value <= 1000
        ]
    return []


def describe_native_action(
    hass: HomeAssistant,
    implementation: str,
    arguments: dict[str, Any],
    visible: Callable[[list[str]], list[str]],
) -> str | None:
    """Describe service calls without copying arbitrary argument values."""
    calls = (
        arguments.get("list") if implementation == "execute_service" else [arguments]
    )
    if not isinstance(calls, list) or not calls:
        return None
    descriptions = []
    for call in calls[:_MAX_CALLS]:
        if not isinstance(call, dict):
            return None
        call = prepare_service_arguments(call)
        domain, service = call.get("domain"), call.get("service")
        if not (
            isinstance(domain, str)
            and _HA_IDENTIFIER.fullmatch(domain)
            and isinstance(service, str)
            and _HA_IDENTIFIER.fullmatch(service)
            and hass.services.has_service(domain, service)
        ):
            return None
        data = call["service_data"]
        details = _target_descriptions(hass, domain, service, data, visible)
        details.extend(_parameter_descriptions(domain, service, data))
        description = f"{domain}.{service}"
        descriptions.append(
            f"{description} ({', '.join(details)})" if details else description
        )
    remainder = f" (+{len(calls) - _MAX_CALLS} more)" if len(calls) > _MAX_CALLS else ""
    summary = "; ".join(descriptions)
    max_length = _MAX_SUMMARY_LENGTH - len(remainder)
    if len(summary) > max_length:
        summary = summary[: max_length - 1] + "…"
    return summary + remainder


def present_entities(hass: HomeAssistant, entities: list[str]) -> list[str]:
    """Keep only well-formed IDs that are present in Home Assistant."""
    return [
        entity
        for entity in entities
        if _ENTITY_ID.fullmatch(entity) and hass.states.get(entity) is not None
    ]
