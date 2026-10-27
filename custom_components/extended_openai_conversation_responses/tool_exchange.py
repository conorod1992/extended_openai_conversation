"""Protocol-safe retained Function Tool exchange helpers."""

from __future__ import annotations

from asyncio import CancelledError
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any, cast

from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from .exceptions import FunctionNotFound
from .function_call_budget import FunctionCallBudget
from .function_execution import (
    async_validate_function_arguments,
    validated_function_call,
)
from .function_tool_recovery import (
    CorrectableToolFailure,
    ToolRecoveryState,
    bind_tool_recovery_state,
    correctable_validation_failure,
    recovery_tool_result,
)
from .function_tool_resolution import (
    current_configuration_data,
    latest_function_tool_for_execution,
)
from .ha_llm_tools import is_ha_tool
from .ha_tool_result_compat import (
    HAToolResultError,
    is_tool_result_content,
    make_tool_result_content,
)
from .parallel_tool_execution import (
    async_execute_parallel_safe_batch_outcomes,
    resolve_parallel_safe_batch,
)
from .public_errors import sanitize_backend_error
from .tool_replay_guard import was_unacknowledged_equivalent, with_effect_replay_scope

_MAX_ERROR_TEXT = 512


def _error_text(error: BaseException | None) -> str:
    """Return a bounded model-visible description for a failed exchange."""
    if isinstance(error, CancelledError):
        return "CancelledError: Tool execution was interrupted before completion"
    if error is None or not isinstance(error, Exception):
        return "Tool execution was interrupted before completion"
    # History repair is a last public boundary, including non-HA dependency
    # failures and errors escaping callers outside the backend adapter.
    error = sanitize_backend_error(error)
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


def validate_tool_call_ids(tool_calls: Iterable[llm.ToolInput]) -> None:
    """Reject one provider round whose call IDs cannot be paired unambiguously."""
    seen: set[str] = set()
    for tool_call in tool_calls:
        if (
            not isinstance(tool_call.id, str)
            or not 1 <= len(tool_call.id) <= 1024
            or not tool_call.id.isprintable()
        ):
            raise HomeAssistantError("Provider returned an invalid tool call id")
        if tool_call.id in seen:
            raise HomeAssistantError(
                f"Provider returned duplicate tool call id `{tool_call.id}`"
            )
        seen.add(tool_call.id)


def downgrade_native_response_output(
    chat_log: conversation.ChatLog, existing_content_ids: set[int]
) -> None:
    """Keep text/calls while discarding one incomplete native output graph."""
    retained: list[conversation.Content] = []
    for content in chat_log.content:
        if (
            id(content) not in existing_content_ids
            and isinstance(content, conversation.AssistantContent)
            and content.native is not None
        ):
            if content.tool_calls or content.content:
                retained.append(replace(content, native=None))
        else:
            retained.append(content)
    chat_log.content[:] = retained


def _prune_repeated_completed_calls(
    chat_log: conversation.ChatLog,
    calls: list[llm.ToolInput],
    existing_content_ids: set[int] | None = None,
) -> bool:
    """Remove only requested occurrences that follow an earlier answered ID.

    Parser failures can retain a duplicate before dispatch validation runs. Share
    its cleanup with that validation, preserving the original call and result.
    A completed native response needs its explicit round boundary when pruning
    would otherwise leave dependent reasoning or hosted-tool items behind.
    """
    requested = {id(call) for call in calls}
    answered: set[str] = set()
    retained = []
    changed = False
    for content in chat_log.content:
        if isinstance(content, conversation.AssistantContent) and content.tool_calls:
            remaining = [
                call
                for call in content.tool_calls
                if not (id(call) in requested and call.id in answered)
            ]
            if len(remaining) != len(content.tool_calls):
                content.tool_calls[:] = remaining
                changed = True
                if not remaining and not getattr(content, "content", None):
                    continue
        elif is_tool_result_content(content):
            call_id = getattr(content, "tool_call_id", None)
            if isinstance(call_id, str):
                answered.add(call_id)
        retained.append(content)
    if changed:
        chat_log.content[:] = retained
        if existing_content_ids is not None:
            downgrade_native_response_output(chat_log, existing_content_ids)
    return changed


