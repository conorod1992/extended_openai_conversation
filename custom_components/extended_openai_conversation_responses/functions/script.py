"""Script tool for Home Assistant script sequences."""

from __future__ import annotations

from contextlib import ExitStack
import logging
from typing import Any

from homeassistant.components.script import config as script_config
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm, trace
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
            # Own the transient runner's outcome without overwriting an enclosing
            # Request Rule's trace, stop reason, variables, or breakpoint identity.
            with ExitStack() as scope:
                outcome = trace.StopReason()
                scope.callback(trace.trace_cv.reset, trace.trace_cv.set({}))
                scope.callback(
                    trace.trace_stack_cv.reset, trace.trace_stack_cv.set(None)
                )
                scope.callback(
                    trace.trace_path_stack_cv.reset, trace.trace_path_stack_cv.set(None)
                )
                scope.callback(trace.variables_cv.reset, trace.variables_cv.set(None))
                scope.callback(trace.trace_id_cv.reset, trace.trace_id_cv.set(None))
                scope.callback(
                    trace.script_execution_cv.reset,
                    trace.script_execution_cv.set(outcome),
                )
                result = await script.async_run(
                    run_variables=arguments, context=context
                )
                if result is None or outcome.script_execution == "cancelled":
                    raise HomeAssistantError("Script Function did not complete")
                if outcome.script_execution == "aborted":
                    # HA deliberately treats a false top-level condition as a
                    # normal stop. Native aborts (timeout/error stop/missing stop
                    # response) instead retain an error on the terminal step.
                    native_trace = trace.trace_get(clear=False) or {}
                    roots = [int(path) for path in native_trace if path.isdecimal()]
                    terminal = max(roots) if roots else None
                    last = (
                        native_trace[str(terminal)][-1].as_dict()
                        if terminal is not None
                        else {}
                    )
                    deliberate_condition = (
                        terminal is not None
                        and "condition" in sequence[terminal]
                        and last.get("result", {}).get("result") is False
                        and "error" not in last
                        and "template_errors" not in last
                    )
                    if not deliberate_condition:
                        raise HomeAssistantError(
                            "Script Function aborted before completion"
                        )
                return result.variables.get("_function_result", "Success")
        finally:
            if unload := getattr(script, "async_unload", None):
                await unload()
            else:
                await script.async_stop()
