"""Contain optional runtime failures without changing successful request behavior."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from dataclasses import replace
from functools import wraps
import logging
from typing import Any, cast

from openai import OpenAIError

from homeassistant.components.conversation import ConversationResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent

from .debug import record_current_provider_failure
from .provider_errors import (
    log_provider_failure,
    provider_user_message,
    request_reauthentication,
)

_LOGGER = logging.getLogger(__name__)
_INSTALLED = False


def _conversation_error_result(
    entity: Any, user_input: Any, chat_log: Any, err: OpenAIError | HomeAssistantError
) -> ConversationResult:
    """Build the same Assist error result for failures before or during provider prep."""
    usage = getattr(entity, "_usage", None)
    if usage is not None:
        usage.mark_current_run_failed(type(err).__name__)

    if isinstance(err, OpenAIError):
        request_reauthentication(entity.hass, getattr(entity, "entry", None), err)
        record_current_provider_failure(err)
        log_provider_failure(_LOGGER, "OpenAI request preparation failed", err)
        message = (
            f"Sorry, I had a problem talking to OpenAI: {provider_user_message(err)}"
        )
    else:
        _LOGGER.error("Error during conversation: %s", err, exc_info=True)
        message = f"Something went wrong: {err}"

    response = intent.IntentResponse(language=user_input.language)
    response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, message)
    entity._fire_conversation_finished(
        user_input, chat_log, status="error", error_type=type(err).__name__
    )
    return ConversationResult(
        response=response, conversation_id=user_input.conversation_id
    )


def _install_request_preparation_boundary() -> None:
    """Keep request-preparation failures inside the existing user-facing error path."""
    from .conversation import ExtendedOpenAIAgentEntity

    current = ExtendedOpenAIAgentEntity._async_handle_message
    if getattr(current, "_extended_openai_preparation_boundary", False):
        return
    original = current

    @wraps(original)
    async def async_handle_message(
        entity: Any,
        user_input: Any,
        chat_log: Any,
        request_options: Mapping[str, Any] | None = None,
    ) -> ConversationResult:
        try:
            return await original(entity, user_input, chat_log, request_options)
        except (OpenAIError, HomeAssistantError) as err:
            return _conversation_error_result(entity, user_input, chat_log, err)

    async_handle_message._extended_openai_preparation_boundary = True  # type: ignore[attr-defined]
    setattr(  # noqa: B010
        ExtendedOpenAIAgentEntity, "_async_handle_message", async_handle_message
    )


def _install_late_chat_tool_call_id_repair() -> None:
    """Recover Chat Completions tool-call IDs supplied after the first delta."""
    from .entity import ExtendedOpenAIBaseLLMEntity

    current = ExtendedOpenAIBaseLLMEntity._transform_chat_stream
    if getattr(current, "_extended_openai_late_tool_id_repair", False):
        return
    original = cast(Any, current)

    @wraps(original)
    async def transform_chat_stream(
        entity: Any,
        chat_log: Any,
        result: Any,
        request_usage: Any = None,
    ) -> AsyncGenerator[Any]:
        seen_indexes: set[int] = set()
        ids_by_index: dict[int, str] = {}

        async def recording_stream() -> AsyncGenerator[Any]:
            async for chunk in result:
                choices = getattr(chunk, "choices", None)
                if choices:
                    delta = getattr(choices[0], "delta", None)
                    for tool_delta in getattr(delta, "tool_calls", None) or ():
                        index = getattr(tool_delta, "index", None)
                        if not isinstance(index, int):
                            continue
                        seen_indexes.add(index)
                        call_id = getattr(tool_delta, "id", None)
                        if isinstance(call_id, str) and call_id:
                            ids_by_index[index] = call_id
                yield chunk

        async for item in original(entity, chat_log, recording_stream(), request_usage):
            tool_calls = item.get("tool_calls") if isinstance(item, dict) else None
            if not tool_calls or not seen_indexes:
                yield item
                continue

            ordered_indexes = sorted(seen_indexes)
            repaired = list(tool_calls)
            changed = False
            for position, tool_call in enumerate(repaired):
                if getattr(tool_call, "id", None):
                    continue
                if position >= len(ordered_indexes):
                    continue
                late_id = ids_by_index.get(ordered_indexes[position])
                if not late_id:
                    continue
                try:
                    repaired[position] = replace(tool_call, id=late_id)
                except TypeError:
                    # Home Assistant currently exposes ToolInput as a dataclass; keep
                    # a conservative fallback for compatible older/newer releases.
                    from homeassistant.helpers import llm

                    repaired[position] = llm.ToolInput(
                        id=late_id,
                        tool_name=tool_call.tool_name,
                        tool_args=tool_call.tool_args,
                        external=getattr(tool_call, "external", True),
                    )
                changed = True

            yield {**item, "tool_calls": repaired} if changed else item

    transform_chat_stream._extended_openai_late_tool_id_repair = True  # type: ignore[attr-defined]
    setattr(  # noqa: B010
        ExtendedOpenAIBaseLLMEntity, "_transform_chat_stream", transform_chat_stream
    )


def install_runtime_failure_hardening() -> None:
    """Install non-fatal runtime failure containment once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_request_preparation_boundary()
    _install_late_chat_tool_call_id_repair()
    _INSTALLED = True