def append_unresolved_tool_results(
    chat_log: conversation.ChatLog,
    agent_id: str,
    tool_calls: Iterable[llm.ToolInput],
    *,
    failed_call_id: str | None = None,
    error: BaseException | None = None,
    existing_content_ids: set[int] | None = None,
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

    _prune_repeated_completed_calls(chat_log, calls, existing_content_ids)
    # A malformed later response can retain a new occurrence of an earlier ID
    # before the dispatch-time duplicate guard runs. Pair each result only with
    # an earlier unanswered occurrence, never with every call sharing that ID.
    pending: dict[str, deque[llm.ToolInput]] = {}
    for content in chat_log.content:
        if isinstance(content, conversation.AssistantContent) and content.tool_calls:
            for tool_call in content.tool_calls:
                pending.setdefault(tool_call.id, deque()).append(tool_call)
        elif is_tool_result_content(content):
            tool_call_id = getattr(content, "tool_call_id", None)
            if isinstance(tool_call_id, str) and (
                matching := pending.get(tool_call_id)
            ):
                matching.popleft()
    unresolved_objects = {
        id(tool_call) for matching in pending.values() for tool_call in matching
    }
    unresolved = [
        tool_call for tool_call in calls if id(tool_call) in unresolved_objects
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
            if isinstance(error, HAToolResultError):
                result = error.private_result(failure_text)
        else:
            result = {
                "status": "skipped",
                "error": (
                    f"Skipped because tool call `{failed_name}` failed before this "
                    "exchange completed"
                ),
            }
        chat_log.async_add_assistant_content_without_tools(
            make_tool_result_content(
                agent_id=agent_id,
                tool_call_id=tool_call.id,
                tool_name=tool_call.tool_name,
                tool_result={"result": result},
                error=True,
            )
        )


def validate_retained_tool_calls(
    entity: Any,
    chat_log: conversation.ChatLog,
    calls: list[llm.ToolInput],
    *,
    request_config_data: Any | None = None,
    existing_content_ids: set[int] | None = None,
    consumed_calls: Iterable[llm.ToolInput] = (),
) -> None:
    """Validate the whole provider round before ordinary or control dispatch."""
    if not calls:
        return
    if (
        request_config_data is not None
        and current_configuration_data(entity) is not request_config_data
    ):
        error = HomeAssistantError(
            "Function Tool configuration changed during the provider request; the pending tool call was stopped"
        )
        append_unresolved_tool_results(
            chat_log,
            entity.entity_id,
            calls,
            error=error,
            existing_content_ids=existing_content_ids,
        )
        raise error
    validate_tool_call_ids(calls)
    # Some API adapters consume control occurrences into ordinary text instead
    # of retaining a call/result pair. Only those exact objects may reuse an
    # earlier ID; configuration and within-round validation still cover them.
    consumed_objects = {id(call) for call in consumed_calls}
    prior_results = {
        call_id
        for content in getattr(chat_log, "content", ())
        if is_tool_result_content(content)
        and isinstance((call_id := getattr(content, "tool_call_id", None)), str)
    }
    if any(
        id(call) not in consumed_objects and call.id in prior_results for call in calls
    ):
        _prune_repeated_completed_calls(chat_log, calls, existing_content_ids)
        error = HomeAssistantError("Provider repeated a completed tool call id")
        append_unresolved_tool_results(
            chat_log,
            entity.entity_id,
            calls,
            error=error,
            existing_content_ids=existing_content_ids,
        )
        raise error


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
    check_aba = getattr(entity, "_assert_no_aba_configuration", None)
    if callable(check_aba):
        check_aba()
    request_tool = request_tools_by_name.get(tool_input.tool_name)
    if request_tool is None:
        raise FunctionNotFound(tool_input.tool_name)

    candidate = request_tool
    if function_tools_factory is not None:
        current_effective = _index_tools(function_tools_factory())
        current_candidate = current_effective.get(tool_input.tool_name)
        if current_candidate is None:
            raise FunctionNotFound(tool_input.tool_name)
        same_ha_reference = request_tool.get("function", {}).get(
            "type"
        ) == "ha_llm" and current_candidate.get("function") == request_tool.get(
            "function"
        )
        if current_candidate != request_tool and not same_ha_reference:
            raise FunctionNotFound(tool_input.tool_name)

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


async def _async_validate_recoverable_call(
    entity: Any,
    function_tool: dict[str, Any],
    tool_input: llm.ToolInput,
    recovery_state: ToolRecoveryState,
) -> llm.ToolInput | CorrectableToolFailure:
    """Validate one already-resolved call without crossing dispatch."""
    malformed = recovery_state.pop_malformed(tool_input.id)
    if malformed is not None:
        return malformed

    # HA LLM Tools own their live schema and permission boundary. Do not
    # reinterpret that external schema with our configured-tool validator.
    if is_ha_tool(function_tool):
        return tool_input

    try:
        arguments = await async_validate_function_arguments(
            entity.hass,
            function_tool.get("spec", {}),
            tool_input.tool_args,
            distinguish_infrastructure=True,
        )
    except HomeAssistantError as err:
        return correctable_validation_failure(err)

    return _validated_tool_input(tool_input, arguments)


async def _execute_bound(
    entity: Any,
    function_tool: dict[str, Any],
    tool_input: llm.ToolInput,
    llm_context: llm.LLMContext | None,
    exposed_entities: list[dict[str, Any]],
    recovery_state: ToolRecoveryState,
    retained_tool_input: llm.ToolInput,
) -> conversation.ToolResultContent:
    """Execute one prepared call with strict runtime-failure semantics bound."""
    with (
        bind_tool_recovery_state(recovery_state),
        validated_function_call(
            tool_input,
            function_tool.get("spec", {}),
            retained_tool_input=retained_tool_input,
        ),
    ):
        return cast(
            conversation.ToolResultContent,
            await entity._execute_function_tool(
                function_tool, tool_input, llm_context, exposed_entities
            ),
        )


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
    """Execute one batch with recovery restricted to pre-dispatch stages."""
    request_tools_by_name = _index_tools(request_function_tools)
    potential_parallel = resolve_parallel_safe_batch(
        pending_tool_calls, request_tools_by_name
    )
    parallel_batch = None

    # Match the legacy budget boundary exactly: current availability is
    # resolved before any budget claim. Availability may encode security or
    # Guest policy, so failures here are always fail-fast and consume no call.
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
                failed_call_id=(
                    resolving_call.id if resolving_call is not None else None
                ),
                error=err,
            )
            raise
        parallel_batch = resolve_parallel_safe_batch(pending_tool_calls, current_tools)

    if parallel_batch is not None:
        prepared: list[tuple[dict[str, Any], llm.ToolInput]] = []
        retained_calls: dict[int, llm.ToolInput] = {}
        recovery_results: dict[str, conversation.ToolResultContent] = {}
        validating_call: llm.ToolInput | None = None
        try:
            for function_tool, tool_input in parallel_batch:
                validating_call = tool_input
                validation_outcome = await _async_validate_recoverable_call(
                    entity, function_tool, tool_input, recovery_state
                )
                if isinstance(validation_outcome, CorrectableToolFailure):
                    if not recovery_state.consume():
                        append_unresolved_tool_results(
                            chat_log,
                            entity.entity_id,
                            pending_tool_calls,
                            failed_call_id=tool_input.id,
                            error=validation_outcome.original,
                        )
                        raise validation_outcome.original
                    recovery_results[tool_input.id] = recovery_tool_result(
                        entity.entity_id, tool_input, validation_outcome
                    )
                else:
                    prepared.append((function_tool, validation_outcome))
                    retained_calls[id(validation_outcome)] = tool_input
        except BaseException as err:
            append_unresolved_tool_results(
                chat_log,
                entity.entity_id,
                pending_tool_calls,
                failed_call_id=(
                    validating_call.id if validating_call is not None else None
                ),
                error=err,
            )
            raise

        execution_outcomes: dict[
            str, conversation.ToolResultContent | BaseException
        ] = {}
        if prepared:
            remaining = function_call_budget.remaining
            try:
                function_call_budget.claim_many(
                    tool_input.tool_name for _, tool_input in prepared
                )
            except BaseException as err:
                append_unresolved_tool_results(
                    chat_log,
                    entity.entity_id,
                    pending_tool_calls,
                    failed_call_id=prepared[
                        0 if remaining is None else min(remaining, len(prepared) - 1)
                    ][1].id,
                    error=err,
                )
                raise
            try:
                outcomes = await async_execute_parallel_safe_batch_outcomes(
                    prepared,
                    lambda function_tool, tool_input: _execute_bound(
                        entity,
                        function_tool,
                        tool_input,
                        llm_context,
                        exposed_entities,
                        recovery_state,
                        retained_calls[id(tool_input)],
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
            execution_outcomes = {
                tool_input.id: execution_outcome
                for (_, tool_input), execution_outcome in zip(
                    prepared, outcomes, strict=True
                )
            }

        first_error: BaseException | None = None
        for tool_input in pending_tool_calls:
            recovery_result = recovery_results.get(tool_input.id)
            if recovery_result is not None:
                chat_log.async_add_assistant_content_without_tools(recovery_result)
                continue
            execution_outcome = execution_outcomes.get(tool_input.id)
            if isinstance(execution_outcome, BaseException):
                append_unresolved_tool_results(
                    chat_log,
                    entity.entity_id,
                    [tool_input],
                    failed_call_id=tool_input.id,
                    error=execution_outcome,
                )
                if first_error is None:
                    first_error = execution_outcome
            elif execution_outcome is not None:
                chat_log.async_add_assistant_content_without_tools(execution_outcome)
        if first_error is not None:
            raise first_error
        return

    # Preserve the old serial ordering: resolve, claim, then cross into
    # validation/recovery and finally dispatch.
    for tool_input in pending_tool_calls:
        try:
            function_tool = _resolve_current_tool(
                entity,
                tool_input,
                request_tools_by_name,
                function_tools_factory,
            )
            outcome = await _async_validate_recoverable_call(
                entity, function_tool, tool_input, recovery_state
            )
            if isinstance(outcome, CorrectableToolFailure):
                if _append_recovery(
                    chat_log, entity, tool_input, outcome, recovery_state
                ):
                    continue
                raise outcome.original
            function_call_budget.claim(tool_input.tool_name)
            tool_result_content = await _execute_bound(
                entity,
                function_tool,
                outcome,
                llm_context,
                exposed_entities,
                recovery_state,
                tool_input,
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


@with_effect_replay_scope
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
    request_config_data: Any | None = None,
    existing_content_ids: set[int] | None = None,
) -> None:
    """Execute one provider tool batch while keeping retained history complete."""
    if not pending_tool_calls:
        return
    validate_retained_tool_calls(
        entity,
        chat_log,
        pending_tool_calls,
        request_config_data=request_config_data,
        existing_content_ids=existing_content_ids,
    )
    if any(
        was_unacknowledged_equivalent(entity, chat_log, call)
        for call in pending_tool_calls
    ):
        error = HomeAssistantError(
            "Provider repeated a tool call whose prior result was not acknowledged. "
            "Start a new conversation, or complete a turn without this tool before retrying"
        )
        append_unresolved_tool_results(
            chat_log, entity.entity_id, pending_tool_calls, error=error
        )
        raise error
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
