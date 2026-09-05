"""Shared configured-function argument validation."""

from __future__ import annotations

from collections.abc import Mapping
import math
import re
from typing import Any

from homeassistant.core import HomeAssistant
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
_STRING_KEYWORDS = {"minLength", "maxLength", "pattern"}
_NUMBER_KEYWORDS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}
_COMMON_SCHEMA_KEYWORDS = {"type", "description", "enum", "const", "default"}
_SUPPORTED_SCHEMA_KEYWORDS = (
    _COMMON_SCHEMA_KEYWORDS
    | _OBJECT_KEYWORDS
    | _ARRAY_KEYWORDS
    | _STRING_KEYWORDS
    | _NUMBER_KEYWORDS
)
_LEGACY_DELAY_FIELDS = frozenset({"hours", "minutes", "seconds"})


def validate_function_schema(schema: Mapping[str, Any]) -> None:
    """Validate the JSON-schema subset enforced by configured Function Tools.

    The provider may understand a wider JSON-Schema vocabulary, but configured tools
    are also validated locally before execution. Rejecting unsupported constraints at
    configuration time prevents the provider and the local runtime from disagreeing
    about what inputs are valid.
    """
    if not isinstance(schema, Mapping):
        raise _schema_error("parameters must be an object schema")
    _validate_schema_node("parameters", schema)
    root_type = schema.get("type")
    if root_type is not None and root_type != "object":
        raise _schema_error("function parameters must describe an object")


def _validate_schema_node(path: str, schema: Mapping[str, Any]) -> None:
    """Validate one schema node recursively without validating a concrete value."""
    unknown = set(schema) - _SUPPORTED_SCHEMA_KEYWORDS
    if unknown:
        raise _schema_error(
            f"unsupported keyword at `{path}`: {', '.join(sorted(unknown))}"
        )

    description = schema.get("description")
    if description is not None and not isinstance(description, str):
        raise _schema_error(f"description at `{path}` must be a string")

    expected = schema.get("type")
    if expected is None:
        object_hint = bool(_OBJECT_KEYWORDS.intersection(schema))
        array_hint = bool(_ARRAY_KEYWORDS.intersection(schema))
        if object_hint and array_hint:
            raise _schema_error(f"schema at `{path}` mixes object and array keywords")
        expected_types = (
            {"object"} if object_hint else {"array"} if array_hint else set()
        )
    elif isinstance(expected, str):
        if expected not in _JSON_TYPES:
            raise _schema_error(f"unsupported type `{expected}` at `{path}`")
        expected_types = {expected}
    elif (
        isinstance(expected, list)
        and expected
        and all(isinstance(item, str) and item in _JSON_TYPES for item in expected)
    ):
        expected_types = set(expected)
    else:
        raise _schema_error(
            f"type at `{path}` must be a JSON type or non-empty list of JSON types"
        )

    for keywords, supported_types, label in (
        (_OBJECT_KEYWORDS, {"object"}, "object"),
        (_ARRAY_KEYWORDS, {"array"}, "array"),
        (_STRING_KEYWORDS, {"string"}, "string"),
        (_NUMBER_KEYWORDS, {"number", "integer"}, "numeric"),
    ):
        used = keywords.intersection(schema)
        if used and not expected_types.intersection(supported_types):
            raise _schema_error(
                f"{', '.join(sorted(used))} at `{path}` requires a {label} schema"
            )

    choices = schema.get("enum")
    if choices is not None and not isinstance(choices, list):
        raise _schema_error(f"enum at `{path}` must be a list")

    if "object" in expected_types:
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise _schema_error(f"properties at `{path}` must be an object")
        for name, child_schema in properties.items():
            if not isinstance(name, str):
                raise _schema_error(f"property names at `{path}` must be strings")
            if not isinstance(child_schema, Mapping):
                raise _schema_error(
                    f"schema for `{_field_name(path, name)}` must be an object"
                )
            _validate_schema_node(_field_name(path, name), child_schema)

        required = schema.get("required", [])
        if not isinstance(required, list) or not all(
            isinstance(item, str) for item in required
        ):
            raise _schema_error(
                f"required at `{path}` must be a list of property names"
            )
        if len(required) != len(set(required)):
            raise _schema_error(f"required at `{path}` contains duplicate names")

        additional = schema.get("additionalProperties", True)
        if not isinstance(additional, (bool, Mapping)):
            raise _schema_error(
                f"additionalProperties at `{path}` must be boolean or an object schema"
            )
        if isinstance(additional, Mapping):
            _validate_schema_node(f"{path}.additionalProperties", additional)
        _validate_schema_length_bounds(schema, path, "minProperties", "maxProperties")

    if "array" in expected_types:
        items = schema.get("items")
        if items is not None:
            if not isinstance(items, Mapping):
                raise _schema_error(f"items at `{path}` must be an object schema")
            _validate_schema_node(f"{path}.items", items)
        unique = schema.get("uniqueItems")
        if unique is not None and not isinstance(unique, bool):
            raise _schema_error(f"uniqueItems at `{path}` must be boolean")
        _validate_schema_length_bounds(schema, path, "minItems", "maxItems")

    if "string" in expected_types:
        _validate_schema_length_bounds(schema, path, "minLength", "maxLength")
        pattern = schema.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str):
                raise _schema_error(f"pattern at `{path}` must be a string")
            try:
                re.compile(pattern)
            except re.error as err:
                raise _schema_error(f"invalid pattern at `{path}`: {err}") from err

    if expected_types.intersection({"number", "integer"}):
        for keyword in _NUMBER_KEYWORDS:
            if keyword not in schema:
                continue
            limit = schema[keyword]
            if (
                isinstance(limit, bool)
                or not isinstance(limit, (int, float))
                or not math.isfinite(float(limit))
            ):
                raise _schema_error(f"{keyword} at `{path}` must be a finite number")
        _validate_numeric_bound_order(schema, path)


