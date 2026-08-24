"""Shared configured-function argument validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.exceptions import HomeAssistantError


def validate_function_arguments(
    spec: Mapping[str, Any], arguments: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate common JSON-schema arguments for every configured-function caller."""
    schema = spec.get("parameters", {})
    if not isinstance(schema, Mapping):
        raise HomeAssistantError("Function input schema is invalid")
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise HomeAssistantError("Function input properties are invalid")
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise HomeAssistantError("Function required inputs are invalid")
    missing = [name for name in required if name not in arguments]
    if missing:
        raise HomeAssistantError(
            "Missing required function input: " + ", ".join(sorted(missing))
        )
    if schema.get("additionalProperties") is False:
        unknown = set(arguments) - set(properties)
        if unknown:
            raise HomeAssistantError(
                "Unknown function input: " + ", ".join(sorted(unknown))
            )
    result: dict[str, Any] = {}
    for name, value in arguments.items():
        field = properties.get(name, {})
        if not isinstance(field, Mapping):
            field = {}
        result[name] = _validate_value(name, value, field)
    return result


def _validate_value(name: str, value: Any, schema: Mapping[str, Any]) -> Any:
    expected = schema.get("type")
    try:
        if expected == "string":
            if not isinstance(value, str):
                raise ValueError
        elif expected == "number":
            if isinstance(value, str):
                value = float(value.strip())
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError
        elif expected == "integer":
            if isinstance(value, str):
                value = int(value.strip())
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError
        elif expected == "boolean":
            if isinstance(value, str) and value.casefold() in {"true", "false"}:
                value = value.casefold() == "true"
            if not isinstance(value, bool):
                raise ValueError
        elif expected == "array":
            if isinstance(value, str):
                value = [item.strip() for item in value.split(",") if item.strip()]
            if not isinstance(value, list):
                raise ValueError
            items = schema.get("items", {})
            if isinstance(items, Mapping):
                value = [_validate_value(f"{name} item", item, items) for item in value]
        elif expected == "object" and not isinstance(value, Mapping):
            raise ValueError
    except (TypeError, ValueError) as err:
        raise HomeAssistantError(
            f"Function input `{name}` must be {expected or 'valid'}"
        ) from err
    choices = schema.get("enum")
    if isinstance(choices, list) and value not in choices:
        raise HomeAssistantError(f"Function input `{name}` must be one of its choices")
    return value
