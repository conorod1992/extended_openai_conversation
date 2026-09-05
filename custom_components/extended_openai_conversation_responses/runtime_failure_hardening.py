"""Contain optional runtime failures without changing successful request behavior."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from dataclasses import replace
from functools import wraps
import logging
import sys
from typing import Any, cast

from openai import OpenAIError

from homeassistant.components.conversation import ConversationResult
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent

from . import usage as usage_module
from .const import DOMAIN
from .debug import record_current_provider_failure
from .guest_mode import GUEST_MODE_UNAVAILABLE
from .provider_errors import (
    log_provider_failure,
    provider_user_message,
    request_reauthentication,
)
from .usage import UsageManager

_LOGGER = logging.getLogger(__name__)
_INSTALLED = False
_VOLATILE_USAGE_MANAGERS = f"{DOMAIN}.volatile_usage_managers"
_ORIGINAL_ASYNC_GET_USAGE = usage_module.async_get_usage


class _VolatileUsageStorage:
    """No-op Usage storage used only when persistent telemetry cannot initialize."""

    async def async_load(self) -> dict[str, Any] | None:
        return None

    async def async_save(self, data: dict[str, Any]) -> None:
        del data


async def async_get_usage_safely(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> UsageManager:
    """Return persistent Usage when possible, otherwise one shared volatile manager."""
    key = (entry_id, subentry_id)
    fallbacks: dict[tuple[str, str], UsageManager] = hass.data.setdefault(
        _VOLATILE_USAGE_MANAGERS, {}
    )
    if key in fallbacks:
        return fallbacks[key]

    try:
        return await _ORIGINAL_ASYNC_GET_USAGE(hass, entry_id, subentry_id)
    except Exception:
        # Usage is diagnostic telemetry. A damaged/unavailable Store must not make
        # the conversation agent itself unavailable. Keep accounting in memory for
        # this HA runtime and retry persistent storage after the next restart.
        _LOGGER.exception(
            "Unable to initialize Usage storage; continuing with volatile accounting"
        )
        manager = UsageManager(
            _VolatileUsageStorage(),
            _VolatileUsageStorage(),
            _VolatileUsageStorage(),
            agent_subentry_id=subentry_id,
        )
        await manager.async_initialize()
        fallbacks[key] = manager
        return manager


def _install_usage_startup_fallback() -> None:
    """Replace Usage getter aliases with the non-fatal startup wrapper."""
    current = usage_module.async_get_usage
    if getattr(current, "_extended_openai_failure_fallback", False):
        return

    async_get_usage_safely._extended_openai_failure_fallback = True  # type: ignore[attr-defined]
    usage_module.async_get_usage = async_get_usage_safely

    # Several modules bind async_get_usage at import time. Configuration lifecycle
    # hardening imports conversation before async_setup, so replace every live alias
    # that still points at the getter this wrapper supersedes.
    package_prefix = f"{__package__}."
    for module_name, module in tuple(sys.modules.items()):
        if (
            module is not None
            and module_name.startswith(package_prefix)
            and module.__dict__.get("async_get_usage") is current
        ):
            module.__dict__["async_get_usage"] = async_get_usage_safely


def _conversation_error_result(
    entity: Any, user_input: Any, chat_log: Any, err: OpenAIError | HomeAssistantError
) -> ConversationResult:
    """Build the same Assist error result for failures before or during provider prep."""
    usage = getattr(entity, "_usage", None)
    if usage is not None:
        usage.mark_current_run_failed(type(err).__name__)

    if isinstance(err, OpenAIError):
        request_reauthentication(entity.hass, entity.entry, err)
        record_current_provider_failure(err)
        log_provider_failure(_LOGGER, "OpenAI request preparation failed", err)
        message = (
            "Sorry, I had a problem talking to OpenAI: "
            f"{provider_user_message(err)}"
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


def _install_archive_failure_label() -> None:
    """Report unexpected Archive failures as Archive failures, not Knowledge failures."""
    from .conversation import ExtendedOpenAIAgentEntity

    current = ExtendedOpenAIAgentEntity._execute_function_tool
    if getattr(current, "_extended_openai_archive_failure_label", False):
        return
    original = current

    @wraps(original)
    async def execute_function_tool(
        entity: Any,
        function_tool: dict[str, Any],
        tool_input: Any,
        llm_context: Any,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        function_type = function_tool.get("function", {}).get("type")
        if function_type != "archive":
            return await original(
                entity, function_tool, tool_input, llm_context, exposed_entities
            )

        operation = function_tool.get("function", {}).get("operation", "")
        policy = entity._effective_guest_policy()
        try:
            if policy.guest_active and not entity._guest_integration_allowed(
                "archive", operation
            ):
                raise RuntimeError(GUEST_MODE_UNAVAILABLE)
            result = await entity._async_execute_archive_tool(
                operation, tool_input.tool_args
            )
        except (RuntimeError, ValueError) as err:
            result = {"status": "error", "error": str(err)}
        except Exception:
            _LOGGER.exception("Conversation Archive tool failed")
            result = {
                "status": "unavailable",
                "error": "Conversation Archive is temporarily unavailable",
            }
        return entity._tool_result(tool_input, result)

    execute_function_tool._extended_openai_archive_failure_label = True  # type: ignore[attr-defined]
    setattr(  # noqa: B010
        ExtendedOpenAIAgentEntity, "_execute_function_tool", execute_function_tool
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
    _install_usage_startup_fallback()
    _install_request_preparation_boundary()
    _install_archive_failure_label()
    _install_late_chat_tool_call_id_repair()
    _INSTALLED = True
