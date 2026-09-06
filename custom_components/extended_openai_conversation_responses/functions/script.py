"""Script tool for Home Assistant script sequences."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.script import config as script_config
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.helpers.script import Script, async_validate_actions_config

from ..const import DOMAIN
from .base import Function, copy_runtime_function_config

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
        # SCRIPT_ENTITY_SCHEMA performs the static validation and template hydration
        # used by Home Assistant scripts. Complete the normal script-loading contract
        # here with HA-aware validation for dynamic actions such as device actions,
        # conditions, triggers, and nested sequences. Validate an isolated runtime-safe
        # copy because Home Assistant may normalize the sequence in place.
        sequence = await async_validate_actions_config(
            hass,
            copy_runtime_function_config(function_config["sequence"]),
        )
        script = Script(
            hass,
            sequence,
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
