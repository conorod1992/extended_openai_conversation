"""Contain optional runtime failures without changing successful request behavior."""

from __future__ import annotations

import logging
from typing import Any

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
