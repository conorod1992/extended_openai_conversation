"""Keep administrator-configured regex work off Home Assistant's event loop."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextvars import ContextVar
from typing import Any

from homeassistant.core import HomeAssistant

from .speech import has_custom_speech_replacements

_DEFER_SPEECH_PROCESSING: ContextVar[bool] = ContextVar(
    "extended_openai_defer_speech_processing", default=False
)
_DEFERRED_SPEECH_INPUT: ContextVar[tuple[str, Mapping[str, Any]] | None] = ContextVar(
    "extended_openai_deferred_speech_input", default=None
)

_INSTALLED = False


async def async_run_configurable_regex[T](
    hass: HomeAssistant,
    target: Callable[..., T],
    *args: Any,
) -> T:
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
        subentry_data = getattr(getattr(agent, "subentry", None), "data", None)
        defer = bool(subentry_data and has_custom_speech_replacements(subentry_data))
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


def install_configurable_regex_isolation() -> None:
    """Install event-loop isolation for administrator-configured regex paths."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_speech_regex_isolation()
    _INSTALLED = True
