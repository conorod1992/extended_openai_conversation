"""Execution receipts survive result processing and failed provider continuations."""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Literal
from uuid import uuid4

from homeassistant.exceptions import HomeAssistantError

_MAX_EFFECT_RECEIPTS = 4096
_MAX_RETAINED_EFFECT_RECEIPTS = 4096


@dataclass(slots=True)
class EffectReceipt:
    """Compact invocation-owned occurrence; prepared payloads are not retained."""

    invocation_id: str
    ordinal: int
    child_path: tuple[int, ...]
    kind: str
    signature: str
    outcome: Literal["uncertain", "completed", "rejected"] = "uncertain"
    recovery_alias: str = ""


@dataclass
class EffectOperation:
    """Task-local provenance and the precise dispatch handle it owns."""

    kind: str
    prepared: Any
    receipt: EffectReceipt | None = None


@dataclass
class ExecutionAttempt:
    """One invocation lifetime; outcomes come exclusively from its receipts."""

    entity: Any
    call: Any
    tool: dict[str, Any]
    arguments: Mapping[str, Any] | None = None
    ha_parameters: Any = None
    active: bool = True
    invocation_id: str = field(default_factory=lambda: uuid4().hex)
    receipts: list[EffectReceipt] = field(default_factory=list)
    dispatch_recorded: bool = False

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
_OPERATION: ContextVar[EffectOperation | None] = ContextVar(
    "eoai_effect_operation", default=None
)
_DETACHED: ContextVar[bool] = ContextVar("eoai_detached_execution", default=False)


def execution_is_detached() -> bool:
    """Whether this independent job has no provider continuation to recover."""
    return _DETACHED.get()


@contextmanager
def execution_child(index: int) -> Iterator[None]:
    """Retain ordered nesting without changing execution arguments."""
    token = _CHILD_PATH.set((*_CHILD_PATH.get(), index))
    try:
        yield
    finally:
        _CHILD_PATH.reset(token)


@contextmanager
def execution_operation(kind: str, prepared: Any) -> Iterator[EffectOperation]:
    """Supply provenance to a nested transactional boundary."""
    operation = EffectOperation(kind, prepared)
    token = _OPERATION.set(operation)
    try:
        yield operation
    finally:
        _OPERATION.reset(token)


@contextmanager
def execution_attempt(entity: Any, call: Any, tool: dict[str, Any]) -> Iterator[None]:
    current = _ATTEMPT.get()
    if (
        current is not None
        and current.active
        and current.entity is entity
        and current.call is call
    ):
        # Conversation and shared entity seams may wrap the same invocation.
        yield
        return
    attempt = ExecutionAttempt(entity, call, tool)
    token = _ATTEMPT.set(attempt)
    path_token = _CHILD_PATH.set(())
    operation_token = _OPERATION.set(None)
    try:
        yield
    finally:
        attempt.active = False
        _retain_execution_attempt(entity, call, attempt)
        _OPERATION.reset(operation_token)
        _CHILD_PATH.reset(path_token)
        _ATTEMPT.reset(token)


def _retain_execution_attempt(
    entity: Any, call: Any, attempt: ExecutionAttempt
) -> None:
    """Retain provider/unknown ownership only; independent work has no continuation."""
    from .tool_replay_guard import (
        _forget_dispatch,
        dispatch_conversation_id,
        dispatch_inputs,
    )

    # Match dispatch recording: a provider scope overrides inherited detachment.
    owner = dispatch_conversation_id(entity)
    if owner == "":
        return
    outcomes = getattr(entity, "_execution_tool_outcomes", None)
    if not isinstance(outcomes, dict):
        outcomes = entity._execution_tool_outcomes = {}
    receipts = getattr(entity, "_execution_effect_receipts", None)
    if not isinstance(receipts, dict):
        receipts = entity._execution_effect_receipts = {}
    owners = getattr(entity, "_dispatched_tool_conversations", None)
    if not isinstance(owners, dict):
        owners = entity._dispatched_tool_conversations = {}
    retained = tuple(attempt.receipts)
    for item in dispatch_inputs(call):
        owners.setdefault(id(item), owner)
        outcomes[id(item)] = attempt.outcome
        receipts[id(item)] = retained
    while len(outcomes) > 4096:
        _forget_dispatch(entity, next(iter(outcomes)), retire=True)
    # Bound records across invocations too, counting validated-call aliases
    # once. Missing precision must retire its owner, never permit a replay.
    groups: dict[int, tuple[tuple[EffectReceipt, ...], list[int]]] = {}
    for key, group in receipts.items():
        if (entry := groups.get(id(group))) is None:
            groups[id(group)] = (group, [key])
        else:
            entry[1].append(key)
    count = sum(len(group) for group, _keys in groups.values())
    for group, keys in groups.values():
        if count <= _MAX_RETAINED_EFFECT_RECEIPTS:
            break
        for key in keys:
            _forget_dispatch(entity, key, retire=True)
        count -= len(group)


