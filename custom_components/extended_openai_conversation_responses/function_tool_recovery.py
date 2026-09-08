"""Bounded recovery for Function Tool failures proven safe before dispatch."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components import conversation
from homeassistant.helpers import llm

from .exceptions import FunctionValidationInfrastructureError, ParseArgumentsFailed

_MAX_RECOVERIES_PER_CONVERSATION = 2
_MAX_MODEL_ERROR_TEXT = 320


class MalformedToolArguments(dict[str, Any]):
    """Empty mapping that retains malformed provider JSON for protocol replay."""

    def __init__(self, raw: str) -> None:
        super().__init__()
        self.raw = raw


@dataclass(frozen=True, slots=True)
class CorrectableToolFailure:
    """A failure whose explicit stage proves dispatch has not started."""

    code: str
    stage: str
    message: str
    original: BaseException


@dataclass(slots=True)
class ToolRecoveryState:
    """Request-local recovery state with an independent loop bound."""

    enabled: bool
    limit: int = _MAX_RECOVERIES_PER_CONVERSATION
    used: int = 0
    malformed_calls: dict[str, CorrectableToolFailure] = field(default_factory=dict)

    def consume(self) -> bool:
        """Reserve one recovery slot, returning False when the cap is exhausted."""
        if not self.enabled or self.used >= self.limit:
            return False
        self.used += 1
        return True

    def remember_malformed(self, call_id: str, raw: str) -> MalformedToolArguments:
        """Retain malformed provider arguments without exposing their raw contents."""
        error = ParseArgumentsFailed(raw)
        self.malformed_calls[call_id] = CorrectableToolFailure(
            code="invalid_json",
            stage="provider_argument_decoding",
            message="Tool arguments were not valid JSON. Send corrected JSON arguments.",
            original=error,
        )
        return MalformedToolArguments(raw)

    def pop_malformed(self, call_id: str) -> CorrectableToolFailure | None:
        """Consume a malformed-argument marker for one retained tool call."""
        return self.malformed_calls.pop(call_id, None)


# ContextVar keeps recovery state isolated when multiple conversations stream or
# execute tools concurrently on the same entity instance.
_ACTIVE_RECOVERY_STATE: ContextVar[ToolRecoveryState | None] = ContextVar(
    "extended_openai_function_tool_recovery", default=None
)


@contextmanager
def bind_tool_recovery_state(state: ToolRecoveryState | None) -> Iterator[None]:
    """Bind recovery policy to one request-local async execution context."""
    token = _ACTIVE_RECOVERY_STATE.set(state)
    try:
        yield
    finally:
        _ACTIVE_RECOVERY_STATE.reset(token)


def current_tool_recovery_state() -> ToolRecoveryState | None:
    """Return the recovery state bound to the current async request context."""
    return _ACTIVE_RECOVERY_STATE.get()


def strict_execution_failures_enabled() -> bool:
    """Return whether arbitrary runtime failures must remain fail-fast."""
    state = current_tool_recovery_state()
    return state is not None and state.enabled


def provider_argument_text(arguments: Any) -> str:
    """Return raw malformed JSON when retained, otherwise signal normal encoding."""
    if isinstance(arguments, MalformedToolArguments):
        return arguments.raw
    raise TypeError("arguments are not malformed provider input")


def correctable_validation_failure(error: BaseException) -> CorrectableToolFailure:
    """Build bounded model feedback from the dedicated pre-dispatch validator."""
    if isinstance(error, FunctionValidationInfrastructureError):
        raise error
    detail = " ".join(str(error).split())
    if len(detail) > _MAX_MODEL_ERROR_TEXT:
        detail = f"{detail[: _MAX_MODEL_ERROR_TEXT - 1]}…"
    if not detail:
        detail = "Tool arguments did not match the required schema"
    return CorrectableToolFailure(
        code="invalid_arguments",
        stage="pre_dispatch_validation",
        message=detail,
        original=error,
    )


def recovery_tool_result(
    agent_id: str,
    tool_input: llm.ToolInput,
    failure: CorrectableToolFailure,
) -> conversation.ToolResultContent:
    """Return one bounded, structured, model-visible pre-dispatch error."""
    return conversation.ToolResultContent(
        agent_id=agent_id,
        tool_call_id=tool_input.id,
        tool_name=tool_input.tool_name,
        tool_result={
            "result": {
                "status": "error",
                "reason": "correctable_tool_error",
                "code": failure.code,
                "stage": failure.stage,
                "error": failure.message,
            }
        },
    )