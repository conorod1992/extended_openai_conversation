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


def execution_identity(
    function_config: Mapping[str, Any], arguments: Mapping[str, Any]
) -> Any:
    """Compare every child observer without rewriting Composite shared inputs."""
    if function_config.get("type") != "composite":
        return prepare_execution_arguments(function_config, arguments)
    sequence = function_config.get("sequence", [])

    # A response variable is written only *after* its child has executed.
    # Earlier child identities can therefore be normalized safely. From the
    # first assignment onward, retain the original shared input representation:
    # later children may inspect it, including through nested composites.
    identities: list[Any] = []
    response_assigned = False
    for child in sequence:
        identities.append(
            dict(arguments)
            if response_assigned
            else execution_identity(child, arguments)
        )
        if _assigns_response_variable(child):
            response_assigned = True
    return {"composite": identities}


def _assigns_response_variable(config: Mapping[str, Any]) -> bool:
    """Whether this subtree may publish an output to following siblings."""
    if config.get("response_variable"):
        return True
    return config.get("type") == "composite" and any(
        _assigns_response_variable(child) for child in config.get("sequence", [])
    )


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
