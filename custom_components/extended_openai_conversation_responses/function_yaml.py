"""Lossless JSON transport for Function collections containing YAML scalar types."""

from copy import deepcopy
from typing import Any

import yaml

from .const import CONF_FUNCTION_TOOLS


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
