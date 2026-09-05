"""Script tool for Home Assistant script sequences."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.script import config as script_config
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.helpers.script import Script

from ..const import DOMAIN
from .base import Function

_LOGGER = logging.getLogger(__name__)


class ScriptFunction(Function):
    def __init__(self) -> None:
        """Initialize script tool."""
        super().__init__(script_config.SCRIPT_ENTITY_SCHEMA)

    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        script = Script(
            hass,
            function_config["sequence"],
            DOMAIN,
            DOMAIN,
            running_description=f"[{DOMAIN}] function",
            logger=_LOGGER,
        )

        context = llm_context.context if llm_context else None
        try:
            result = await script.async_run(run_variables=arguments, context=context)
            if result is None:
                return "Success"
            return result.variables.get("_function_result", "Success")
        finally:
            await script.async_unload()
