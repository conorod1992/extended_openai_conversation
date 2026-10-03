"""AI Task integration for Extended OpenAI Conversation (Responses)."""

from __future__ import annotations

from json import JSONDecodeError
import logging
from typing import TYPE_CHECKING, Any

from openai import OpenAIError
import voluptuous as vol

from homeassistant.components import ai_task, conversation
from homeassistant.components.ai_task.const import DEFAULT_SYSTEM_PROMPT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.json import json_loads

from .debug import record_current_provider_failure
from .entity import (
    ExtendedOpenAIBaseLLMEntity,
    _schema_explicitly_allows_null,
    _serialize_structured_output,
)
from .ha_llm_tools import ToolSnapshot, caller_api_tools, tool_snapshot_scope
from .provider_errors import log_provider_failure, request_reauthentication

_LOGGER = logging.getLogger(__name__)


def _omit_optional_nulls(data: Any, schema: dict[str, Any]) -> Any:
    """Undo strict-output placeholders using the unmodified caller schema."""
    if isinstance(data, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        return {
            key: _omit_optional_nulls(value, properties.get(key, {}))
            for key, value in data.items()
            if not (
                key in properties
                and key not in required
                and value is None
                and not _schema_explicitly_allows_null(properties[key])
            )
        }
    if isinstance(data, list) and isinstance(schema.get("items"), dict):
        return [_omit_optional_nulls(item, schema["items"]) for item in data]
    return data


def parse_ai_task_structured_response(
    text: str,
    structure: vol.Schema | None = None,
    *,
    original_schema: dict[str, Any] | None = None,
) -> Any:
    """Parse and validate the caller's contract without exposing task contents."""
    try:
        data = json_loads(text)
    except JSONDecodeError as err:
        _LOGGER.error("Failed to parse structured AI Task JSON response: %s", err)
        raise HomeAssistantError("Error with structured response") from err
    if original_schema is not None:
        data = _omit_optional_nulls(data, original_schema)
    if structure is not None:
        try:
            return structure(data)
        except vol.Invalid:
            # Selector errors may include model output. Expose a stable task
            # failure and keep validation details out of logs and public errors.
            raise HomeAssistantError(
                "AI Task result does not match the requested structure"
            ) from None
    return data


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
            original_schema = (
                _serialize_structured_output(task.structure, chat_log.llm_api)
                if task.structure is not None
                else None
            )
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
                    structure_schema=original_schema,
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

        data = parse_ai_task_structured_response(
            text, task.structure, original_schema=original_schema
        )

        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id,
            data=data,
        )
