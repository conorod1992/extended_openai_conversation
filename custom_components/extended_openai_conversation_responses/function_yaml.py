"""Lossless JSON transport for Function collections containing YAML scalar types."""

from copy import deepcopy
import math
from typing import Any

import yaml

from .const import CONF_FUNCTION_TOOLS

# Reserved only on the Management wire. A literal mapping with this key is itself
# YAML-encoded, so decoding never confuses user-authored data with a scalar tag.
_MANAGEMENT_YAML = "__eoai_management_yaml_v1__"


def _management_yaml_value(value: Any) -> Any:
    """Represent typed Function values reversibly while keeping tools as objects."""
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, list):
        return [_management_yaml_value(item) for item in value]
    if (
        isinstance(value, dict)
        and all(isinstance(key, str) for key in value)
        and set(value) != {_MANAGEMENT_YAML}
    ):
        return {key: _management_yaml_value(item) for key, item in value.items()}
    return {
        _MANAGEMENT_YAML: yaml.safe_dump(value, sort_keys=False, allow_unicode=True)
    }


def management_function_response(value: Any) -> Any:
    """Project Function definitions at the WebSocket boundary, never cache/storage.

    Other Management sections retain HA's established JSON representation. Typed
    values inside Functions remain tagged through frontend cloning and subsequent
    edits, instead of becoming strings or breaking a successful save response.
    """
    if isinstance(value, dict):
        if "spec" in value and "function" in value:
            return _management_yaml_value(value)
        return {key: management_function_response(item) for key, item in value.items()}
    if isinstance(value, list):
        return [management_function_response(item) for item in value]
    return value


def decode_management_function_values(value: Any) -> Any:
    """Decode wire tags once; literal escaped mappings are not decoded again."""
    if isinstance(value, dict):
        if set(value) == {_MANAGEMENT_YAML} and isinstance(
            value[_MANAGEMENT_YAML], str
        ):
            return yaml.safe_load(value[_MANAGEMENT_YAML])
        return {
            key: decode_management_function_values(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [decode_management_function_values(item) for item in value]
    return value


def _contains_yaml_types(value: Any) -> bool:
    """Inspect types without encoding scalar values ahead of transport budgets.

    Visit shared containers once, using depth-sized iterator storage. Export's
    existing graph/size checks remain responsible for rejecting cyclic inputs.
    """
    pending = [iter((value,))]
    seen: set[int] = set()
    scalar_types = (str, int, float, bool, type(None))
    while pending:
        try:
            child = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        if isinstance(child, dict):
            if any(not isinstance(key, scalar_types) for key in child):
                return True
            children = iter(child.values())
        elif isinstance(child, (list, tuple)):
            children = iter(child)
        elif not isinstance(child, scalar_types):
            return True
        else:
            continue
        if id(child) not in seen:
            seen.add(id(child))
            pending.append(children)
    return False


def json_safe_function_fields(config: dict[str, Any]) -> dict[str, Any]:
    """Keep ordinary collections structured; transport typed YAML as YAML text.

    The existing Function field accepts both forms. Unlike default=str, YAML
    preserves dates, timestamps and binary values, including their distinction
    from identically spelled strings. Callers must redact before encoding.
    """
    result = dict(config)
    tools = result.get(CONF_FUNCTION_TOOLS)
    if isinstance(tools, list) and _contains_yaml_types(tools):
        result[CONF_FUNCTION_TOOLS] = yaml.safe_dump(
            tools, sort_keys=False, allow_unicode=True
        )
    return result


def decoded_function_fields(config: Any) -> Any:
    """Expose valid YAML collections to structural redaction and secret matching."""
    if not isinstance(config, dict):
        return config
    tools = config.get(CONF_FUNCTION_TOOLS)
    if not isinstance(tools, str):
        return config
    try:
        decoded = yaml.safe_load(tools)
    except yaml.YAMLError:
        return config  # Malformed text is retained only for private recovery.
    if not isinstance(decoded, list):
        return config
    result = deepcopy(config)
    result[CONF_FUNCTION_TOOLS] = decoded
    return result
