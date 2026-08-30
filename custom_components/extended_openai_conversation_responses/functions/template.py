"""Template tool for Jinja2 rendering."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, llm

from .base import Function


class TemplateFunction(Function):
    def __init__(self) -> None:
        """Initialize template tool."""
        super().__init__(
            vol.Schema(
                {
                    vol.Required("value_template"): cv.template,
                    vol.Optional("parse_result"): bool,
                }
            )
        )

    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        # Template tools historically bypassed the native entity authorization path.
        # Enforce the exposed-entity boundary for the conventional entity_id inputs
        # used by the built-in get_attributes tool and user-defined HA templates.
        entity_ids: list[str] = []
        for key in ("entity_id", "entity_ids"):
            value = arguments.get(key)
            if isinstance(value, str):
                entity_ids.extend(
                    item.strip() for item in value.split(",") if item.strip()
                )
            elif isinstance(value, list):
                entity_ids.extend(str(item) for item in value)
        if entity_ids:
            self.validate_entity_ids(hass, entity_ids, exposed_entities)

        return function_config["value_template"].async_render(
            arguments,
            parse_result=function_config.get("parse_result", False),
        )
