"""Contain optional runtime failures without changing successful request behavior."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from openai import OpenAIError

from homeassistant.components.conversation import ConversationResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent

from .debug import record_current_provider_failure
from .provider_errors import (
    ProviderTransportError,
    log_provider_failure,
    provider_user_message,
    request_reauthentication,
)

_LOGGER = logging.getLogger(__name__)
_USE_INPUT_CONVERSATION_ID = object()


def _conversation_error_result(
    entity: Any,
    user_input: Any,
    chat_log: Any,
    err: OpenAIError | HomeAssistantError | httpx.RequestError,
    *,
    logger: logging.Logger | None = None,
    provider_log_message: str = "OpenAI request preparation failed",
    conversation_id: Any = _USE_INPUT_CONVERSATION_ID,
) -> ConversationResult:
    """Build the same Assist error result for failures before or during provider prep."""
    if isinstance(err, httpx.RequestError):
        stream_error = ProviderTransportError("Provider stream interrupted")
        stream_error.__cause__ = err
        err = stream_error
    active_logger = logger or _LOGGER
    usage = getattr(entity, "_usage", None)
    if usage is not None:
        usage.mark_current_run_failed(type(err).__name__)

    if isinstance(err, OpenAIError):
        request_reauthentication(entity.hass, getattr(entity, "entry", None), err)
        record_current_provider_failure(err)
        log_provider_failure(active_logger, provider_log_message, err)
        message = (
            f"Sorry, I had a problem talking to OpenAI: {provider_user_message(err)}"
        )
    else:
        active_logger.error("Error during conversation: %s", err, exc_info=True)
        message = f"Something went wrong: {err}"

    response = intent.IntentResponse(language=user_input.language)
    response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, message)
    entity._fire_conversation_finished(
        user_input, chat_log, status="error", error_type=type(err).__name__
    )
    return ConversationResult(
        response=response,
        conversation_id=(
            user_input.conversation_id
            if conversation_id is _USE_INPUT_CONVERSATION_ID
            else conversation_id
        ),
    )
