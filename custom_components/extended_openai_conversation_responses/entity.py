"""Base entity for Extended OpenAI Conversation (Responses)."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator, Iterable
from dataclasses import replace
import json
import logging
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from openai import AsyncClient, AsyncStream
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionChunk,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)
import orjson
import voluptuous as vol
from voluptuous_openapi import convert  # type: ignore[import-untyped]

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigSubentry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, llm
from homeassistant.helpers.entity import Entity
from homeassistant.util import slugify

from .const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_CONTEXT_THRESHOLD,
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    CONF_MAX_TOKENS,
    CONF_REASONING_EFFORT,
    CONF_SERVICE_TIER,
    CONF_SHORTEN_TOOL_CALL_ID,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    DEFAULT_API_MODE,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONTEXT_THRESHOLD,
    DEFAULT_CONTEXT_TRUNCATE_STRATEGY,
    DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    DEFAULT_MAX_TOKENS,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_SERVICE_TIER,
    DEFAULT_SHORTEN_TOOL_CALL_ID,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    DOMAIN,
)
from .exceptions import FunctionNotFound, ParseArgumentsFailed, TokenLengthExceededError
from .functions import get_function
from .helpers import get_api_mode, get_model_config

if TYPE_CHECKING:
    from . import ExtendedOpenAIConfigEntry

_LOGGER = logging.getLogger(__name__)

# Max number of back and forth with the LLM to generate a response
MAX_TOOL_ITERATIONS = 20

CONTINUE_CONVERSATION_TOOL_NAME = "set_continue_conversation"
CONTINUE_CONVERSATION_TOOL = {
    "spec": {
        "name": CONTINUE_CONVERSATION_TOOL_NAME,
        "description": (
            "Return the final spoken response and whether the user is expected to "
            "reply immediately. Use this only when the final answer is ready."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "response": {
                    "type": "string",
                    "description": "The complete plain-text response spoken to the user.",
                },
                "continue_conversation": {
                    "type": "boolean",
                    "description": "Whether to listen for an immediate follow-up.",
                },
            },
            "required": ["response", "continue_conversation"],
            "additionalProperties": False,
        },
    }
}


def _shorten_tool_call_id(tool_call_id: str) -> str:
    """Shorten tool call ID to exactly 9 alphanumeric characters as Mistral requires."""
    import hashlib

    return hashlib.sha256(tool_call_id.encode()).hexdigest()[:9]


def _adjust_schema(schema: dict[str, Any]) -> None:
    """Adjust the schema to be compatible with OpenAI API."""
    if schema["type"] == "object":
        schema.setdefault("strict", True)
        schema.setdefault("additionalProperties", False)
        if "properties" not in schema:
            return

        if "required" not in schema:
            schema["required"] = []

        # Ensure all properties are required
        for prop, prop_info in schema["properties"].items():
            _adjust_schema(prop_info)
            if prop not in schema["required"]:
                prop_info["type"] = [prop_info["type"], "null"]
                schema["required"].append(prop)

    elif schema["type"] == "array":
        if "items" not in schema:
            return

        _adjust_schema(schema["items"])


def _format_structured_output(
    schema: vol.Schema, llm_api: llm.APIInstance | None
) -> dict[str, Any]:
    """Format the schema to be compatible with OpenAI API."""
    result: dict[str, Any] = convert(
        schema,
        custom_serializer=(
            llm_api.custom_serializer if llm_api else llm.selector_serializer
        ),
    )

    _adjust_schema(result)

    return result


def _convert_content_to_param(
    chat_content: list[conversation.Content],
    shorten_tool_call_id: bool = False,
) -> list[ChatCompletionMessageParam]:
    """Convert chat log content to OpenAI message format."""
    messages: list[ChatCompletionMessageParam] = []

    for content in chat_content:
        if content.role == "system":
            messages.append({"role": "system", "content": content.content})
        elif content.role == "user":
            messages.append({"role": "user", "content": content.content})
        elif content.role == "assistant":
            msg: ChatCompletionAssistantMessageParam = {"role": "assistant"}
            if content.content:
                msg["content"] = content.content
            if content.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": _shorten_tool_call_id(tool_call.id)
                        if shorten_tool_call_id
                        else tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.tool_name,
                            "arguments": json.dumps(tool_call.tool_args),
                        },
                    }
                    for tool_call in content.tool_calls
                ]
            # Some OpenAI-compatible APIs (like Mistral) reject empty tool_calls arrays
            # Remove tool_calls field if it's an empty array to maintain compatibility
            if msg.get("tool_calls") == []:
                msg.pop("tool_calls", None)
            messages.append(msg)
        elif content.role == "tool_result":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": _shorten_tool_call_id(content.tool_call_id)
                    if shorten_tool_call_id
                    else content.tool_call_id,
                    "content": orjson.dumps(content.tool_result).decode(),
                }
            )

    return messages


def _serialize_response_item(item: Any) -> dict[str, Any]:
    """Serialize an SDK Responses item for use as a subsequent input item."""
    if hasattr(item, "model_dump"):
        serialized = cast(dict[str, Any], item.model_dump(exclude_none=True))
    elif hasattr(item, "to_dict"):
        serialized = cast(dict[str, Any], item.to_dict())
    elif isinstance(item, dict):
        serialized = dict(item)
    else:
        raise TypeError(f"Unsupported Responses item type: {type(item)!r}")

    if serialized.get("type") == "reasoning":
        return {
            key: value
            for key in ("type", "id", "summary", "encrypted_content")
            if (value := serialized.get(key)) is not None
        }
    return serialized


def _convert_content_to_responses_param(
    chat_content: Iterable[conversation.Content],
) -> list[dict[str, Any]]:
    """Convert Home Assistant chat content to Responses API input items."""
    items: list[dict[str, Any]] = []

    for content in chat_content:
        if isinstance(content, conversation.ToolResultContent):
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": content.tool_call_id,
                    "output": orjson.dumps(content.tool_result).decode(),
                }
            )
            continue

        if content.content:
            items.append(
                {
                    "type": "message",
                    "role": content.role,
                    "content": content.content,
                }
            )

        if isinstance(content, conversation.AssistantContent):
            native = content.native
            if native is not None and getattr(native, "type", None) == "reasoning":
                items.append(_serialize_response_item(native))

            for tool_call in content.tool_calls or []:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": tool_call.id,
                        "name": tool_call.tool_name,
                        "arguments": json.dumps(tool_call.tool_args),
                    }
                )

    return items


def _format_tools(
    function_tools: list[dict[str, Any]], api_mode: str
) -> list[dict[str, Any]]:
    """Format function definitions for the selected OpenAI API."""
    if api_mode == API_MODE_RESPONSES:
        return [
            {"type": "function", **func_spec["spec"]} for func_spec in function_tools
        ]

    return [
        dict(
            ChatCompletionToolParam(
                type="function",
                function=func_spec["spec"],
            )
        )
        for func_spec in function_tools
    ]


class ExtendedOpenAIBaseLLMEntity(Entity):
    """Extended OpenAI base entity."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self, entry: ExtendedOpenAIConfigEntry, subentry: ConfigSubentry
    ) -> None:
        """Initialize the entity."""
        self.entry = entry
        self.subentry = subentry
        self._attr_unique_id = subentry.subentry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="OpenAI",
            model=subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    @property
    def _client(self) -> AsyncClient:
        """Return the OpenAI client."""
        return self.entry.runtime_data

    async def _async_handle_chat_log(
        self,
        chat_log: conversation.ChatLog,
        function_tools: list[dict[str, Any]],
        exposed_entities: list[dict[str, Any]],
        llm_context: llm.LLMContext | None = None,
        structure_name: str | None = None,
        structure: vol.Schema | None = None,
        conditional_continue: bool = False,
    ) -> bool | None:
        """Generate an answer for the chat log with streaming support."""
        options = self.subentry.data
        model = options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)
        api_mode = get_api_mode(
            options.get(CONF_API_MODE, DEFAULT_API_MODE),
            model,
        )
        max_function_calls = options.get(
            CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
            DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
        )
        shorten_tool_call_id = options.get(
            CONF_SHORTEN_TOOL_CALL_ID,
            DEFAULT_SHORTEN_TOOL_CALL_ID,
        )

        model_config = get_model_config(model)
        messages: Any
        if api_mode == API_MODE_RESPONSES:
            messages = _convert_content_to_responses_param(chat_log.content)
        else:
            messages = _convert_content_to_param(chat_log.content, shorten_tool_call_id)

        await self._async_add_attachments(chat_log, messages, api_mode)

        if conditional_continue and any(
            function_tool["spec"]["name"] == CONTINUE_CONVERSATION_TOOL_NAME
            for function_tool in function_tools
        ):
            raise HomeAssistantError(
                f"Function tool name `{CONTINUE_CONVERSATION_TOOL_NAME}` is reserved "
                "for Conditional continue conversation mode"
            )

        tools = _format_tools(
            [
                *function_tools,
                *([CONTINUE_CONVERSATION_TOOL] if conditional_continue else []),
            ],
            api_mode,
        )
        continuation_decision: bool | None = None
        api_kwargs: dict[str, Any] = {
            "model": model,
            "stream": True,
        }

        max_tokens = options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)
        if api_mode == API_MODE_RESPONSES:
            api_kwargs["max_output_tokens"] = max_tokens
            # Responses are kept stateless, matching the integration's existing
            # ChatLog-owned conversation history. Encrypted reasoning items let
            # reasoning models continue across chained tool calls.
            api_kwargs["store"] = False
        else:
            api_kwargs["stream_options"] = {"include_usage": True}
            if model_config["supports_max_completion_tokens"]:
                api_kwargs["max_completion_tokens"] = max_tokens
            elif model_config["supports_max_tokens"]:
                api_kwargs["max_tokens"] = max_tokens

        if model_config["supports_top_p"]:
            api_kwargs["top_p"] = options.get(CONF_TOP_P, DEFAULT_TOP_P)

        if model_config["supports_temperature"]:
            api_kwargs["temperature"] = options.get(
                CONF_TEMPERATURE, DEFAULT_TEMPERATURE
            )

        if model_config.get("supports_reasoning_effort"):
            reasoning_effort = options.get(
                CONF_REASONING_EFFORT, DEFAULT_REASONING_EFFORT
            )
            if api_mode == API_MODE_RESPONSES:
                api_kwargs["reasoning"] = {"effort": reasoning_effort}
                api_kwargs["include"] = ["reasoning.encrypted_content"]
            else:
                api_kwargs["reasoning_effort"] = reasoning_effort

        if model_config.get("supports_service_tier"):
            api_kwargs["service_tier"] = options.get(
                CONF_SERVICE_TIER, DEFAULT_SERVICE_TIER
            )

        if structure is not None:
            output_format = {
                "type": "json_schema",
                "name": slugify(structure_name),
                "strict": True,
                "schema": _format_structured_output(structure, chat_log.llm_api),
            }
            if api_mode == API_MODE_RESPONSES:
                api_kwargs["text"] = {"format": output_format}
            else:
                api_kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": slugify(structure_name),
                        "strict": True,
                        "schema": _format_structured_output(
                            structure, chat_log.llm_api
                        ),
                    },
                }

        tool_kwargs: dict[str, Any] = {}
        if tools:
            tool_kwargs["tools"] = tools
            tool_kwargs["tool_choice"] = "required" if conditional_continue else "auto"

        finalization_retry_attempted = False
        draft_content_ids: set[int] = set()

        for n_requests in range(MAX_TOOL_ITERATIONS):
            if tools and 0 <= max_function_calls <= n_requests:
                if conditional_continue:
                    tool_kwargs["tools"] = _format_tools(
                        [CONTINUE_CONVERSATION_TOOL], api_mode
                    )
                    tool_kwargs["tool_choice"] = "required"
                else:
                    tool_kwargs["tool_choice"] = "none"

            _LOGGER.info(
                "Prompt for %s using %s: %s", model, api_mode, json.dumps(messages)
            )

            if api_mode == API_MODE_RESPONSES:
                responses_stream = cast(
                    AsyncStream[Any],
                    await self._client.responses.create(
                        input=messages,
                        **api_kwargs,
                        **tool_kwargs,
                    ),
                )
                transformed_stream = self._transform_responses_stream(
                    chat_log, responses_stream
                )
            else:
                chat_stream = cast(
                    AsyncStream[ChatCompletionChunk],
                    await self._client.chat.completions.create(
                        messages=messages,
                        **api_kwargs,
                        **tool_kwargs,
                    ),
                )
                transformed_stream = self._transform_chat_stream(chat_log, chat_stream)

            existing_content_ids = {id(content) for content in chat_log.content}
            pending_tool_calls: list[llm.ToolInput] = []

            async for content in chat_log.async_add_delta_content_stream(
                self.entity_id, transformed_stream
            ):
                if (
                    isinstance(content, conversation.AssistantContent)
                    and content.tool_calls
                ):
                    pending_tool_calls.extend(content.tool_calls)

            if pending_tool_calls:
                _LOGGER.info("Response Tool Calls %s", pending_tool_calls)

            control_calls = [
                tool_input
                for tool_input in pending_tool_calls
                if tool_input.tool_name == CONTINUE_CONVERSATION_TOOL_NAME
            ]
            pending_tool_calls = [
                tool_input
                for tool_input in pending_tool_calls
                if tool_input.tool_name != CONTINUE_CONVERSATION_TOOL_NAME
            ]

            if control_calls:
                control_call = control_calls[-1]
                response_text = control_call.tool_args.get("response")
                decision = control_call.tool_args.get("continue_conversation")
                if not isinstance(response_text, str) or not isinstance(decision, bool):
                    raise ParseArgumentsFailed(json.dumps(control_call.tool_args))

                # A finalizer emitted beside an action tool is premature. Remove it
                # from history and wait for the post-tool response to decide.
                is_final = not pending_tool_calls
                self._consume_continue_conversation_tool(
                    chat_log,
                    existing_content_ids,
                    response_text if is_final else None,
                )
                if is_final:
                    continuation_decision = decision
                    if draft_content_ids:
                        chat_log.content[:] = [
                            content
                            for content in chat_log.content
                            if id(content) not in draft_content_ids
                        ]

            for tool_input in pending_tool_calls:
                function_tool = next(
                    (
                        f
                        for f in (function_tools)
                        if f["spec"]["name"] == tool_input.tool_name
                    ),
                    None,
                )

                if function_tool is None:
                    raise FunctionNotFound(tool_input.tool_name)

                tool_result_content = await self._execute_function_tool(
                    function_tool,
                    tool_input,
                    llm_context,
                    exposed_entities,
                )

                chat_log.async_add_assistant_content_without_tools(tool_result_content)

            if api_mode == API_MODE_RESPONSES:
                messages.extend(
                    _convert_content_to_responses_param(
                        content
                        for content in chat_log.content
                        if id(content) not in existing_content_ids
                    )
                )
            else:
                messages = _convert_content_to_param(
                    chat_log.content, shorten_tool_call_id
                )

            if (
                conditional_continue
                and continuation_decision is None
                and not pending_tool_calls
                and not control_calls
            ):
                if not finalization_retry_attempted:
                    draft_content_ids.update(
                        id(content)
                        for content in chat_log.content
                        if id(content) not in existing_content_ids
                        and isinstance(content, conversation.AssistantContent)
                        and not content.tool_calls
                    )
                    finalization_retry_attempted = True
                    tool_kwargs["tools"] = _format_tools(
                        [CONTINUE_CONVERSATION_TOOL], api_mode
                    )
                    tool_kwargs["tool_choice"] = "required"
                    _LOGGER.warning(
                        "Conditional response omitted %s; retrying once with only "
                        "the finalizer available",
                        CONTINUE_CONVERSATION_TOOL_NAME,
                    )
                    continue

                _LOGGER.error(
                    "Conditional response omitted %s after the finalization retry; "
                    "using the assistant text with continuation disabled",
                    CONTINUE_CONVERSATION_TOOL_NAME,
                )
                if draft_content_ids:
                    chat_log.content[:] = [
                        content
                        for content in chat_log.content
                        if id(content) not in draft_content_ids
                    ]

            if not chat_log.unresponded_tool_results:
                break

        return continuation_decision

    @staticmethod
    def _consume_continue_conversation_tool(
        chat_log: conversation.ChatLog,
        existing_content_ids: set[int],
        response_text: str | None,
    ) -> None:
        """Convert the internal finalizer tool call into normal assistant content."""
        updated_content: list[conversation.Content] = []
        for content in chat_log.content:
            if (
                id(content) in existing_content_ids
                or not isinstance(content, conversation.AssistantContent)
                or not content.tool_calls
                or not any(
                    tool_call.tool_name == CONTINUE_CONVERSATION_TOOL_NAME
                    for tool_call in content.tool_calls
                )
            ):
                updated_content.append(content)
                continue

            remaining_calls = [
                tool_call
                for tool_call in content.tool_calls
                if tool_call.tool_name != CONTINUE_CONVERSATION_TOOL_NAME
            ]
            replacement_content = (
                response_text if not remaining_calls else content.content
            )
            if replacement_content or remaining_calls or content.native:
                updated_content.append(
                    replace(
                        content,
                        content=replacement_content,
                        tool_calls=remaining_calls or None,
                    )
                )

        chat_log.content[:] = updated_content

    async def _async_add_attachments(
        self,
        chat_log: conversation.ChatLog,
        messages: list[Any],
        api_mode: str,
    ) -> None:
        """Attach images and PDFs from the latest user content to the request."""
        last_content = chat_log.content[-1]
        if not isinstance(last_content, conversation.UserContent) or not getattr(
            last_content, "attachments", None
        ):
            return

        def prepare_attachments() -> list[dict[str, Any]]:
            prepared: list[dict[str, Any]] = []
            for attachment in last_content.attachments or []:
                path = Path(attachment.path)
                if not path.exists():
                    raise HomeAssistantError(f"`{path}` does not exist")

                mime_type = attachment.mime_type or mimetypes.guess_type(path)[0]
                if not mime_type:
                    raise HomeAssistantError(
                        f"Unable to determine attachment type for `{path}`"
                    )

                encoded = base64.b64encode(path.read_bytes()).decode()
                data_url = f"data:{mime_type};base64,{encoded}"
                if mime_type.startswith("image/"):
                    if api_mode == API_MODE_RESPONSES:
                        prepared.append(
                            {
                                "type": "input_image",
                                "image_url": data_url,
                                "detail": "auto",
                            }
                        )
                    else:
                        prepared.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": data_url},
                            }
                        )
                elif mime_type == "application/pdf" and api_mode == API_MODE_RESPONSES:
                    prepared.append(
                        {
                            "type": "input_file",
                            "filename": path.name,
                            "file_data": data_url,
                        }
                    )
                else:
                    raise HomeAssistantError(
                        "Chat Completions supports image attachments; Responses "
                        "supports image and PDF attachments. "
                        f"Unsupported attachment `{path}` ({mime_type})."
                    )
            return prepared

        attachments = await self.hass.async_add_executor_job(prepare_attachments)
        last_message = messages[-1]
        text_content = last_message["content"]
        if api_mode == API_MODE_RESPONSES:
            last_message["content"] = [
                {"type": "input_text", "text": text_content},
                *attachments,
            ]
        else:
            last_message["content"] = [
                {"type": "text", "text": text_content},
                *attachments,
            ]

    async def _transform_chat_stream(
        self,
        chat_log: conversation.ChatLog,
        result: AsyncStream[ChatCompletionChunk],
    ) -> AsyncGenerator[
        conversation.AssistantContentDeltaDict | conversation.ToolResultContentDeltaDict
    ]:
        """Transform OpenAI stream to Home Assistant format."""
        current_tool_calls: dict[int, dict[str, Any]] = {}
        first_chunk = True

        async for chunk in result:
            _LOGGER.debug("Received chunk: %s", chunk)

            # Signal new assistant message on first chunk
            if first_chunk:
                yield {"role": "assistant"}
                first_chunk = False

            if not chunk.choices:
                # Track usage from final chunk if available
                if chunk.usage:
                    chat_log.async_trace(
                        {
                            "stats": {
                                "input_tokens": chunk.usage.prompt_tokens,
                                "output_tokens": chunk.usage.completion_tokens,
                            }
                        }
                    )
                    if chunk.usage.total_tokens > self.subentry.data.get(
                        CONF_CONTEXT_THRESHOLD, DEFAULT_CONTEXT_THRESHOLD
                    ):
                        await self._truncate_message_history(chat_log)
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            if delta.content:
                # Ensure content is a string (Mistral might return unexpected types)
                content_value = delta.content
                if not isinstance(content_value, str):
                    _LOGGER.warning(
                        "Received non-string content from API: %s (type: %s)",
                        content_value,
                        type(content_value),
                    )
                    content_value = str(content_value) if content_value else ""
                if content_value:
                    yield {"content": content_value}

            if delta.tool_calls:
                for tool_call_delta in delta.tool_calls:
                    idx = tool_call_delta.index
                    if idx not in current_tool_calls:
                        current_tool_calls[idx] = {
                            "id": tool_call_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }

                    if tool_call_delta.function:
                        if tool_call_delta.function.name:
                            current_tool_calls[idx]["name"] = (
                                tool_call_delta.function.name
                            )
                        if tool_call_delta.function.arguments:
                            current_tool_calls[idx]["arguments"] += (
                                tool_call_delta.function.arguments
                            )

            if current_tool_calls and (choice.finish_reason in {"tool_calls", "stop"}):
                # Yield all accumulated tool calls (marked as external since we handle them ourselves)
                tool_calls_list = []
                for idx in sorted(current_tool_calls.keys()):
                    tool_call = current_tool_calls[idx]
                    try:
                        args = json.loads(tool_call["arguments"])
                    except json.JSONDecodeError as err:
                        raise ParseArgumentsFailed(tool_call["arguments"]) from err
                    tool_calls_list.append(
                        llm.ToolInput(
                            id=tool_call["id"],
                            tool_name=tool_call["name"],
                            tool_args=args,
                            external=True,  # Mark as external so ChatLog doesn't try to execute
                        )
                    )
                if tool_calls_list:
                    yield {"tool_calls": tool_calls_list}
                current_tool_calls.clear()
            if choice.finish_reason == "length":
                raise TokenLengthExceededError(
                    self.subentry.data.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)
                )

            if choice.finish_reason == "stop":
                break

    async def _transform_responses_stream(
        self,
        chat_log: conversation.ChatLog,
        result: AsyncStream[Any],
    ) -> AsyncGenerator[
        conversation.AssistantContentDeltaDict | conversation.ToolResultContentDeltaDict
    ]:
        """Transform a Responses API event stream to Home Assistant format."""
        async for event in result:
            _LOGGER.debug("Received Responses event: %s", event)
            event_type = getattr(event, "type", "")

            if event_type == "response.output_item.added":
                item_type = getattr(event.item, "type", "")
                if item_type in {"message", "function_call"}:
                    yield {"role": "assistant"}
                continue

            if event_type == "response.output_text.delta":
                if event.delta:
                    yield {"content": event.delta}
                continue

            if event_type == "response.output_item.done":
                item = event.item
                item_type = getattr(item, "type", "")
                if item_type == "reasoning":
                    # Preserve encrypted reasoning so stateless chained tool calls
                    # can continue without losing the model's reasoning context.
                    yield {"native": item}
                elif item_type == "function_call":
                    try:
                        arguments = json.loads(item.arguments)
                    except json.JSONDecodeError as err:
                        raise ParseArgumentsFailed(item.arguments) from err
                    yield {
                        "tool_calls": [
                            llm.ToolInput(
                                id=item.call_id,
                                tool_name=item.name,
                                tool_args=arguments,
                                external=True,
                            )
                        ]
                    }
                continue

            if event_type in {"response.completed", "response.incomplete"}:
                response = event.response
                if response.usage is not None:
                    input_tokens = response.usage.input_tokens
                    output_tokens = response.usage.output_tokens
                    chat_log.async_trace(
                        {
                            "stats": {
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                            }
                        }
                    )
                    if input_tokens + output_tokens > self.subentry.data.get(
                        CONF_CONTEXT_THRESHOLD, DEFAULT_CONTEXT_THRESHOLD
                    ):
                        await self._truncate_message_history(chat_log)

                if event_type == "response.incomplete":
                    details = response.incomplete_details
                    reason = (
                        details.reason
                        if details and details.reason
                        else "unknown reason"
                    )
                    if reason == "max_output_tokens":
                        raise TokenLengthExceededError(
                            self.subentry.data.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)
                        )
                    raise HomeAssistantError(f"OpenAI response incomplete: {reason}")
                continue

            if event_type == "response.failed":
                error = getattr(event.response, "error", None)
                reason = getattr(error, "message", None) or "unknown reason"
                raise HomeAssistantError(f"OpenAI response failed: {reason}")

            if event_type in {"error", "response.error"}:
                reason = getattr(event, "message", None) or "unknown reason"
                raise HomeAssistantError(f"OpenAI response error: {reason}")

    async def _execute_function_tool(
        self,
        function_tool: dict[str, Any],
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> conversation.ToolResultContent:
        """Execute a custom function."""
        arguments: dict[str, Any] = tool_input.tool_args
        function_config = function_tool["function"]
        function = get_function(function_config["type"])

        if self.should_run_in_background(arguments):
            # create a delayed function and execute in background
            function_config = self.get_delayed_function_config(
                function_config, arguments
            )
            function = get_function(function_config["type"])
            self.entry.async_create_task(
                self.hass,
                function.execute(
                    self.hass,
                    function_config,
                    arguments,
                    llm_context,
                    exposed_entities,
                ),
            )
            result = "Scheduled"
        else:
            result = await function.execute(
                self.hass, function_config, arguments, llm_context, exposed_entities
            )

        return conversation.ToolResultContent(
            agent_id=self.entity_id,
            tool_call_id=tool_input.id,
            tool_name=tool_input.tool_name,
            tool_result={"result": str(result)},
        )

    def should_run_in_background(self, arguments: dict[str, Any]) -> bool:
        """Check if function needs delay."""
        return isinstance(arguments, dict) and arguments.get("delay") is not None

    def get_delayed_function_config(
        self, function_config: dict[str, Any], arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute function with delay."""
        # create a composite function with delay in script function
        return {
            "type": "composite",
            "sequence": [
                {
                    "type": "script",
                    "sequence": [{"delay": arguments["delay"]}],
                },
                function_config,
            ],
        }

    async def _truncate_message_history(self, chat_log: conversation.ChatLog) -> None:
        """Truncate message history based on strategy."""
        options = self.subentry.data
        strategy = options.get(
            CONF_CONTEXT_TRUNCATE_STRATEGY, DEFAULT_CONTEXT_TRUNCATE_STRATEGY
        )

        if strategy == "clear":
            # Keep only system prompt and last user message
            # This is handled by refreshing the LLM data
            _LOGGER.info("Context threshold exceeded, conversation history cleared")
            last_user_message_index = None
            messages = chat_log.content
            for i in reversed(range(len(messages))):
                if isinstance(messages[i], conversation.UserContent):
                    last_user_message_index = i
                    break

            if last_user_message_index is not None:
                del messages[1:last_user_message_index]
