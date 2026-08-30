"""Shared configured-function argument validation."""

from __future__ import annotations

from collections.abc import Mapping
import math
import re
from typing import Any

from homeassistant.exceptions import HomeAssistantError

_JSON_TYPES = {"array", "boolean", "integer", "null", "number", "object", "string"}
_OBJECT_KEYWORDS = {
    "properties",
    "required",
    "additionalProperties",
    "minProperties",
    "maxProperties",
}
_ARRAY_KEYWORDS = {"items", "minItems", "maxItems", "uniqueItems"}


def validate_function_arguments(
    spec: Mapping[str, Any], arguments: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate configured-function arguments against their JSON schema."""
    schema = spec.get("parameters", {})
    if not isinstance(schema, Mapping):
        raise HomeAssistantError("Function input schema is invalid")
    if not isinstance(arguments, Mapping):
        raise HomeAssistantError("Function input must be an object")

    result = _validate_value("", dict(arguments), schema)
    if not isinstance(result, Mapping):
        raise HomeAssistantError("Function input schema must describe an object")
    return dict(result)


def _field_name(parent: str, child: str) -> str:
    """Build a readable dotted input path."""
    return f"{parent}.{child}" if parent else child


def _schema_error(message: str) -> HomeAssistantError:
    """Return a consistent invalid-schema error."""
    return HomeAssistantError(f"Function input schema is invalid: {message}")


def _type_error(name: str, expected: str | list[str] | None) -> HomeAssistantError:
    """Return a consistent input type error."""
    if isinstance(expected, list):
        expected_text = " or ".join(expected)
    else:
        expected_text = expected or "valid"
    label = name or "input"
    return HomeAssistantError(f"Function input `{label}` must be {expected_text}")


def _validate_expected_type(name: str, value: Any, expected: str) -> Any:
    """Validate and preserve the integration's safe scalar coercions."""
    try:
        if expected == "string":
            if not isinstance(value, str):
                raise ValueError
        elif expected == "number":
            if isinstance(value, str):
                value = float(value.strip())
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError
            if not math.isfinite(float(value)):
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
        elif expected == "object":
            if not isinstance(value, Mapping):
                raise ValueError
            value = dict(value)
        elif expected == "null":
            if value is not None:
                raise ValueError
        else:
            raise _schema_error(f"unsupported type `{expected}`")
    except (OverflowError, TypeError, ValueError) as err:
        raise _type_error(name, expected) from err
    return value


def _validate_type(name: str, value: Any, expected: Any) -> Any:
    """Validate a JSON-schema type declaration, including nullable unions."""
    if expected is None:
        return value
    if isinstance(expected, str):
        if expected not in _JSON_TYPES:
            raise _schema_error(f"unsupported type `{expected}`")
        return _validate_expected_type(name, value, expected)
    if (
        not isinstance(expected, list)
        or not expected
        or not all(isinstance(item, str) and item in _JSON_TYPES for item in expected)
    ):
        raise _schema_error("type must be a JSON type or non-empty list of JSON types")

    for candidate in expected:
        try:
            return _validate_expected_type(name, value, candidate)
        except HomeAssistantError as err:
            if str(err).startswith("Function input schema is invalid"):
                raise
    raise _type_error(name, expected)


def _validate_number_constraints(
    name: str, value: int | float, schema: Mapping[str, Any]
) -> None:
    """Validate numeric JSON-schema bounds."""
    checks = (
        ("minimum", lambda actual, limit: actual >= limit, "at least"),
        ("maximum", lambda actual, limit: actual <= limit, "at most"),
        ("exclusiveMinimum", lambda actual, limit: actual > limit, "greater than"),
        ("exclusiveMaximum", lambda actual, limit: actual < limit, "less than"),
    )
    for keyword, predicate, wording in checks:
        if keyword not in schema:
            continue
        limit = schema[keyword]
        if isinstance(limit, bool) or not isinstance(limit, (int, float)):
            raise _schema_error(f"{keyword} must be numeric")
        if not predicate(value, limit):
            raise HomeAssistantError(
                f"Function input `{name or 'input'}` must be {wording} {limit}"
            )


def _validate_length_constraint(
    name: str,
    value: str | list[Any] | Mapping[str, Any],
    schema: Mapping[str, Any],
    minimum_key: str,
    maximum_key: str,
    noun: str,
) -> None:
    """Validate common string/array/object length constraints."""
    minimum = schema.get(minimum_key)
    maximum = schema.get(maximum_key)
    for keyword, limit in ((minimum_key, minimum), (maximum_key, maximum)):
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 0
        ):
            raise _schema_error(f"{keyword} must be a non-negative integer")
    if minimum is not None and len(value) < minimum:
        raise HomeAssistantError(
            f"Function input `{name or 'input'}` must contain at least {minimum} {noun}"
        )
    if maximum is not None and len(value) > maximum:
        raise HomeAssistantError(
            f"Function input `{name or 'input'}` must contain at most {maximum} {noun}"
        )


