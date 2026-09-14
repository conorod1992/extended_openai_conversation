"""Focused failure-path coverage for retained Function Tool exchanges."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from custom_components.extended_openai_conversation_responses import tool_exchange
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    CorrectableToolFailure,
    ToolRecoveryState,
)


def _tool_call(call_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        tool_name=f"tool_{call_id}",
        tool_args={},
        external=True,
    )


def _tool(call_id: str) -> dict:
    return {"spec": {"name": f"tool_{call_id}"}}


def _harness(call_ids: tuple[str, ...] = ("a", "b")):
    pending = [_tool_call(call_id) for call_id in call_ids]
    tools = [_tool(call_id) for call_id in call_ids]
    pairs = list(zip(tools, pending, strict=True))
    chat_log = SimpleNamespace(
        async_add_assistant_content_without_tools=MagicMock()
    )
    entity = SimpleNamespace(
        entity_id="conversation.test",
        hass=object(),
        _execute_function_tool=AsyncMock(),
    )
    budget = SimpleNamespace(
        remaining=None,
        claim=MagicMock(),
        claim_many=MagicMock(),
    )
    return pending, tools, pairs, chat_log, entity, budget


@pytest.mark.parametrize("recovery_enabled", [False, True])
@pytest.mark.asyncio
async def test_parallel_resolution_failure_closes_batch_before_budget_claim(
    monkeypatch: pytest.MonkeyPatch, recovery_enabled: bool
) -> None:
    """A current-tool lookup failure closes the full batch before claiming budget."""
    pending, tools, pairs, chat_log, entity, budget = _harness()
    error = RuntimeError("current tool lookup failed")
    unresolved = MagicMock()

    monkeypatch.setattr(
        tool_exchange, "resolve_parallel_safe_batch", MagicMock(return_value=pairs)
    )

    def resolve_current(_entity, tool_input, *_args):
        if tool_input.id == "b":
            raise error
        return tools[0]

    monkeypatch.setattr(tool_exchange, "_resolve_current_tool", resolve_current)
    monkeypatch.setattr(tool_exchange, "append_unresolved_tool_results", unresolved)
    execute_parallel = AsyncMock()
    monkeypatch.setattr(
        tool_exchange, "async_execute_parallel_safe_batch_outcomes", execute_parallel
    )

    recovery_state = ToolRecoveryState(enabled=True) if recovery_enabled else None
    with pytest.raises(RuntimeError) as exc_info:
        await tool_exchange.async_execute_tool_exchange(
            entity,
            chat_log,
            pending,
            tools,
            budget,
            None,
            [],
            recovery_state=recovery_state,
        )

    assert exc_info.value is error
    unresolved.assert_called_once_with(
        chat_log,
        entity.entity_id,
        pending,
        failed_call_id="b",
        error=error,
    )
    budget.claim_many.assert_not_called()
    budget.claim.assert_not_called()
    execute_parallel.assert_not_awaited()


@pytest.mark.parametrize(
    ("remaining", "expected_failed_call_id"),
    [(None, "a"), (1, "b")],
)
@pytest.mark.asyncio
async def test_recovery_parallel_budget_failure_marks_exhausted_call(
    monkeypatch: pytest.MonkeyPatch,
    remaining: int | None,
    expected_failed_call_id: str,
) -> None:
    """Parallel budget failure is attributed without crossing validation/dispatch."""
    pending, tools, pairs, chat_log, entity, budget = _harness()
    error = RuntimeError("budget exhausted")
    budget.remaining = remaining
    budget.claim_many.side_effect = error
    unresolved = MagicMock()

    monkeypatch.setattr(
        tool_exchange, "resolve_parallel_safe_batch", MagicMock(return_value=pairs)
    )
    monkeypatch.setattr(
        tool_exchange,
        "_resolve_current_tool",
        MagicMock(side_effect=lambda _entity, tool_input, *_args: tools[0] if tool_input.id == "a" else tools[1]),
    )
    monkeypatch.setattr(tool_exchange, "append_unresolved_tool_results", unresolved)
    validate = AsyncMock()
    execute_parallel = AsyncMock()
    monkeypatch.setattr(tool_exchange, "_async_validate_recoverable_call", validate)
    monkeypatch.setattr(
        tool_exchange, "async_execute_parallel_safe_batch_outcomes", execute_parallel
    )

    with pytest.raises(RuntimeError) as exc_info:
        await tool_exchange.async_execute_tool_exchange(
            entity,
            chat_log,
            pending,
            tools,
            budget,
            None,
            [],
            recovery_state=ToolRecoveryState(enabled=True),
        )

    assert exc_info.value is error
    unresolved.assert_called_once_with(
        chat_log,
        entity.entity_id,
        pending,
        failed_call_id=expected_failed_call_id,
        error=error,
    )
    validate.assert_not_awaited()
    execute_parallel.assert_not_awaited()
    entity._execute_function_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_parallel_fully_recovered_batch_skips_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch resolved entirely by pre-dispatch recovery never enters execution."""
    pending, tools, pairs, chat_log, entity, budget = _harness()
    failures = [
        CorrectableToolFailure(
            code="invalid_arguments",
            stage="pre_dispatch_validation",
            message=f"bad {tool_input.id}",
            original=ValueError(f"bad {tool_input.id}"),
        )
        for tool_input in pending
    ]

    monkeypatch.setattr(
        tool_exchange, "resolve_parallel_safe_batch", MagicMock(return_value=pairs)
    )
    monkeypatch.setattr(
        tool_exchange,
        "_resolve_current_tool",
        MagicMock(side_effect=lambda _entity, tool_input, *_args: tools[0] if tool_input.id == "a" else tools[1]),
    )
    validate = AsyncMock(side_effect=failures)
    monkeypatch.setattr(tool_exchange, "_async_validate_recoverable_call", validate)
    monkeypatch.setattr(
        tool_exchange,
        "recovery_tool_result",
        MagicMock(
            side_effect=lambda _agent_id, tool_input, _failure: f"recovery-{tool_input.id}"
        ),
    )
    execute_parallel = AsyncMock()
    monkeypatch.setattr(
        tool_exchange, "async_execute_parallel_safe_batch_outcomes", execute_parallel
    )
    unresolved = MagicMock()
    monkeypatch.setattr(tool_exchange, "append_unresolved_tool_results", unresolved)

    state = ToolRecoveryState(enabled=True)
    await tool_exchange.async_execute_tool_exchange(
        entity,
        chat_log,
        pending,
        tools,
        budget,
        None,
        [],
        recovery_state=state,
    )

    assert state.used == 2
    execute_parallel.assert_not_awaited()
    entity._execute_function_tool.assert_not_awaited()
    unresolved.assert_not_called()
    assert chat_log.async_add_assistant_content_without_tools.call_args_list == [
        call("recovery-a"),
        call("recovery-b"),
    ]


