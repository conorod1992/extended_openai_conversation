"""Composite tool for chaining multiple functions."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, llm

from .base import Function

MAX_COMPOSITE_DEPTH = 32
MAX_COMPOSITE_FUNCTIONS = 256


def _validate_resources(config: dict[str, Any]) -> None:
    """Bound the tree before recursive schema copying or any side effect."""
    stack: list[tuple[Any, int, frozenset[int]]] = [(config, 0, frozenset())]
    count = 0
    while stack:
        node, depth, ancestors = stack.pop()
        count += 1
        if depth > MAX_COMPOSITE_DEPTH or count > MAX_COMPOSITE_FUNCTIONS:
            raise HomeAssistantError(
                "Composite function exceeds depth/node safety limits (32 levels, 256 functions)"
            )
        if id(node) in ancestors:
            raise HomeAssistantError(
                "Composite function contains a recursive configuration"
            )
        if not isinstance(node, dict) or node.get("type") != "composite":
            continue
        sequence = node.get("sequence", [])
        sequence = sequence if isinstance(sequence, list) else [sequence]
        if sequence:
            if len(sequence) > MAX_COMPOSITE_FUNCTIONS:
                raise HomeAssistantError(
                    "Composite function exceeds depth/node safety limits (32 levels, 256 functions)"
                )
            stack.extend(
                (child, depth + 1, ancestors | {id(node)}) for child in sequence
            )


class CompositeFunction(Function):
    def __init__(self) -> None:
        """Initialize composite tool."""
        super().__init__(
            vol.Schema(
                {
                    vol.Required("sequence"): vol.All(
                        cv.ensure_list,
                        vol.Length(min=1),
                        [self.function_schema],
                    )
                }
            )
        )

    def validate_schema(self, function_config: dict[str, Any]) -> dict[str, Any]:
        _validate_resources(function_config)
        return super().validate_schema(function_config)

    def function_schema(self, function_config: Any) -> dict[str, Any]:
        """Validate a composite function schema."""
        from . import get_function

        if not isinstance(function_config, dict):
            raise vol.Invalid("expected dictionary")

        composite_schema = {vol.Optional("response_variable"): str}
        function = get_function(function_config["type"])

        return dict(function.data_schema.extend(composite_schema)(function_config))

    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        from . import get_function

        _validate_resources(function_config)
        sequence = function_config["sequence"]
        if not sequence:
            raise HomeAssistantError(
                "Composite function sequence must contain at least one function"
            )

        new_arguments = arguments.copy()
        result: Any = None

        for next_function_config in sequence:
            next_function = get_function(next_function_config["type"])
            result = await next_function.execute(
                hass, next_function_config, new_arguments, llm_context, exposed_entities
            )

            response_variable = next_function_config.get("response_variable")
            if response_variable:
                new_arguments[response_variable] = result

        return result
