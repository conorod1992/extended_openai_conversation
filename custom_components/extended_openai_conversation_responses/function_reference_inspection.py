"""Read-only recognition of integration Function/Group action references.

Traversal budgets and ownership/repair policies belong to callers. Records point
at the authored executable action; payload lookalikes are never inspected. No
references are resolved, templates rendered, or source mappings changed here.
"""

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .const import DOMAIN, SERVICE_CALL_FUNCTION
from .script_payload import effective_service_payload, literal_service_name


@dataclass(frozen=True, slots=True)
class FunctionReference:
    """One executable reference and its exact authored mutation location."""

    action: Mapping[str, Any]
    payload: dict[str, Any] | None
    operation: Literal["call", "enable", "disable"]
    field: Literal["function", "functions", "function_groups"]


_SERVICES: dict[
    str,
    tuple[
        Literal["call", "enable", "disable"],
        Literal["function", "functions", "function_groups"],
    ],
] = {
    f"{DOMAIN}.{SERVICE_CALL_FUNCTION}": ("call", "function"),
    f"{DOMAIN}.enable_function_tools": ("enable", "functions"),
    f"{DOMAIN}.disable_function_tools": ("disable", "functions"),
    f"{DOMAIN}.enable_function_groups": ("enable", "function_groups"),
    f"{DOMAIN}.disable_function_groups": ("disable", "function_groups"),
}


def inspect_function_references(
    actions: Iterable[Mapping[str, Any]],
) -> Iterator[FunctionReference]:
    """Recognize the finite service domain in an already bounded action walk.

    Preserve HA selector precedence and case handling. Unknown selectors produce
    no record; known actions with dynamic payloads retain an unknown payload so
    callers can apply their established conservative or validation policy.
    """
    for action in actions:
        service = literal_service_name(action)
        if service is None or (kind := _SERVICES.get(service)) is None:
            continue
        operation, field = kind
        yield FunctionReference(
            action, effective_service_payload(action), operation, field
        )