def _validate_schema_length_bounds(
    schema: Mapping[str, Any], path: str, minimum_key: str, maximum_key: str
) -> None:
    """Validate non-negative integer schema bounds and their ordering."""
    minimum = schema.get(minimum_key)
    maximum = schema.get(maximum_key)
    for keyword, limit in ((minimum_key, minimum), (maximum_key, maximum)):
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 0
        ):
            raise _schema_error(f"{keyword} at `{path}` must be a non-negative integer")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise _schema_error(f"{minimum_key} at `{path}` cannot exceed {maximum_key}")


def _validate_numeric_bound_order(schema: Mapping[str, Any], path: str) -> None:
    """Reject contradictory numeric lower/upper bounds."""
    lower = schema.get("minimum", schema.get("exclusiveMinimum"))
    upper = schema.get("maximum", schema.get("exclusiveMaximum"))
    if lower is not None and upper is not None and lower > upper:
        raise _schema_error(f"numeric lower bound at `{path}` exceeds upper bound")


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


async def async_validate_function_arguments(
    hass: HomeAssistant,
    spec: Mapping[str, Any],
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate Function Tool arguments away from Home Assistant's event loop."""
    return await hass.async_add_executor_job(
        validate_function_arguments, spec, arguments
    )


def _is_legacy_delay_schema(schema: object) -> bool:
    """Return whether a parameter matches the documented legacy delay contract."""
    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        return False
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        return False
    if not set(properties).issubset(_LEGACY_DELAY_FIELDS):
        return False
    return all(
        isinstance(child, Mapping) and child.get("type") in {"integer", "number"}
        for child in properties.values()
    )


def split_legacy_execution_delay(
    spec: Mapping[str, Any], arguments: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Separate documented legacy scheduling metadata from real function arguments.

    Historically any argument named ``delay`` was consumed as integration scheduling
    metadata. Preserve that behavior only for the documented object-shaped delay
    schema (hours/minutes/seconds). Other parameters literally named ``delay`` remain
    part of the configured Function Tool's argument namespace.
    """
    execution_arguments = dict(arguments)
    parameters = spec.get("parameters")
    if not isinstance(parameters, Mapping):
        return execution_arguments, None
    properties = parameters.get("properties")
    if not isinstance(properties, Mapping):
        return execution_arguments, None
    delay_schema = properties.get("delay")
    delay_value = execution_arguments.get("delay")
    if not _is_legacy_delay_schema(delay_schema) or not isinstance(
        delay_value, Mapping
    ):
        return execution_arguments, None

    execution_arguments.pop("delay", None)
    return execution_arguments, dict(delay_value)


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
