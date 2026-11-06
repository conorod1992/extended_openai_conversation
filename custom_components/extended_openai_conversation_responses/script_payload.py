"""Inspect authored native service payloads without rendering HA templates."""

from collections.abc import Mapping
from typing import Any

from .const import DOMAIN, SERVICE_CALL_FUNCTION


def literal_service_name(action: Mapping[str, Any]) -> str | None:
    """Read the winning service selector using HA's case normalization."""
    name = action.get("action", action.get("service", action.get("service_template")))
    return name.lower() if isinstance(name, str) else None


def is_function_service_action(action: Mapping[str, Any]) -> bool:
    """Recognize a literal Function service name using HA's case normalization.

    Inspect only the action selector; names in payloads and templates are data.
    Keep the authored service spelling and Function name intact.
    """
    return literal_service_name(action) == f"{DOMAIN}.{SERVICE_CALL_FUNCTION}"


def effective_service_payload(action: Mapping[str, Any]) -> dict[str, Any] | None:
    """Match HA's shallow data then data_template update, retaining templates.

    A whole-mapping template has no statically known payload; never infer a
    dependency from the other mapping when that template may override it.
    """
    payload: dict[str, Any] = {}
    for key in ("data", "data_template"):
        if key not in action:
            continue
        value = action[key]
        if not isinstance(value, Mapping):
            return None
        payload.update(value)
    return payload


def set_effective_service_field(action: dict[str, Any], key: str, value: Any) -> None:
    """Edit the winning authored field without flattening or rendering mappings."""
    for field in ("data_template", "data"):
        payload = action.get(field)
        if isinstance(payload, Mapping) and key in payload:
            action[field] = {**payload, key: value}
            return
    field = (
        "data_template" if isinstance(action.get("data_template"), Mapping) else "data"
    )
    action[field] = {**action.get(field, {}), key: value}
