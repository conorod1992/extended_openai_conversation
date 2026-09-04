"""Keep administrator-configured regex work off Home Assistant's event loop."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextvars import ContextVar
from typing import Any, TypeVar

from homeassistant.core import HomeAssistant

from .speech import has_custom_speech_replacements

_T = TypeVar("_T")

_DEFER_SPEECH_PROCESSING: ContextVar[bool] = ContextVar(
    "extended_openai_defer_speech_processing", default=False
)
_DEFERRED_SPEECH_INPUT: ContextVar[tuple[str, Mapping[str, Any]] | None] = ContextVar(
    "extended_openai_deferred_speech_input", default=None
)
_PREVALIDATED_ARGUMENTS: ContextVar[
    tuple[object, object, dict[str, Any]] | None
] = ContextVar("extended_openai_prevalidated_arguments", default=None)

_INSTALLED = False


async def async_run_configurable_regex(
    hass: HomeAssistant,
    target: Callable[..., _T],
    *args: Any,
) -> _T:
    """Run synchronous configurable-regex work in Home Assistant's executor.

    This isolates potentially expensive administrator-configured matching from the
    event loop. It is intentionally not presented as a hard regex timeout: Python's
    built-in ``re`` engine does not provide cancellable per-match execution.
    """
    return await hass.async_add_executor_job(target, *args)


def _install_speech_regex_isolation() -> None:
    """Defer completed-response custom speech regex until after the async handler."""
    from . import conversation as conversation_module
    from .conversation import ExtendedOpenAIAgentEntity

    current = ExtendedOpenAIAgentEntity._async_handle_message
    if getattr(current, "_extended_openai_configurable_regex_executor", False):
        return

    original_handle_message = current
    original_process_speech_text = conversation_module.process_speech_text

    def deferred_process_speech_text(
        original_text: str, agent_config: Mapping[str, Any]
    ) -> str:
        if _DEFER_SPEECH_PROCESSING.get() and has_custom_speech_replacements(
            agent_config
        ):
            _DEFERRED_SPEECH_INPUT.set((original_text, agent_config))
            # This intermediate value never leaves _async_handle_message. The
            # wrapper below replaces it with the exact normal pipeline result
            # before the ConversationResult is returned.
            return original_text
        return original_process_speech_text(original_text, agent_config)

    conversation_module.process_speech_text = deferred_process_speech_text

    async def async_handle_message(
        agent: Any,
        user_input: Any,
        chat_log: Any,
        request_options: Mapping[str, Any] | None = None,
    ) -> Any:
        defer = has_custom_speech_replacements(agent.subentry.data)
        defer_token = _DEFER_SPEECH_PROCESSING.set(defer)
        input_token = _DEFERRED_SPEECH_INPUT.set(None)
        deferred_input: tuple[str, Mapping[str, Any]] | None = None
        try:
            result = await original_handle_message(
                agent, user_input, chat_log, request_options
            )
            deferred_input = _DEFERRED_SPEECH_INPUT.get()
        finally:
            _DEFERRED_SPEECH_INPUT.reset(input_token)
            _DEFER_SPEECH_PROCESSING.reset(defer_token)

        if deferred_input is not None:
            speech_text = await async_run_configurable_regex(
                agent.hass,
                original_process_speech_text,
                *deferred_input,
            )
            result.response.async_set_speech(speech_text)
        return result

    async_handle_message._extended_openai_configurable_regex_executor = True  # type: ignore[attr-defined]
    ExtendedOpenAIAgentEntity._async_handle_message = async_handle_message  # type: ignore[method-assign,assignment]


def _install_function_argument_regex_isolation() -> None:
    """Move configured Function Tool schema validation into the HA executor."""
    from . import entity as entity_module
    from .entity import ExtendedOpenAIBaseLLMEntity

    current = ExtendedOpenAIBaseLLMEntity._execute_function_tool
    if getattr(current, "_extended_openai_configurable_regex_executor", False):
        return

    original_execute = current
    original_validate = entity_module.validate_function_arguments

    def cached_validate(spec: Any, arguments: Any) -> dict[str, Any]:
        cached = _PREVALIDATED_ARGUMENTS.get()
        if cached is not None and cached[0] is spec and cached[1] is arguments:
            return cached[2]
        return original_validate(spec, arguments)

    entity_module.validate_function_arguments = cached_validate

    async def execute_function_tool(
        entity: Any,
        function_tool: dict[str, Any],
        tool_input: Any,
        llm_context: Any,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        spec = function_tool.get("spec")
        if not isinstance(spec, Mapping):
            return await original_execute(
                entity,
                function_tool,
                tool_input,
                llm_context,
                exposed_entities,
            )

        arguments = tool_input.tool_args
        validated = await async_run_configurable_regex(
            entity.hass,
            original_validate,
            spec,
            arguments,
        )
        token = _PREVALIDATED_ARGUMENTS.set((spec, arguments, validated))
        try:
            return await original_execute(
                entity,
                function_tool,
                tool_input,
                llm_context,
                exposed_entities,
            )
        finally:
            _PREVALIDATED_ARGUMENTS.reset(token)

    execute_function_tool._extended_openai_configurable_regex_executor = True  # type: ignore[attr-defined]
    ExtendedOpenAIBaseLLMEntity._execute_function_tool = execute_function_tool  # type: ignore[method-assign,assignment]


def install_configurable_regex_isolation() -> None:
    """Install event-loop isolation for administrator-configured regex paths."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_speech_regex_isolation()
    _install_function_argument_regex_isolation()
    _INSTALLED = True
