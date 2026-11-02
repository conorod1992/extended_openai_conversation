"""Lossless JSON transport for Function collections containing YAML scalar types."""

from copy import deepcopy
import math
from typing import Any

import yaml

from .const import CONF_FUNCTION_TOOLS
from .yaml_serialization import dump_yaml, load_yaml_or_json

# Reserved only on the Management wire. A literal mapping with this key is itself
# YAML-encoded, so decoding never confuses user-authored data with a scalar tag.
_MANAGEMENT_YAML = "__eoai_management_yaml_v1__"
_MAX_JSON_INTEGER = 2**53 - 1  # JavaScript's lossless integer range.


def _management_yaml_value(value: Any) -> Any:
    """Represent typed Function values reversibly while keeping tools as objects."""
    if value is None or type(value) in (str, bool):
        return value
    if type(value) is int and abs(value) <= _MAX_JSON_INTEGER:
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
    return {_MANAGEMENT_YAML: dump_yaml(value, sort_keys=False)}


def management_function_response(value: Any, *, section: str | None = None) -> Any:
    """Project Function definitions at the WebSocket boundary, never cache/storage.

    Other Management sections retain HA's established JSON representation. Typed
    values inside Functions remain tagged through frontend cloning and subsequent
    edits, instead of becoming strings or breaking a successful save response.
    """
    if section is not None and section not in {
        "configuration",
        "tools",
        "function_repair",
    }:
        return value
    if isinstance(value, dict):
        if "spec" in value and "function" in value:
            return _management_yaml_value(value)
        if section == "function_repair" and value.get("tools") is not None:
            tools = value["tools"]
            value = {
                **value,
                "tools_yaml": tools
                if isinstance(tools, str)
                else dump_yaml(tools, sort_keys=False),
            }
        if isinstance(repair := value.get("function_repair"), dict):
            tools = repair.get("tools")
            if (
                tools is not None
                and repair.get("isolatable") is False
                and repair.get("tools_valid") is not True
            ):
                # Editable source must be generated before wire tagging. Strings
                # submitted by the YAML editor are authored data, never a codec.
                value = {
                    **value,
                    "function_repair": {
                        **repair,
                        "tools_yaml": tools
                        if isinstance(tools, str)
                        else dump_yaml(tools, sort_keys=False),
                    },
                }
        # Portable artifacts already have their own JSON/YAML representation.
        # Applying editor tags inside them would change the exported document
        # (notably literal marker data) and disagree with its serialized JSON.
        return {
            key: item if key == "document" else management_function_response(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [management_function_response(item) for item in value]
    return value


def _decode_management_yaml_value(value: Any) -> Any:
    """Decode wire tags once; literal escaped mappings are not decoded again."""
    if isinstance(value, dict):
        if set(value) == {_MANAGEMENT_YAML} and isinstance(
            value[_MANAGEMENT_YAML], str
        ):
            return yaml.safe_load(value[_MANAGEMENT_YAML])
        return {key: _decode_management_yaml_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_management_yaml_value(item) for item in value]
    return value


def decode_management_function_values(value: Any) -> Any:
    """Decode Function definitions, leaving unrelated Management payloads intact."""
    if isinstance(value, dict):
        if "spec" in value and "function" in value:
            return _decode_management_yaml_value(value)
        return {
            key: decode_management_function_values(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [decode_management_function_values(item) for item in value]
    return value


def decode_management_function_request(message: dict[str, Any]) -> dict[str, Any]:
    """Decode only fields owned by Function editors, never arbitrary action data.

    A rule's event/service payload or a tool's test arguments may legitimately
    resemble a Function definition, including the reserved wire marker.
    """
    section = message.get("section")
    if not isinstance(section, str) or section not in {
        "configuration",
        "tools",
        "function_repair",
    }:
        return message
    result = dict(message)
    if (
        section in {"configuration", "function_repair"}
        and isinstance(config := message.get("config"), dict)
        and CONF_FUNCTION_TOOLS in config
    ):
        result["config"] = {
            **config,
            CONF_FUNCTION_TOOLS: decode_management_function_values(
                config[CONF_FUNCTION_TOOLS]
            ),
        }
    if section in {"tools", "function_repair"}:
        for key in ("tool", "tools"):
            if key in message:
                result[key] = decode_management_function_values(message[key])
    return result


def contains_yaml_types(value: Any) -> bool:
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
            # JSON object keys are strings, including when Python's encoder
            # silently converts integer/bool/null keys rather than rejecting.
            if any(not isinstance(key, str) for key in child):
                return True
            children = iter(child.values())
        elif isinstance(child, (list, tuple)):
            children = iter(child)
        elif (
            not isinstance(child, scalar_types)
            or (type(child) is int and abs(child) > _MAX_JSON_INTEGER)
            or (type(child) is float and not math.isfinite(child))
        ):
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
    if isinstance(tools, list) and contains_yaml_types(tools):
        result[CONF_FUNCTION_TOOLS] = dump_yaml(tools, sort_keys=False)
    return result


def decoded_function_fields(config: Any) -> Any:
    """Expose valid YAML collections to structural redaction and secret matching."""
    if not isinstance(config, dict):
        return config
    tools = config.get(CONF_FUNCTION_TOOLS)
    if not isinstance(tools, str):
        return config
    try:
        decoded = load_yaml_or_json(tools)
    except yaml.YAMLError:
        return config  # Malformed text is retained only for private recovery.
    if not isinstance(decoded, list):
        return config
    result = deepcopy(config)
    result[CONF_FUNCTION_TOOLS] = decoded
    return result
