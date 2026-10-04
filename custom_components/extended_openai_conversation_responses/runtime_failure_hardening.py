"""Contain optional runtime failures without changing successful request behavior."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from openai import OpenAIError

from homeassistant.components.conversation import AssistantContent, ConversationResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent

from .const import CONF_API_MODE, CONF_CHAT_MODEL, DEFAULT_API_MODE, DEFAULT_CHAT_MODEL
from .debug import record_current_provider_failure
from .exceptions import TokenLengthExceededError
from .model_lifecycle import record_entity_retirement_failure
from .operational_errors import log_handled_failure
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
    handled_locally: bool = False,
) -> ConversationResult:
    """Build the same Assist error result for failures before or during provider prep."""
    if isinstance(err, httpx.RequestError):
        stream_error = ProviderTransportError("Provider stream interrupted")
        stream_error.__cause__ = err
        err = stream_error
    active_logger = logger or _LOGGER
    entry_id = getattr(getattr(entity, "entry", None), "entry_id", "unknown")
    subentry = getattr(entity, "subentry", None)
    assistant = getattr(subentry, "subentry_id", "unknown")
    options = getattr(subentry, "data", {})
    context = (
        f"entry={entry_id} assistant={assistant} "
        f"configured_model={options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)} "
        f"configured_api_mode={options.get(CONF_API_MODE, DEFAULT_API_MODE)}"
    )
    usage = getattr(entity, "_usage", None)
    if usage is not None:
        usage.mark_current_run_failed(type(err).__name__)

    if isinstance(err, OpenAIError):
        request_reauthentication(entity.hass, getattr(entity, "entry", None), err)
        record_current_provider_failure(err)
        retirement_message = record_entity_retirement_failure(
            entity, err, logger=active_logger
        )
        if retirement_message is not None:
            message = f"Sorry, {retirement_message}"
        else:
            log_provider_failure(
                active_logger, f"{provider_log_message} {context}", err
            )
            message = f"Sorry, I had a problem talking to OpenAI: {provider_user_message(err)}"
    else:
        if isinstance(err, TokenLengthExceededError):
            active_logger.warning("Conversation failed %s: %s", context, err)
        else:
            log_handled_failure(active_logger, f"Conversation failed {context}", err)
        message = f"Something went wrong: {err}"

    response = intent.IntentResponse(language=user_input.language)
    response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, message)
    if handled_locally:
        chat_log.content.append(
            AssistantContent(agent_id=entity.entity_id, content=message)
        )
    entity._fire_conversation_finished(
        user_input,
        chat_log,
        status="error",
        error_type=type(err).__name__,
        **({"handled_locally": True} if handled_locally else {}),
    )
    return ConversationResult(
        response=response,
        conversation_id=(
            user_input.conversation_id
            if conversation_id is _USE_INPUT_CONVERSATION_ID
            else conversation_id
        ),
    )
