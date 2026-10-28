"""Deliver metadata-only completion notifications without retaining transcripts."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import logging
from typing import Any, Literal, NotRequired, TypedDict

from homeassistant.core import HomeAssistant

from .const import EVENT_CONVERSATION_FINISHED
from .operation_settlement import async_settle_owned_operation
from .operational_errors import log_handled_failure

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CompletionMetadata:
    """Completion facts; the sender owns the public event representation."""

    agent_id: str
    status: Literal["success", "local", "error"]
    conversation_id: str | None = None
    handled_locally: bool = False
    error_type: str | None = None


class _CompletionPayload(TypedDict):
    schema_version: Literal[2]
    conversation_id: str | None
    agent_id: str
    status: Literal["success", "local", "error"]
    handled_locally: NotRequired[Literal[True]]
    error_type: NotRequired[str]


def _prepare(metadata: CompletionMetadata) -> _CompletionPayload:
    """Validate scalars and detach the fixed schema before queue admission."""
    if type(metadata) is not CompletionMetadata:
        raise TypeError("Expected completion metadata")
    if (
        type(metadata.agent_id) is not str
        or not metadata.agent_id
        or type(metadata.status) is not str
        or metadata.status not in {"success", "local", "error"}
        or (
            metadata.conversation_id is not None
            and type(metadata.conversation_id) is not str
        )
        or type(metadata.handled_locally) is not bool
        or (
            metadata.error_type is not None
            and (
                type(metadata.error_type) is not str
                or not metadata.error_type.isidentifier()
            )
        )
    ):
        raise ValueError("Invalid completion metadata")
    payload: _CompletionPayload = {
        "schema_version": 2,
        "conversation_id": metadata.conversation_id,
        "agent_id": metadata.agent_id,
        "status": metadata.status,
    }
    if metadata.handled_locally:
        payload["handled_locally"] = True
    if metadata.error_type is not None:
        payload["error_type"] = metadata.error_type
    return payload


# Each invocation owns its queue, including same-agent and cross-agent nesting.
_PENDING: ContextVar[list[tuple[HomeAssistant, _CompletionPayload]] | None] = (
    ContextVar("eoai_completion_events", default=None)
)


def _emit(hass: HomeAssistant, payload: _CompletionPayload) -> None:
    """Keep bus failures best-effort without logging private exception prose."""
    try:
        hass.bus.async_fire(EVENT_CONVERSATION_FINISHED, payload)
    except Exception as err:
        log_handled_failure(_LOGGER, "Completion notification failed", err)


async def _async_emit(hass: HomeAssistant, payload: _CompletionPayload) -> None:
    """Keep queued delivery on HA's loop, in request order."""
    _emit(hass, payload)


@asynccontextmanager
async def completion_event_scope() -> AsyncIterator[None]:
    """Attempt every notification in order before return or cancellation exits."""
    pending: list[tuple[HomeAssistant, _CompletionPayload]] = []
    token = _PENDING.set(pending)
    try:
        yield
    finally:
        _PENDING.reset(token)
        if pending:
            # Own one task for the whole queue. Shielding alone would let this
            # context exit on cancellation while later events remain unscheduled.
            await async_settle_owned_operation(_async_flush(pending))


async def _async_flush(
    pending: list[tuple[HomeAssistant, _CompletionPayload]],
) -> None:
    for hass, payload in pending:
        await _async_emit(hass, payload)


def queue_completion_event(
    hass: HomeAssistant, metadata: CompletionMetadata, contents: Iterable[Any] = ()
) -> None:
    """Queue metadata without inspecting or retaining request/history content.

    ``contents`` is accepted for older internal callers, but deliberately ignored.
    Full transcripts belong in the explicitly controlled conversation archive,
    never on the Recorder-visible HA event bus.
    """
    try:
        payload = _prepare(metadata)
    except Exception as err:
        log_handled_failure(_LOGGER, "Completion notification failed", err)
        return
    if (pending := _PENDING.get()) is not None:
        pending.append((hass, payload))
    else:
        _emit(hass, payload)
