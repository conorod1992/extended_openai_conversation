"""Pure execution preparation shared with failed-continuation replay checks."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _ResponseIdentity:
    """Symbolic child output; never passed to an executor or template."""

    path: tuple[int, ...]


def execution_identity(
    function_config: Mapping[str, Any], arguments: Mapping[str, Any]
) -> Any:
    """Compare every child observer without rewriting Composite shared inputs."""

    def identity(
        config: Mapping[str, Any], inputs: Mapping[str, Any], path: tuple[int, ...]
    ) -> Any:
        if config.get("type") != "composite":
            # A response can replace a native data mapping. Its shape is unknown:
            # preserve these inputs conservatively rather than inventing fields.
            if any(
                isinstance(inputs.get(key), _ResponseIdentity)
                for key in ("data", "service_data", "list")
            ):
                return deepcopy(dict(inputs))
            return prepare_execution_arguments(config, inputs)
        shared = deepcopy(dict(inputs))
        identities = []
        for index, child in enumerate(config.get("sequence", [])):
            child_path = (*path, index)
            identities.append(identity(child, shared, child_path))
            if variable := child.get("response_variable"):
                shared[variable] = _ResponseIdentity(child_path)
        # Each nested composite has its own shared-input copy. Only its own
        # response_variable publishes an output to its parent's later siblings.
        return {"composite": identities}

    return identity(function_config, arguments, ())


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
