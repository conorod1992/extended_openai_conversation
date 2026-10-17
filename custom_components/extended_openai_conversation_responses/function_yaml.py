"""Lossless JSON transport for Function collections containing YAML scalar types."""

from copy import deepcopy
import json
from typing import Any

import yaml

from .const import CONF_FUNCTION_TOOLS


def json_safe_function_fields(config: dict[str, Any]) -> dict[str, Any]:
    """Keep ordinary collections structured; transport typed YAML as YAML text.

    The existing Function field accepts both forms. Unlike default=str, YAML
    preserves dates, timestamps and binary values, including their distinction
    from identically spelled strings. Callers must redact before encoding.
    """
    result = dict(config)
    tools = result.get(CONF_FUNCTION_TOOLS)
    if isinstance(tools, list):
        try:
            json.dumps(tools)
        except TypeError, ValueError:
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
