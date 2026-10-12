"""Pure execution preparation shared with failed-continuation replay checks."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


def prepare_service_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve native service aliases and target representation before dispatch."""
    result = deepcopy(dict(arguments))
    data = dict(result.get("service_data", result.get("data", {})))
    entity_id = data.get("entity_id", result.get("entity_id"))
    if isinstance(entity_id, str):
        entity_id = [item.strip() for item in entity_id.split(",") if item.strip()]
    if entity_id is not None:
        data["entity_id"] = entity_id
    result.pop("data", None)
    result.pop("entity_id", None)
    result["service_data"] = data
    return result


def prepare_execution_arguments(
    function_config: Mapping[str, Any], arguments: Mapping[str, Any]
) -> dict[str, Any]:
    """Normalize only equivalences implemented by the selected executor.

    JSON Schema defaults are annotations, not executor defaults. Other tools keep
    their arguments intact, and list order remains significant for action batches.
    """
    result = deepcopy(dict(arguments))
    if function_config.get("type") != "native":
        return result
    name = function_config.get("name")
    if name == "execute_service_single":
        return prepare_service_arguments(result)
    if name == "execute_service":
        result["list"] = [
            prepare_service_arguments(item) for item in result.get("list", [])
        ]
    elif name == "send_broadcast":
        result.setdefault("whole_home", False)
    return result