def _validate_object(
    name: str, value: Mapping[str, Any], schema: Mapping[str, Any]
) -> dict[str, Any]:
    """Recursively validate an object schema."""
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise _schema_error("properties must be an object")
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(
        isinstance(item, str) for item in required
    ):
        raise _schema_error("required must be a list of property names")

    missing = [_field_name(name, field) for field in required if field not in value]
    if missing:
        raise HomeAssistantError(
            "Missing required function input: " + ", ".join(sorted(missing))
        )

    additional = schema.get("additionalProperties", True)
    if not isinstance(additional, (bool, Mapping)):
        raise _schema_error("additionalProperties must be boolean or an object schema")

    result: dict[str, Any] = {}
    unknown: list[str] = []
    for key, item in value.items():
        if not isinstance(key, str):
            raise HomeAssistantError("Function input object keys must be strings")
        child_name = _field_name(name, key)
        child_schema = properties.get(key)
        if child_schema is None:
            if additional is False:
                unknown.append(child_name)
                continue
            if isinstance(additional, Mapping):
                result[key] = _validate_value(child_name, item, additional)
            else:
                result[key] = item
            continue
        if not isinstance(child_schema, Mapping):
            raise _schema_error(f"schema for `{child_name}` must be an object")
        result[key] = _validate_value(child_name, item, child_schema)

    if unknown:
        raise HomeAssistantError(
            "Unknown function input: " + ", ".join(sorted(unknown))
        )

    _validate_length_constraint(
        name,
        result,
        schema,
        "minProperties",
        "maxProperties",
        "properties",
    )
    return result


def _validate_value(name: str, value: Any, schema: Mapping[str, Any]) -> Any:
    """Recursively validate one JSON-schema value."""
    expected = schema.get("type")
    if expected is None:
        if _OBJECT_KEYWORDS.intersection(schema):
            expected = "object"
        elif _ARRAY_KEYWORDS.intersection(schema):
            expected = "array"

    value = _validate_type(name, value, expected)

    # Type unions are uncommon in tool specs. Once one member has matched, apply
    # constraints according to the resulting Python value as well as explicit type.
    expected_types = {expected} if isinstance(expected, str) else set(expected or [])

    if "object" in expected_types and isinstance(value, Mapping):
        value = _validate_object(name, value, schema)
    elif "array" in expected_types and isinstance(value, list):
        _validate_length_constraint(
            name, value, schema, "minItems", "maxItems", "items"
        )
        if schema.get("uniqueItems") is True:
            for index, item in enumerate(value):
                if item in value[:index]:
                    raise HomeAssistantError(
                        f"Function input `{name or 'input'}` must contain unique items"
                    )
        elif "uniqueItems" in schema and schema.get("uniqueItems") is not False:
            raise _schema_error("uniqueItems must be boolean")
        items = schema.get("items")
        if items is not None:
            if not isinstance(items, Mapping):
                raise _schema_error(
                    f"items for `{name or 'input'}` must be an object schema"
                )
            value = [
                _validate_value(f"{name or 'input'}[{index}]", item, items)
                for index, item in enumerate(value)
            ]
    elif "string" in expected_types and isinstance(value, str):
        _validate_length_constraint(
            name, value, schema, "minLength", "maxLength", "characters"
        )
        pattern = schema.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str):
                raise _schema_error("pattern must be a string")
            try:
                matches = re.search(pattern, value) is not None
            except re.error as err:
                raise _schema_error(f"invalid pattern for `{name or 'input'}`") from err
            if not matches:
                raise HomeAssistantError(
                    f"Function input `{name or 'input'}` does not match its required pattern"
                )
    elif (
        expected_types.intersection({"number", "integer"})
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        _validate_number_constraints(name, value, schema)

    choices = schema.get("enum")
    if choices is not None:
        if not isinstance(choices, list):
            raise _schema_error("enum must be a list")
        if value not in choices:
            raise HomeAssistantError(
                f"Function input `{name or 'input'}` must be one of its choices"
            )

    if "const" in schema and value != schema["const"]:
        raise HomeAssistantError(
            f"Function input `{name or 'input'}` must match its required value"
        )

    return value
