"""Script tool for Home Assistant script sequences."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.script import config as script_config
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.helpers import template as ha_template
from homeassistant.helpers.script import Script

from ..const import DOMAIN
from .base import Function

_LOGGER = logging.getLogger(__name__)


def _compile_template_strings(hass: HomeAssistant, value: Any) -> Any:
    """Compile plain template strings into Template objects.

    Home Assistant's Script runtime renders service-call ``data``/``target``
    values via ``template.async_render_complex``, which only renders
    ``Template`` instances and passes plain strings through unchanged. Normal
    script entities get their sequence validated by
    ``script.async_validate_action_config`` at load time, which performs this
    compilation. Dynamically-built sequences (like configured Function Tools)
    skip that validation, so raw ``{{ ... }}`` strings would otherwise be
    shipped verbatim to the called service instead of being rendered with the
    tool arguments. Compiling the strings here restores rendering.
    """

    if isinstance(value, str) and ("{{" in value or "{%" in value):
        return ha_template.Template(value, hass)
    if isinstance(value, dict):
        return {
            key: _compile_template_strings(hass, item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_compile_template_strings(hass, item) for item in value]
    return value


def _prepare_action(hass: HomeAssistant, action: dict[str, Any]) -> dict[str, Any]:
    """Normalize one raw action config so the Script runtime accepts it.

    Recent Home Assistant versions removed the legacy raw ``service`` /
    ``service_data`` handling from ``async_prepare_call_from_config``; a raw
    legacy key now fails with ``KeyError: 'service_template'``. Normalize to
    the modern keys so stored functions written against older conventions keep
    working.
    """
    prepared = dict(action)
    if "service" in prepared and "action" not in prepared:
        prepared["action"] = prepared.pop("service")
    if "service_data" in prepared and "data" not in prepared:
        prepared["data"] = prepared.pop("service_data")
    if "service_template" in prepared and "action_template" not in prepared:
        prepared["action_template"] = prepared.pop("service_template")
    for key in ("data", "data_template", "target"):
        if key in prepared:
            prepared[key] = _compile_template_strings(hass, prepared[key])
    return prepared


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
        raw_sequence = function_config.get("sequence") or []
        sequence = [
            _prepare_action(hass, action) if isinstance(action, dict) else action
            for action in raw_sequence
        ]
        _LOGGER.debug(
            "Executing script function with run_variables=%r prepared_sequence=%r",
            arguments,
            sequence,
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
