"""Execution receipts survive result processing and failed provider continuations."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from homeassistant.exceptions import HomeAssistantError


@dataclass
class EffectReceipt:
    """One invocation-owned dispatch occurrence, independently settled."""

    invocation_id: str
    ordinal: int
    child_path: tuple[int, ...]
    kind: str
    prepared: Any
    signature: str
    outcome: Literal["uncertain", "completed", "rejected"] = "uncertain"


@dataclass
class ExecutionAttempt:
    entity: Any
    call: Any
    tool: dict[str, Any]
    arguments: Mapping[str, Any] | None = None
    ha_parameters: Any = None
    invocation_id: str = field(default_factory=lambda: uuid4().hex)
    receipts: list[EffectReceipt] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        outcomes = {receipt.outcome for receipt in self.receipts}
        if "uncertain" in outcomes:
            return "uncertain"
        if "completed" in outcomes:
            return "partial" if "rejected" in outcomes else "completed"
        return "rejected"


_ATTEMPT: ContextVar[ExecutionAttempt | None] = ContextVar(
    "eoai_execution_attempt", default=None
)
_CHILD_PATH: ContextVar[tuple[int, ...]] = ContextVar("eoai_effect_path", default=())
_OPERATION: ContextVar[tuple[str, Any] | None] = ContextVar(
    "eoai_effect_operation", default=None
)


@contextmanager
def execution_child(index: int) -> Iterator[None]:
    """Retain ordered nesting without changing execution arguments."""
    token = _CHILD_PATH.set((*_CHILD_PATH.get(), index))
    try:
        yield
    finally:
        _CHILD_PATH.reset(token)


@contextmanager
def execution_operation(kind: str, prepared: Any) -> Iterator[None]:
    """Supply provenance to a nested transactional boundary."""
    token = _OPERATION.set((kind, prepared))
    try:
        yield
    finally:
        _OPERATION.reset(token)


@contextmanager
def execution_attempt(entity: Any, call: Any, tool: dict[str, Any]) -> Iterator[None]:
    attempt = ExecutionAttempt(entity, call, tool)
    token = _ATTEMPT.set(attempt)
    path_token = _CHILD_PATH.set(())
    operation_token = _OPERATION.set(None)
    try:
        yield
    finally:
        from .tool_replay_guard import dispatch_inputs

        outcomes = getattr(entity, "_execution_tool_outcomes", None)
        if not isinstance(outcomes, dict):
            outcomes = entity._execution_tool_outcomes = {}
        receipts = getattr(entity, "_execution_effect_receipts", None)
        if not isinstance(receipts, dict):
            receipts = entity._execution_effect_receipts = {}
        for item in dispatch_inputs(call):
            outcomes[id(item)] = attempt.outcome
            receipts[id(item)] = tuple(attempt.receipts)
        while len(outcomes) > 4096:
            receipts.pop(next(iter(outcomes)), None)
            outcomes.pop(next(iter(outcomes)))
        _OPERATION.reset(operation_token)
        _CHILD_PATH.reset(path_token)
        _ATTEMPT.reset(token)


def execution_prepared(
    arguments: Mapping[str, Any], *, ha_parameters: Any = None
) -> None:
    if attempt := _ATTEMPT.get():
        attempt.arguments = arguments
        attempt.ha_parameters = ha_parameters


def current_execution_receipt() -> EffectReceipt | None:
    if (attempt := _ATTEMPT.get()) and attempt.receipts:
        return attempt.receipts[-1]
    return None


def execution_started(
    *, kind: str | None = None, prepared: Any = None
) -> EffectReceipt | None:
    if attempt := _ATTEMPT.get():
        from .tool_replay_guard import (
            check_effect_replay,
            effect_signature,
            record_dispatch,
        )

        if len(attempt.receipts) >= 4096:
            raise HomeAssistantError(
                "Tool execution exceeds the effect receipt safety limit"
            )
        operation = _OPERATION.get()
        kind = kind or (operation[0] if operation else "tool")
        if prepared is None:
            prepared = (
                operation[1]
                if operation
                else {
                    "function": attempt.tool.get("function", {}),
                    "arguments": attempt.arguments
                    if attempt.arguments is not None
                    else attempt.call.tool_args,
                }
            )
        prepared = deepcopy(prepared)
        signature = effect_signature(kind, prepared)
        # Only previous failed continuations participate. Live repeated actions
        # are distinct occurrences and never enter the recovery ledger here.
        check_effect_replay(attempt.entity, signature)
        receipt = EffectReceipt(
            attempt.invocation_id,
            len(attempt.receipts),
            _CHILD_PATH.get(),
            kind,
            prepared,
            signature,
        )
        attempt.receipts.append(receipt)
        record_dispatch(
            attempt.entity,
            attempt.call,
            attempt.tool,
            prepared_arguments=attempt.arguments,
            ha_parameters=attempt.ha_parameters,
        )
        return receipt
    return None


def execution_completed(receipt: EffectReceipt | None = None) -> None:
    receipt = receipt or current_execution_receipt()
    if (
        (attempt := _ATTEMPT.get())
        and receipt is not None
        and receipt.invocation_id == attempt.invocation_id
        and receipt.outcome == "uncertain"
    ):
        receipt.outcome = "completed"


def execution_rejected(
    receipt: EffectReceipt | None = None, *, before_dispatch: bool = False
) -> None:
    """Authoritative pre-execution rejection does not imply an effect."""
    if attempt := _ATTEMPT.get():
        explicit = receipt is not None
        receipt = None if before_dispatch else receipt or current_execution_receipt()
        if explicit and (
            receipt is None
            or receipt.invocation_id != attempt.invocation_id
            or receipt.outcome != "uncertain"
        ):
            return
        if receipt is not None and receipt.invocation_id == attempt.invocation_id:
            if receipt.outcome == "uncertain":
                receipt.outcome = "rejected"
                return
            if receipt.outcome == "rejected":
                return
        operation = _OPERATION.get()
        attempt.receipts.append(
            EffectReceipt(
                attempt.invocation_id,
                len(attempt.receipts),
                _CHILD_PATH.get(),
                operation[0] if operation else "validation",
                None,
                "",
                "rejected",
            )
        )


async def execute_backend(
    function: Any,
    hass: Any,
    config: dict[str, Any],
    arguments: dict[str, Any],
    context: Any,
    exposed: list[Any],
) -> Any:
    """Native effects/composite children own precise boundaries; opaque backends are conservative."""
    with execution_operation("backend", {"function": config, "arguments": arguments}):
        owns_receipts = config.get("type") in {"native", "composite"}
        before = len(attempt.receipts) if (attempt := _ATTEMPT.get()) else 0
        receipt = None if owns_receipts else execution_started()
        try:
            result = await function.execute(hass, config, arguments, context, exposed)
        except BaseException:
            if attempt is not None and (
                len(attempt.receipts) == before
                or all(
                    item.outcome == "completed" for item in attempt.receipts[before:]
                )
            ):
                execution_rejected(before_dispatch=True)
            raise
        if not owns_receipts:
            execution_completed(receipt)
        return result
