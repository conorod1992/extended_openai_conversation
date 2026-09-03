"""Keep context truncation reliable when providers omit stream usage metadata.

Provider-reported usage remains authoritative for accounting. A conservative local
estimate is attached only to the in-flight RequestUsage object so the existing
context-management decision can still fire; UsageManager is wrapped to strip that
estimate before totals/details are persisted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
import json
import logging
import math
from typing import Any

from .usage import RequestUsage, UsageManager, extract_usage

_LOGGER = logging.getLogger(__name__)
_INSTALLED = False
_LOCAL_ESTIMATE_DETAIL = "__extended_openai_local_context_estimate"


@dataclass(slots=True)
class _EstimateState:
    """Request-scoped inputs needed to approximate the current provider payload."""

    entity: Any
    function_tools: list[dict[str, Any]]
    function_tools_factory: Callable[[], list[dict[str, Any]]] | None
    conditional_continue: bool
    options: Mapping[str, Any]


_CURRENT_ESTIMATE_STATE: ContextVar[_EstimateState | None] = ContextVar(
    "extended_openai_context_estimate_state", default=None
)


def _has_token_count(usage: RequestUsage) -> bool:
    """Return whether normalized provider metadata contains a usable token count."""
    return any(
        (
            usage.input_tokens,
            usage.output_tokens,
            usage.total_tokens,
            usage.cached_input_tokens,
            usage.reasoning_tokens,
        )
    )


def _local_estimate(usage: RequestUsage | None) -> int | None:
    """Return the request-local context estimate when one is currently active."""
    if usage is None:
        return None
    value = usage.details.get(_LOCAL_ESTIMATE_DETAIL)
    return value if isinstance(value, int) and value > 0 else None


def _restore_local_estimate(usage: RequestUsage | None, estimate: int | None) -> None:
    """Restore an estimate if a terminal provider event replaced it with zeros."""
    if usage is None or estimate is None or _has_token_count(usage):
        return
    usage.input_tokens = estimate
    usage.output_tokens = 0
    usage.total_tokens = estimate
    usage.cached_input_tokens = 0
    usage.reasoning_tokens = 0
    usage.details = {_LOCAL_ESTIMATE_DETAIL: estimate}


def _copy_usage(target: RequestUsage, source: RequestUsage) -> None:
    """Replace an in-flight estimate with provider-reported usage."""
    target.input_tokens = source.input_tokens
    target.output_tokens = source.output_tokens
    target.total_tokens = source.total_tokens
    target.cached_input_tokens = source.cached_input_tokens
    target.reasoning_tokens = source.reasoning_tokens
    target.details = dict(source.details)


def _capture_provider_usage(target: RequestUsage | None, raw_usage: Any) -> bool:
    """Normalize provider usage from any stream position into one request object."""
    if target is None or raw_usage is None:
        return False
    normalized = extract_usage(raw_usage)
    if not _has_token_count(normalized):
        return False
    _copy_usage(target, normalized)
    return True


def _serialized_characters(value: Any) -> tuple[int, int]:
    """Return total and non-ASCII characters for deterministic JSON serialization."""
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    non_ascii = sum(ord(character) > 127 for character in serialized)
    return len(serialized), non_ascii


def estimate_provider_input_tokens(input_value: Any, tools: Any = None) -> int:
    """Conservatively estimate provider input tokens without a tokenizer dependency.

    ASCII-heavy request JSON is budgeted at roughly three characters per token.
    Non-ASCII characters are budgeted more aggressively because many tokenizers encode
    them at one or more tokens per character. This estimate is intentionally used only
    as a safety fallback for truncation and is never reported as provider/billing usage.
    """
    input_characters, input_non_ascii = _serialized_characters(input_value)
    tool_characters = 0
    tool_non_ascii = 0
    if tools:
        tool_characters, tool_non_ascii = _serialized_characters(tools)

    total_characters = input_characters + tool_characters
    non_ascii = input_non_ascii + tool_non_ascii
    ascii_characters = max(0, total_characters - non_ascii)
    return max(1, math.ceil(ascii_characters / 3) + (non_ascii * 2))


def _request_tools(state: _EstimateState, api_mode: str) -> list[dict[str, Any]]:
    """Build a conservative view of tools available for the current provider round."""
    from .request import (
        CONTINUE_CONVERSATION_TOOL,
        build_provider_request_snapshot,
        format_function_tools,
    )

    try:
        function_tools = (
            state.function_tools_factory()
            if state.function_tools_factory is not None
            else state.function_tools
        )
    except Exception:
        # Estimation must never become a new failure mode. Falling back to the base
        # list can undercount a newly loaded group, but remains much safer than zero.
        _LOGGER.debug(
            "Unable to refresh Function Tools for local context estimate",
            exc_info=True,
        )
        function_tools = state.function_tools

    effective_functions = [
        *function_tools,
        *([CONTINUE_CONVERSATION_TOOL] if state.conditional_continue else []),
    ]
    tools = format_function_tools(effective_functions, api_mode)

    try:
        snapshot = build_provider_request_snapshot(
            state.options, getattr(state.entity.entry, "data", {})
        )
        if snapshot.provider_tools and state.entity._provider_tool_allowed(
            "web_search"
        ):
            tools = [*snapshot.provider_tools, *tools]
    except Exception:
        # The live request path already validates provider-owned tools. A sizing-only
        # helper should degrade rather than mask the real request/error behavior.
        _LOGGER.debug(
            "Unable to include provider-owned tools in local context estimate",
            exc_info=True,
        )
    return tools


def _estimate_current_request(
    chat_log: Any, request_usage: RequestUsage | None, api_mode: str
) -> None:
    """Seed one in-flight request with a local estimate until real usage arrives."""
    if request_usage is None or _has_token_count(request_usage):
        return
    state = _CURRENT_ESTIMATE_STATE.get()
    if state is None:
        return

    from . import entity as entity_module
    from .const import CONF_SHORTEN_TOOL_CALL_ID, DEFAULT_SHORTEN_TOOL_CALL_ID

    try:
        input_value: Any
        if api_mode == "responses":
            input_value = entity_module._convert_content_to_responses_param(
                chat_log.content
            )
        else:
            input_value = entity_module._convert_content_to_param(
                chat_log.content,
                bool(
                    state.options.get(
                        CONF_SHORTEN_TOOL_CALL_ID, DEFAULT_SHORTEN_TOOL_CALL_ID
                    )
                ),
            )
        estimate = estimate_provider_input_tokens(
            input_value, _request_tools(state, api_mode)
        )
    except Exception:
        _LOGGER.debug("Unable to estimate provider input size", exc_info=True)
        return

    request_usage.input_tokens = estimate
    request_usage.total_tokens = estimate
    request_usage.details = {_LOCAL_ESTIMATE_DETAIL: estimate}


def usage_for_accounting(usage: RequestUsage | None) -> RequestUsage | None:
    """Remove local context estimates before provider-usage accounting persists them."""
    if usage is None or _LOCAL_ESTIMATE_DETAIL not in usage.details:
        return usage
    return RequestUsage()


def install_context_usage_hardening() -> None:
    """Install usage normalization and estimate fallback once."""
    global _INSTALLED
    if _INSTALLED:
        return

    from . import entity as entity_module

    entity_type: Any = entity_module.ExtendedOpenAIBaseLLMEntity
    original_handle = entity_type._async_handle_chat_log
    original_chat_transform = entity_type._transform_chat_stream
    original_responses_transform = entity_type._transform_responses_stream
    original_record_request = UsageManager.async_record_request

    async def handle_with_estimate_state(
        entity: Any,
        chat_log: Any,
        function_tools: list[dict[str, Any]],
        exposed_entities: list[dict[str, Any]],
        llm_context: Any = None,
        structure_name: str | None = None,
        structure: Any = None,
        conditional_continue: bool = False,
        function_tools_factory: Callable[[], list[dict[str, Any]]] | None = None,
        function_group_loader: Callable[[Any], dict[str, Any]] | None = None,
        request_options: Mapping[str, Any] | None = None,
    ) -> Any:
        options = request_options or entity.subentry.data
        token = _CURRENT_ESTIMATE_STATE.set(
            _EstimateState(
                entity=entity,
                function_tools=function_tools,
                function_tools_factory=function_tools_factory,
                conditional_continue=conditional_continue,
                options=options,
            )
        )
        try:
            return await original_handle(
                entity,
                chat_log,
                function_tools,
                exposed_entities,
                llm_context,
                structure_name,
                structure,
                conditional_continue,
                function_tools_factory,
                function_group_loader,
                request_options,
            )
        finally:
            _CURRENT_ESTIMATE_STATE.reset(token)

    async def chat_transform_with_usage(
        entity: Any,
        chat_log: Any,
        result: Any,
        request_usage: RequestUsage | None = None,
    ) -> AsyncIterator[Any]:
        _estimate_current_request(chat_log, request_usage, "chat_completions")

        async def normalized_stream() -> AsyncIterator[Any]:
            async for chunk in result:
                estimate = _local_estimate(request_usage)
                raw_usage = getattr(chunk, "usage", None)
                captured = _capture_provider_usage(request_usage, raw_usage)
                # The original transformer handles the standard final usage-only
                # chunk. Trace only non-standard usage attached to a normal choice.
                if captured and getattr(chunk, "choices", None):
                    assert request_usage is not None
                    chat_log.async_trace(
                        {
                            "stats": {
                                "input_tokens": request_usage.input_tokens,
                                "output_tokens": request_usage.output_tokens,
                            }
                        }
                    )
                yield chunk
                if not captured:
                    _restore_local_estimate(request_usage, estimate)

        async for item in original_chat_transform(
            entity, chat_log, normalized_stream(), request_usage
        ):
            yield item

    async def responses_transform_with_usage(
        entity: Any,
        chat_log: Any,
        result: Any,
        request_usage: RequestUsage | None = None,
    ) -> AsyncIterator[Any]:
        _estimate_current_request(chat_log, request_usage, "responses")

        async def normalized_stream() -> AsyncIterator[Any]:
            async for event in result:
                estimate = _local_estimate(request_usage)
                response = getattr(event, "response", None)
                raw_usage = getattr(response, "usage", None)
                if raw_usage is None:
                    raw_usage = getattr(event, "usage", None)
                captured = _capture_provider_usage(request_usage, raw_usage)
                event_type = getattr(event, "type", "")
                if captured and event_type not in {
                    "response.completed",
                    "response.incomplete",
                }:
                    assert request_usage is not None
                    chat_log.async_trace(
                        {
                            "stats": {
                                "input_tokens": request_usage.input_tokens,
                                "output_tokens": request_usage.output_tokens,
                            }
                        }
                    )
                yield event
                if not captured:
                    _restore_local_estimate(request_usage, estimate)

        async for item in original_responses_transform(
            entity, chat_log, normalized_stream(), request_usage
        ):
            yield item

    async def record_request_without_estimate(
        manager: UsageManager, *args: Any, **kwargs: Any
    ) -> None:
        if "usage" in kwargs:
            kwargs["usage"] = usage_for_accounting(kwargs["usage"])
        await original_record_request(manager, *args, **kwargs)

    entity_type._async_handle_chat_log = handle_with_estimate_state
    entity_type._transform_chat_stream = chat_transform_with_usage
    entity_type._transform_responses_stream = responses_transform_with_usage
    UsageManager.async_record_request = record_request_without_estimate  # type: ignore[assignment]
    _INSTALLED = True
