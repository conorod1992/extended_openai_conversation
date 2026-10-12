"""Execution receipts survive result processing and failed provider continuations."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class ExecutionAttempt:
    entity: Any
    call: Any
    tool: dict[str, Any]
    arguments: Mapping[str, Any] | None = None
    ha_parameters: Any = None
    completed: int = 0
    inflight: bool = False
    failed: bool = False


_ATTEMPT: ContextVar[ExecutionAttempt | None] = ContextVar(
    "eoai_execution_attempt", default=None
)


@contextmanager
def execution_attempt(entity: Any, call: Any, tool: dict[str, Any]) -> Iterator[None]:
    attempt = ExecutionAttempt(entity, call, tool)
    token = _ATTEMPT.set(attempt)
    try:
        yield
    finally:
        from .tool_replay_guard import dispatch_inputs

        outcomes = getattr(entity, "_execution_tool_outcomes", None)
        if not isinstance(outcomes, dict):
            outcomes = entity._execution_tool_outcomes = {}
        outcome = (
            "uncertain"
            if attempt.inflight or attempt.failed
            else "completed"
            if attempt.completed
            else "rejected"
        )
        for item in dispatch_inputs(call):
            outcomes[id(item)] = outcome
        while len(outcomes) > 4096:
            outcomes.pop(next(iter(outcomes)))
        _ATTEMPT.reset(token)


def execution_prepared(
    arguments: Mapping[str, Any], *, ha_parameters: Any = None
) -> None:
    if attempt := _ATTEMPT.get():
        attempt.arguments = arguments
        attempt.ha_parameters = ha_parameters


def execution_started() -> None:
    if attempt := _ATTEMPT.get():
        from .tool_replay_guard import record_dispatch

        attempt.inflight = True
        record_dispatch(
            attempt.entity,
            attempt.call,
            attempt.tool,
            prepared_arguments=attempt.arguments,
            ha_parameters=attempt.ha_parameters,
        )


def execution_completed() -> None:
    if attempt := _ATTEMPT.get():
        attempt.completed += 1
        attempt.inflight = False


def execution_rejected() -> None:
    """Authoritative pre-execution rejection does not imply an effect."""
    if attempt := _ATTEMPT.get():
        attempt.inflight = False
        # A preceding successful child still makes the whole call partial.
        attempt.failed = bool(attempt.completed)


async def execute_backend(
    function: Any,
    hass: Any,
    config: dict[str, Any],
    arguments: dict[str, Any],
    context: Any,
    exposed: list[Any],
) -> Any:
    """Native effects/composite children own precise boundaries; opaque backends are conservative."""
    owns_receipts = config.get("type") in {"native", "composite"}
    if not owns_receipts:
        execution_started()
    try:
        result = await function.execute(hass, config, arguments, context, exposed)
    except BaseException:
        if attempt := _ATTEMPT.get():
            attempt.failed = bool(attempt.completed)
        raise
    if not owns_receipts:
        execution_completed()
    return result