def with_execution_receipt[ResultT](
    function: Callable[..., Coroutine[Any, Any, ResultT]],
) -> Callable[..., Coroutine[Any, Any, ResultT]]:
    """Own receipts at the shared seam, including AI Task and direct callers."""

    @wraps(function)
    async def tracked(
        entity: Any,
        function_tool: dict[str, Any],
        tool_input: Any,
        *args: Any,
        **kwargs: Any,
    ) -> ResultT:
        with execution_attempt(entity, tool_input, function_tool):
            return await function(entity, function_tool, tool_input, *args, **kwargs)

    return tracked


@contextmanager
def detached_execution() -> Iterator[None]:
    """Independent work has no provider continuation to recover."""
    from .tool_replay_guard import bind_effect_replay_scope

    attempt_token = _ATTEMPT.set(None)
    operation_token = _OPERATION.set(None)
    path_token = _CHILD_PATH.set(())
    detached_token = _DETACHED.set(True)
    try:
        with bind_effect_replay_scope(None, None):
            yield
    finally:
        _DETACHED.reset(detached_token)
        _CHILD_PATH.reset(path_token)
        _OPERATION.reset(operation_token)
        _ATTEMPT.reset(attempt_token)


def execution_prepared(
    arguments: Mapping[str, Any], *, ha_parameters: Any = None
) -> None:
    if (attempt := _ATTEMPT.get()) and attempt.active:
        attempt.arguments = arguments
        attempt.ha_parameters = ha_parameters
        attempt.dispatch_recorded = False


def current_execution_receipt() -> EffectReceipt | None:
    if (attempt := _ATTEMPT.get()) and attempt.active and attempt.receipts:
        return attempt.receipts[-1]
    return None


def execution_started(
    *,
    kind: str | None = None,
    prepared: Any = None,
    recovery_alias: str = "",
    check_alias: str = "",
) -> EffectReceipt | None:
    if (attempt := _ATTEMPT.get()) and attempt.active:
        from .tool_replay_guard import (
            UnidentifiableEffect,
            check_effect_replay,
            effect_signature,
            record_dispatch,
        )

        if len(attempt.receipts) >= _MAX_EFFECT_RECEIPTS:
            raise HomeAssistantError(
                "Tool execution exceeds the effect receipt safety limit"
            )
        operation = _OPERATION.get()
        kind = kind or (operation.kind if operation else "tool")
        if prepared is None:
            prepared = (
                operation.prepared
                if operation
                else {
                    "function": attempt.tool.get("function", {}),
                    "arguments": attempt.arguments
                    if attempt.arguments is not None
                    else attempt.call.tool_args,
                }
            )
        # Opaque dispatches may depend on unobservable API/script context. Their
        # receipts stay owned by the selected tool; only precise effect contracts
        # can match across wrappers. Generic persistence lacks a prepared payload.
        opaque = kind in {"backend", "ha_tool"} or (
            kind == "persistence"
            and (operation is None or operation.kind != "delayed_job")
        )
        try:
            signature = effect_signature(
                kind,
                {"owner": attempt.call.tool_name, "operation": prepared}
                if opaque
                else prepared,
            )
        except UnidentifiableEffect:
            # An opaque value has no reliable textual identity. After a failed
            # continuation, require acknowledgment rather than guessing equality.
            signature = "*"
        # Only previous failed continuations participate. Live repeated actions
        # are distinct occurrences and never enter the recovery ledger here.
        check_effect_replay(attempt.entity, signature)
        if check_alias:
            check_effect_replay(attempt.entity, check_alias)
        receipt = EffectReceipt(
            attempt.invocation_id,
            len(attempt.receipts),
            _CHILD_PATH.get(),
            kind,
            signature,
            recovery_alias=recovery_alias,
        )
        attempt.receipts.append(receipt)
        if operation is not None:
            operation.receipt = receipt
        if not attempt.dispatch_recorded:
            record_dispatch(
                attempt.entity,
                attempt.call,
                attempt.tool,
                prepared_arguments=attempt.arguments,
                ha_parameters=attempt.ha_parameters,
            )
            attempt.dispatch_recorded = True
        return receipt
    return None


def execution_completed(receipt: EffectReceipt | None = None) -> None:
    receipt = receipt or current_execution_receipt()
    if (
        (attempt := _ATTEMPT.get())
        and attempt.active
        and receipt is not None
        and receipt.invocation_id == attempt.invocation_id
        and receipt.outcome == "uncertain"
    ):
        receipt.outcome = "completed"


def execution_rejected(
    receipt: EffectReceipt | None = None, *, before_dispatch: bool = False
) -> None:
    """Authoritative pre-execution rejection does not imply an effect."""
    if (attempt := _ATTEMPT.get()) and attempt.active:
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
        # Reserve one terminal rejection beyond the dispatch budget so reaching
        # the ceiling remains partial, without growing on every later refusal.
        if len(attempt.receipts) >= _MAX_EFFECT_RECEIPTS + 1:
            return
        attempt.receipts.append(
            EffectReceipt(
                attempt.invocation_id,
                len(attempt.receipts),
                _CHILD_PATH.get(),
                operation.kind if operation else "validation",
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
        owns_receipts = config.get("type") in {"native", "composite", "bash"}
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
