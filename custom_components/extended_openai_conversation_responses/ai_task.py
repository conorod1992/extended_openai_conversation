"""AI Task integration for Extended OpenAI Conversation (Responses)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from openai import OpenAIError

from homeassistant.components import ai_task, conversation
from homeassistant.components.ai_task.const import DEFAULT_SYSTEM_PROMPT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .debug import record_current_provider_failure
from .entity import ExtendedOpenAIBaseLLMEntity
from .ha_llm_tools import ToolSnapshot, caller_api_tools, tool_snapshot_scope
from .provider_errors import log_provider_failure, request_reauthentication
from .structured_output import parse_ai_task_structured_response

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry

    from . import ExtendedOpenAIConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up AI Task entities."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != "ai_task_data":
            continue

        async_add_entities(
            [ExtendedOpenAITaskEntity(config_entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class ExtendedOpenAITaskEntity(
    ai_task.AITaskEntity,
    ExtendedOpenAIBaseLLMEntity,
):
    """Extended OpenAI AI Task entity."""

    def __init__(
        self, entry: ExtendedOpenAIConfigEntry, subentry: ConfigSubentry
    ) -> None:
        """Initialize the entity."""
        super().__init__(entry, subentry)
        self._attr_supported_features = (
            ai_task.AITaskEntityFeature.GENERATE_DATA
            | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
        )

    async def _async_generate_data(
        self,
        task: ai_task.GenDataTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenDataTaskResult:
        """Handle a generate data task."""
        # Core has already assembled a caller-supplied API with the task context.
        # Reuse our exchange/budget; never import this agent's custom Functions.
        snapshot, tools = (
            caller_api_tools(chat_log.llm_api)
            if chat_log.llm_api is not None
            else (ToolSnapshot(), [])
        )
        try:
            if chat_log.llm_api is not None:
                caller_instance = chat_log.llm_api
                # Ask Core to render its normal task baseline without source
                # prompts. Our per-round exposure now owns those intact fragments.
                # Retain the caller serializer for structured output conversion.
                await chat_log.async_provide_llm_data(
                    llm_context=caller_instance.llm_context,
                    user_llm_prompt=DEFAULT_SYSTEM_PROMPT,
                )
                chat_log.llm_api = caller_instance
            with tool_snapshot_scope(snapshot):
                await self._async_handle_chat_log(
                    chat_log,
                    function_tools=tools,
                    exposed_entities=[],
                    llm_context=chat_log.llm_api.llm_context
                    if chat_log.llm_api
                    else None,
                    structure_name=task.name,
                    structure=task.structure,
                )
        except OpenAIError as err:
            request_reauthentication(self.hass, getattr(self, "entry", None), err)
            record_current_provider_failure(err)
            log_provider_failure(_LOGGER, "OpenAI AI Task request failed", err)
            raise

        # Extract response
        if not isinstance(chat_log.content[-1], conversation.AssistantContent):
            raise HomeAssistantError(
                "Last content in chat log is not an AssistantContent"
            )

        text = chat_log.content[-1].content or ""

        # Handle structured output
        if not task.structure:
            return ai_task.GenDataTaskResult(
                conversation_id=chat_log.conversation_id,
                data=text,
            )

        data = parse_ai_task_structured_response(text)

        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id,
            data=data,
        )
