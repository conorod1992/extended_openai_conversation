"""Protocol-safe retained Function Tool exchange helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from .exceptions import FunctionNotFound
from .function_call_budget import FunctionCallBudget
from .function_execution import async_validate_function_arguments
from .function_tool_recovery import (
    CorrectableToolFailure,
    ToolRecoveryState,
    bind_tool_recovery_state,
    correctable_unavailable_failure,
    correctable_validation_failure,
    recovery_tool_result,
)
from .function_tool_resolution import latest_function_tool_for_execution
from .ha_llm_tools import is_ha_tool
from .parallel_tool_execution import (
    async_execute_parallel_safe_batch_outcomes,
    resolve_parallel_safe_batch,
)

_MAX_ERROR_TEXT = 512


def _error_text(error: BaseException | None) -> str:
    """Return a bounded model-visible description for a failed exchange."""
    if error is None:
        return "Tool execution was interrupted before completion"
    name = type(error).__name__
    detail = str(error).strip()
    text = f"{name}: {detail}" if detail else name
    if len(text) <= _MAX_ERROR_TEXT:
        return text
    return f"{text[: _MAX_ERROR_TEXT - 1]}…"


def retained_tool_calls_since(
    chat_log: conversation.ChatLog, existing_content_ids: set[int]
) -> list[llm.ToolInput]:
    """Return external tool calls actually retained during one provider round."""
    calls: list[llm.ToolInput] = []
    for content in chat_log.content:
        if id(content) in existing_content_ids:
            continue
        if isinstance(content, conversation.AssistantContent) and content.tool_calls:
            calls.extend(content.tool_calls)
    return calls


def append_unresolved_tool_results(
    chat_log: conversation.ChatLog,
    agent_id: str,
    tool_calls: Iterable[llm.ToolInput],
    *,
    failed_call_id: str | None = None,
    error: BaseException | None = None,
) -> None:
    """Close every still-retained call exactly once before an error escapes.

    Results already recorded by successful calls are preserved. The call that caused
    the abort is represented as an error; other calls that never reached a completed
    result are explicitly marked skipped. If the failure is at provider/round level
    rather than attributable to one call, the first unresolved call carries the error
    and the remaining calls are skipped.
    """
    calls = list(tool_calls)
    if not calls:
        return

    retained_ids = {
        tool_call.id
        for content in chat_log.content
        if isinstance(content, conversation.AssistantContent) and content.tool_calls
        for tool_call in content.tool_calls
    }
    completed_ids = {
        content.tool_call_id
        for content in chat_log.content
        if isinstance(content, conversation.ToolResultContent)
    }
    unresolved = [
        tool_call
        for tool_call in calls
        if tool_call.id in retained_ids and tool_call.id not in completed_ids
    ]
    if not unresolved:
        return

    actual_failed_id = (
        failed_call_id
        if failed_call_id is not None
        and any(call.id == failed_call_id for call in unresolved)
        else unresolved[0].id
    )
    failure_text = _error_text(error)
    failed_name = next(
        (call.tool_name for call in unresolved if call.id == actual_failed_id),
        "another tool call",
    )

    for tool_call in unresolved:
        result: dict[str, Any]
        if tool_call.id == actual_failed_id:
            result = {"status": "error", "error": failure_text}
        else:
            result = {
                "status": "skipped",
                "error": (
                    f"Skipped because tool call `{failed_name}` failed before this "
                    "exchange completed"
                ),
            }
        chat_log.async_add_assistant_content_without_tools(
            conversation.ToolResultContent(
                agent_id=agent_id,
                tool_call_id=tool_call.id,
                tool_name=tool_call.tool_name,
                tool_result={"result": result},
            )
        )


def _index_tools(function_tools: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index effective Function Tools without changing first-definition precedence."""
    indexed: dict[str, dict[str, Any]] = {}
    for function_tool in function_tools:
        name = function_tool.get("spec", {}).get("name")
        if isinstance(name, str):
            indexed.setdefault(name, function_tool)
    return indexed


