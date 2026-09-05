"""AI Task integration for Extended OpenAI Conversation (Responses)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from openai import OpenAIError

from homeassistant.components import ai_task, conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .debug import record_current_provider_failure
from .entity import ExtendedOpenAIBaseLLMEntity
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
        # Call _async_handle_chat_log with empty custom_functions and exposed_entities
        # AI Task operates without functions
        try:
            await self._async_handle_chat_log(
                chat_log,
                function_tools=[],
                exposed_entities=[],
                llm_context=None,
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
