"""Script tool for Home Assistant script sequences."""

from __future__ import annotations

from contextlib import ExitStack
import logging
from typing import Any, cast

from homeassistant.components.script import config as script_config
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm, trace
from homeassistant.helpers.script import Script, async_validate_actions_config

from .. import ha_actions
from ..const import DOMAIN
from .base import Function, copy_runtime_function_config

_LOGGER = logging.getLogger(__name__)


class _AuthorizedScriptServices:
    """Apply EOAI policy only to calls made by this Script and its nested runners."""

    def __init__(
        self,
        hass: HomeAssistant,
        function: Function | None,
        exposed_entities: list[dict[str, Any]],
        internal_services: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        self._hass = hass
        self._function = function
        self._exposed_entities = exposed_entities
        self._internal_services = internal_services

    def __getattr__(self, name: str) -> Any:
        return getattr(self._hass.services, name)

    async def async_call(
        self,
        domain: str,
        service: str,
        service_data: Any = None,
        blocking: bool = False,
        context: Any = None,
        target: Any = None,
        return_response: bool = False,
    ) -> Any:
        # Internal rule guards/dispatchers enforce their own active-execution policy.
        targets = set()
        if (domain, service) not in self._internal_services:
            targets = await ha_actions.async_authorize_ha_action(
                self._hass,
                domain,
                service,
                data=service_data,
                target=target,
                context=context,
            )
        if self._function is not None:
            self._function.validate_entity_ids(
                self._hass, sorted(targets), self._exposed_entities
            )
        # Preserve HA's response variables, blocking and cancellation semantics.
        return await self._hass.services.async_call(
            domain,
            service,
            service_data,
            blocking=blocking,
            context=context,
            target=target,
            return_response=return_response,
        )


class _AuthorizedScriptHass:
    """A Script-local view of HA; no global service method is patched."""

    def __init__(
        self,
        hass: HomeAssistant,
        function: Function | None,
        exposed_entities: list[dict[str, Any]],
        internal_services: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        self._hass = hass
        self.services = _AuthorizedScriptServices(
            hass, function, exposed_entities, internal_services
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._hass, name)


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
            cast(HomeAssistant, _AuthorizedScriptHass(hass, self, exposed_entities)),
            sequence,
            DOMAIN,
            DOMAIN,
            running_description=f"[{DOMAIN}] function",
            logger=_LOGGER,
            variables=function_config.get("variables"),
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
