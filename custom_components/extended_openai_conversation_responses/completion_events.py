"""Deliver metadata-only completion notifications without retaining transcripts."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from contextvars import ContextVar
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import EVENT_CONVERSATION_FINISHED
from .operation_settlement import async_settle_owned_operation
from .operational_errors import log_handled_failure

_LOGGER = logging.getLogger(__name__)

# Each invocation owns its queue, including same-agent and cross-agent nesting.
_PENDING: ContextVar[list[tuple[HomeAssistant, dict[str, Any]]] | None] = ContextVar(
    "eoai_completion_events", default=None
)


def _emit(hass: HomeAssistant, payload: dict[str, Any]) -> None:
    """Keep bus failures best-effort without logging private exception prose."""
    try:
        hass.bus.async_fire(EVENT_CONVERSATION_FINISHED, payload)
    except Exception as err:
        log_handled_failure(_LOGGER, "Completion notification failed", err)


async def _async_emit(hass: HomeAssistant, payload: dict[str, Any]) -> None:
    """Keep queued delivery on HA's loop, in request order."""
    _emit(hass, payload)


@asynccontextmanager
async def completion_event_scope() -> AsyncIterator[None]:
    """Attempt every notification in order before return or cancellation exits."""
    pending: list[tuple[HomeAssistant, dict[str, Any]]] = []
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
    pending: list[tuple[HomeAssistant, dict[str, Any]]],
) -> None:
    for hass, payload in pending:
        await _async_emit(hass, payload)


def queue_completion_event(
    hass: HomeAssistant, payload: dict[str, Any], contents: Iterable[Any] = ()
) -> None:
    """Queue metadata without inspecting or retaining request/history content.

    ``contents`` is accepted for older internal callers, but deliberately ignored.
    Full transcripts belong in the explicitly controlled conversation archive,
    never on the Recorder-visible HA event bus.
    """
    if (pending := _PENDING.get()) is not None:
        pending.append((hass, payload))
    else:
        _emit(hass, payload)