def _resolve_current_tool(
    entity: Any,
    tool_input: llm.ToolInput,
    request_tools_by_name: dict[str, dict[str, Any]],
    function_tools_factory: Callable[[], list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    """Resolve one request-round call against current effective availability."""
    request_tool = request_tools_by_name.get(tool_input.tool_name)
    if request_tool is None:
        raise FunctionNotFound(tool_input.tool_name)

    candidate = request_tool
    if function_tools_factory is not None:
        current_effective = _index_tools(function_tools_factory())
        current_candidate = current_effective.get(tool_input.tool_name)
        if current_candidate is None:
            raise FunctionNotFound(tool_input.tool_name)
        candidate = current_candidate

    return latest_function_tool_for_execution(entity, candidate)


def _append_recovery(
    chat_log: conversation.ChatLog,
    entity: Any,
    tool_input: llm.ToolInput,
    failure: CorrectableToolFailure,
    recovery_state: ToolRecoveryState,
) -> bool:
    """Append one correctable result only while the independent cap permits it."""
    if not recovery_state.consume():
        return False
    chat_log.async_add_assistant_content_without_tools(
        recovery_tool_result(entity.entity_id, tool_input, failure)
    )
    return True


def _validated_tool_input(
    tool_input: llm.ToolInput, arguments: dict[str, Any]
) -> llm.ToolInput:
    """Preserve call identity while dispatching locally validated/coerced arguments."""
    return llm.ToolInput(
        id=tool_input.id,
        tool_name=tool_input.tool_name,
        tool_args=arguments,
        external=tool_input.external,
    )


async def _async_prepare_recoverable_call(
    entity: Any,
    tool_input: llm.ToolInput,
    request_tools_by_name: dict[str, dict[str, Any]],
    function_tools_factory: Callable[[], list[dict[str, Any]]] | None,
    recovery_state: ToolRecoveryState,
) -> tuple[dict[str, Any], llm.ToolInput] | CorrectableToolFailure:
    """Resolve and validate one call without crossing the side-effect boundary."""
    malformed = recovery_state.pop_malformed(tool_input.id)
    if malformed is not None:
        return malformed

    try:
        function_tool = _resolve_current_tool(
            entity,
            tool_input,
            request_tools_by_name,
            function_tools_factory,
        )
    except FunctionNotFound as err:
        return correctable_unavailable_failure(tool_input.tool_name, err)

    # HA LLM Tools own their live request schema and permission boundary. Their
    # availability is still resolved above, but never reinterpret the HA schema with
    # the integration's configured-tool JSON-Schema subset.
    if is_ha_tool(function_tool):
        return function_tool, tool_input

    try:
        arguments = await async_validate_function_arguments(
            entity.hass,
            function_tool.get("spec", {}),
            tool_input.tool_args,
        )
    except HomeAssistantError as err:
        return correctable_validation_failure(err)

    return function_tool, _validated_tool_input(tool_input, arguments)


async def _async_execute_with_recovery(
    entity: Any,
    chat_log: conversation.ChatLog,
    pending_tool_calls: list[llm.ToolInput],
    request_function_tools: list[dict[str, Any]],
    function_call_budget: FunctionCallBudget,
    llm_context: llm.LLMContext | None,
    exposed_entities: list[dict[str, Any]],
    function_tools_factory: Callable[[], list[dict[str, Any]]] | None,
    recovery_state: ToolRecoveryState,
) -> None:
    """Execute one batch with recovery restricted to explicit pre-dispatch stages."""
    request_tools_by_name = _index_tools(request_function_tools)
    potential_parallel = resolve_parallel_safe_batch(
        pending_tool_calls, request_tools_by_name
    )

    # Preserve the ordinary atomic budget reservation for a proven parallel batch.
    if potential_parallel is not None:
        remaining = function_call_budget.remaining
        try:
            function_call_budget.claim_many(
                tool_input.tool_name for _, tool_input in potential_parallel
            )
        except BaseException as err:
            failed_index = (
                0 if remaining is None else min(remaining, len(potential_parallel) - 1)
            )
            append_unresolved_tool_results(
                chat_log,
                entity.entity_id,
                pending_tool_calls,
                failed_call_id=potential_parallel[failed_index][1].id,
                error=err,
            )
            raise

        prepared: list[tuple[dict[str, Any], llm.ToolInput]] = []
        for tool_input in pending_tool_calls:
            outcome = await _async_prepare_recoverable_call(
                entity,
                tool_input,
                request_tools_by_name,
                function_tools_factory,
                recovery_state,
            )
            if isinstance(outcome, CorrectableToolFailure):
                if not _append_recovery(
                    chat_log, entity, tool_input, outcome, recovery_state
                ):
                    append_unresolved_tool_results(
                        chat_log,
                        entity.entity_id,
                        pending_tool_calls,
                        failed_call_id=tool_input.id,
                        error=outcome.original,
                    )
                    raise outcome.original
                continue
            prepared.append(outcome)

        if not prepared:
            return
        parallel_batch = resolve_parallel_safe_batch(
            [tool_input for _, tool_input in prepared],
            {tool["spec"]["name"]: tool for tool, _ in prepared},
        )
        if parallel_batch is None:
            # Availability can change the effective set but not a tool's safety
            # classification. Fall back serially rather than widening concurrency.
            for function_tool, tool_input in prepared:
                try:
                    with bind_tool_recovery_state(recovery_state):
                        result = await entity._execute_function_tool(
                            function_tool,
                            tool_input,
                            llm_context,
                            exposed_entities,
                        )
                except BaseException as err:
                    append_unresolved_tool_results(
                        chat_log,
                        entity.entity_id,
                        pending_tool_calls,
                        failed_call_id=tool_input.id,
                        error=err,
                    )
                    raise
                chat_log.async_add_assistant_content_without_tools(result)
            return

        try:
            outcomes = await async_execute_parallel_safe_batch_outcomes(
                parallel_batch,
                lambda function_tool, tool_input: _execute_bound(
                    entity,
                    function_tool,
                    tool_input,
                    llm_context,
                    exposed_entities,
                    recovery_state,
                ),
            )
        except BaseException as err:
            append_unresolved_tool_results(
                chat_log,
                entity.entity_id,
                pending_tool_calls,
                error=err,
            )
            raise

        first_error: BaseException | None = None
        for (_, tool_input), outcome in zip(parallel_batch, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                append_unresolved_tool_results(
                    chat_log,
                    entity.entity_id,
                    [tool_input],
                    failed_call_id=tool_input.id,
                    error=outcome,
                )
                if first_error is None:
                    first_error = outcome
            else:
                chat_log.async_add_assistant_content_without_tools(outcome)
        if first_error is not None:
            raise first_error
        return

    # Serial semantics intentionally match the legacy path: each model-requested
    # call consumes its normal budget immediately before its own preparation.
    for tool_input in pending_tool_calls:
        try:
            function_call_budget.claim(tool_input.tool_name)
            outcome = await _async_prepare_recoverable_call(
                entity,
                tool_input,
                request_tools_by_name,
                function_tools_factory,
                recovery_state,
            )
            if isinstance(outcome, CorrectableToolFailure):
                if _append_recovery(
                    chat_log, entity, tool_input, outcome, recovery_state
                ):
                    continue
                raise outcome.original
            function_tool, validated_input = outcome
            with bind_tool_recovery_state(recovery_state):
                tool_result_content = await entity._execute_function_tool(
                    function_tool,
                    validated_input,
                    llm_context,
                    exposed_entities,
                )
        except BaseException as err:
            append_unresolved_tool_results(
                chat_log,
                entity.entity_id,
                pending_tool_calls,
                failed_call_id=tool_input.id,
                error=err,
            )
            raise
        chat_log.async_add_assistant_content_without_tools(tool_result_content)


async def _execute_bound(
    entity: Any,
    function_tool: dict[str, Any],
    tool_input: llm.ToolInput,
    llm_context: llm.LLMContext | None,
    exposed_entities: list[dict[str, Any]],
    recovery_state: ToolRecoveryState,
) -> conversation.ToolResultContent:
    """Execute one prepared call with strict runtime-failure semantics bound."""
    with bind_tool_recovery_state(recovery_state):
        return await entity._execute_function_tool(
            function_tool, tool_input, llm_context, exposed_entities
        )


async def async_execute_tool_exchange(
    entity: Any,
    chat_log: conversation.ChatLog,
    pending_tool_calls: list[llm.ToolInput],
    request_function_tools: list[dict[str, Any]],
    function_call_budget: FunctionCallBudget,
    llm_context: llm.LLMContext | None,
    exposed_entities: list[dict[str, Any]],
    *,
    function_tools_factory: Callable[[], list[dict[str, Any]]] | None = None,
    recovery_state: ToolRecoveryState | None = None,
) -> None:
    """Execute one provider tool batch while keeping retained history complete."""
    if not pending_tool_calls:
        return
    if recovery_state is not None and recovery_state.enabled:
        await _async_execute_with_recovery(
            entity,
            chat_log,
            pending_tool_calls,
            request_function_tools,
            function_call_budget,
            llm_context,
            exposed_entities,
            function_tools_factory,
            recovery_state,
        )
        return

    # Disabled mode deliberately retains the previous fail-fast path byte-for-byte in
    # behavior so opting out cannot change execution, budgets, or error history.
    request_tools_by_name = _index_tools(request_function_tools)
    potential_parallel = resolve_parallel_safe_batch(
        pending_tool_calls, request_tools_by_name
    )
    parallel_batch = None

    if potential_parallel is not None:
        current_tools: dict[str, dict[str, Any]] = {}
        resolving_call: llm.ToolInput | None = None
        try:
            for resolving_call in pending_tool_calls:
                current_tools[resolving_call.tool_name] = _resolve_current_tool(
                    entity,
                    resolving_call,
                    request_tools_by_name,
                    function_tools_factory,
                )
        except BaseException as err:
            append_unresolved_tool_results(
                chat_log,
                entity.entity_id,
                pending_tool_calls,
                failed_call_id=resolving_call.id
                if resolving_call is not None
                else None,
                error=err,
            )
            raise
        parallel_batch = resolve_parallel_safe_batch(pending_tool_calls, current_tools)

    if parallel_batch is not None:
        remaining = function_call_budget.remaining
        try:
            function_call_budget.claim_many(
                tool_input.tool_name for _, tool_input in parallel_batch
            )
        except BaseException as err:
            failed_index = (
                0 if remaining is None else min(remaining, len(parallel_batch) - 1)
            )
            append_unresolved_tool_results(
                chat_log,
                entity.entity_id,
                pending_tool_calls,
                failed_call_id=parallel_batch[failed_index][1].id,
                error=err,
            )
            raise

        try:
            outcomes = await async_execute_parallel_safe_batch_outcomes(
                parallel_batch,
                lambda function_tool, tool_input: entity._execute_function_tool(
                    function_tool,
                    tool_input,
                    llm_context,
                    exposed_entities,
                ),
            )
        except BaseException as err:
            append_unresolved_tool_results(
                chat_log,
                entity.entity_id,
                pending_tool_calls,
                error=err,
            )
            raise

        first_error: BaseException | None = None
        for (_, tool_input), outcome in zip(parallel_batch, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                append_unresolved_tool_results(
                    chat_log,
                    entity.entity_id,
                    [tool_input],
                    failed_call_id=tool_input.id,
                    error=outcome,
                )
                if first_error is None:
                    first_error = outcome
            else:
                chat_log.async_add_assistant_content_without_tools(outcome)
        if first_error is not None:
            raise first_error
        return

    for tool_input in pending_tool_calls:
        try:
            function_tool = _resolve_current_tool(
                entity,
                tool_input,
                request_tools_by_name,
                function_tools_factory,
            )
            function_call_budget.claim(tool_input.tool_name)
            tool_result_content = await entity._execute_function_tool(
                function_tool,
                tool_input,
                llm_context,
                exposed_entities,
            )
        except BaseException as err:
            append_unresolved_tool_results(
                chat_log,
                entity.entity_id,
                pending_tool_calls,
                failed_call_id=tool_input.id,
                error=err,
            )
            raise
        chat_log.async_add_assistant_content_without_tools(tool_result_content)