@pytest.mark.parametrize("recovery_enabled", [False, True])
@pytest.mark.asyncio
async def test_parallel_executor_failure_closes_entire_batch(
    monkeypatch: pytest.MonkeyPatch, recovery_enabled: bool
) -> None:
    """A batch-executor failure closes every pending call in either execution mode."""
    pending, tools, pairs, chat_log, entity, budget = _harness()
    error = RuntimeError("parallel executor failed")
    unresolved = MagicMock()

    monkeypatch.setattr(
        tool_exchange, "resolve_parallel_safe_batch", MagicMock(return_value=pairs)
    )
    monkeypatch.setattr(
        tool_exchange,
        "_resolve_current_tool",
        MagicMock(side_effect=lambda _entity, tool_input, *_args: tools[0] if tool_input.id == "a" else tools[1]),
    )
    monkeypatch.setattr(tool_exchange, "append_unresolved_tool_results", unresolved)
    if recovery_enabled:
        monkeypatch.setattr(
            tool_exchange,
            "_async_validate_recoverable_call",
            AsyncMock(side_effect=pending),
        )
    execute_parallel = AsyncMock(side_effect=error)
    monkeypatch.setattr(
        tool_exchange, "async_execute_parallel_safe_batch_outcomes", execute_parallel
    )

    recovery_state = ToolRecoveryState(enabled=True) if recovery_enabled else None
    with pytest.raises(RuntimeError) as exc_info:
        await tool_exchange.async_execute_tool_exchange(
            entity,
            chat_log,
            pending,
            tools,
            budget,
            None,
            [],
            recovery_state=recovery_state,
        )

    assert exc_info.value is error
    unresolved.assert_called_once_with(
        chat_log,
        entity.entity_id,
        pending,
        error=error,
    )
    execute_parallel.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_parallel_mixed_outcomes_preserve_history_then_raise_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed recovery outcomes are all processed before the first failure escapes."""
    pending, tools, pairs, chat_log, entity, budget = _harness(("a", "b", "c", "d"))
    first_error = RuntimeError("first failure")
    later_error = ValueError("later failure")
    unresolved = MagicMock()

    monkeypatch.setattr(
        tool_exchange, "resolve_parallel_safe_batch", MagicMock(return_value=pairs)
    )
    tool_by_name = {tool["spec"]["name"]: tool for tool in tools}
    monkeypatch.setattr(
        tool_exchange,
        "_resolve_current_tool",
        MagicMock(
            side_effect=lambda _entity, tool_input, *_args: tool_by_name[tool_input.tool_name]
        ),
    )
    monkeypatch.setattr(
        tool_exchange,
        "_async_validate_recoverable_call",
        AsyncMock(side_effect=pending),
    )
    monkeypatch.setattr(tool_exchange, "append_unresolved_tool_results", unresolved)
    execute_parallel = AsyncMock(
        return_value=[first_error, None, later_error, "success-d"]
    )
    monkeypatch.setattr(
        tool_exchange, "async_execute_parallel_safe_batch_outcomes", execute_parallel
    )

    with pytest.raises(RuntimeError) as exc_info:
        await tool_exchange.async_execute_tool_exchange(
            entity,
            chat_log,
            pending,
            tools,
            budget,
            None,
            [],
            recovery_state=ToolRecoveryState(enabled=True),
        )

    assert exc_info.value is first_error
    assert unresolved.call_args_list == [
        call(
            chat_log,
            entity.entity_id,
            [pending[0]],
            failed_call_id="a",
            error=first_error,
        ),
        call(
            chat_log,
            entity.entity_id,
            [pending[2]],
            failed_call_id="c",
            error=later_error,
        ),
    ]
    chat_log.async_add_assistant_content_without_tools.assert_called_once_with(
        "success-d"
    )
    execute_parallel.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_parallel_multiple_failures_are_recorded_before_first_is_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled recovery records every parallel failure before propagating the first."""
    pending, tools, pairs, chat_log, entity, budget = _harness(("a", "b", "c"))
    first_error = RuntimeError("first failure")
    later_error = ValueError("later failure")
    unresolved = MagicMock()

    monkeypatch.setattr(
        tool_exchange, "resolve_parallel_safe_batch", MagicMock(return_value=pairs)
    )
    tool_by_name = {tool["spec"]["name"]: tool for tool in tools}
    monkeypatch.setattr(
        tool_exchange,
        "_resolve_current_tool",
        MagicMock(
            side_effect=lambda _entity, tool_input, *_args: tool_by_name[tool_input.tool_name]
        ),
    )
    monkeypatch.setattr(tool_exchange, "append_unresolved_tool_results", unresolved)
    execute_parallel = AsyncMock(return_value=[first_error, later_error, "success-c"])
    monkeypatch.setattr(
        tool_exchange, "async_execute_parallel_safe_batch_outcomes", execute_parallel
    )

    with pytest.raises(RuntimeError) as exc_info:
        await tool_exchange.async_execute_tool_exchange(
            entity,
            chat_log,
            pending,
            tools,
            budget,
            None,
            [],
        )

    assert exc_info.value is first_error
    assert unresolved.call_args_list == [
        call(
            chat_log,
            entity.entity_id,
            [pending[0]],
            failed_call_id="a",
            error=first_error,
        ),
        call(
            chat_log,
            entity.entity_id,
            [pending[1]],
            failed_call_id="b",
            error=later_error,
        ),
    ]
    chat_log.async_add_assistant_content_without_tools.assert_called_once_with(
        "success-c"
    )
    execute_parallel.assert_awaited_once()
