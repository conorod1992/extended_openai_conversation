"""Shared OpenAPI numeric constraint normalization without changing optionality."""

from typing import Any

from homeassistant.exceptions import HomeAssistantError


def normalize_exclusive_bounds(schema: dict[str, Any]) -> None:
    """Normalize one schema node; callers retain their own traversal semantics."""
    for exclusive, inclusive in (
        ("exclusiveMinimum", "minimum"),
        ("exclusiveMaximum", "maximum"),
    ):
        # HA serializes OpenAPI's boolean flags. Modern JSON Schema carries
        # the bound itself in the exclusive keyword; numeric bounds stay intact.
        flag = schema.get(exclusive)
        if flag is False:
            schema.pop(exclusive)
        elif flag is True:
            bound = schema.get(inclusive)
            if not isinstance(bound, (int, float)) or isinstance(bound, bool):
                raise HomeAssistantError(
                    f"{exclusive} requires a numeric {inclusive} bound"
                )
            schema[exclusive] = schema.pop(inclusive)
