"""Prepare completion event messages outside HA's event loop."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from contextvars import ContextVar
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import EVENT_CONVERSATION_FINISHED
from .ha_tool_result_compat import is_tool_result_content
from .operational_errors import log_handled_failure

_LOGGER = logging.getLogger(__name__)

# Each invocation owns its queue, including same-agent and cross-agent nesting.
_PENDING: ContextVar[
    list[tuple[HomeAssistant, dict[str, Any], tuple[Any, ...]]] | None
] = ContextVar("eoai_completion_events", default=None)


async def _async_emit(
    hass: HomeAssistant, payload: dict[str, Any], contents: tuple[Any, ...]
) -> None:
    """Only message conversion leaves the loop; the HA bus call stays on-loop."""
    try:
        payload["messages"] = await asyncio.to_thread(
            lambda: [item.as_dict() for item in contents]
        )
        hass.bus.async_fire(EVENT_CONVERSATION_FINISHED, payload)
    except Exception as err:
        # Notifications describe completed work; one failure must not replace the
        # request outcome or prevent later notifications. Cancellation still exits.
        log_handled_failure(_LOGGER, "Completion notification failed", err)


@asynccontextmanager
async def completion_event_scope() -> AsyncIterator[None]:
    """Attempt every notification in order before return or cancellation exits."""
    pending: list[tuple[HomeAssistant, dict[str, Any], tuple[Any, ...]]] = []
    token = _PENDING.set(pending)
    try:
        yield
    finally:
        _PENDING.reset(token)
        if pending:
            # Own one task for the whole queue. Shielding alone would let this
            # context exit on cancellation while later events remain unscheduled.
            task = asyncio.create_task(_async_flush(pending))
            cancellation: asyncio.CancelledError | None = None
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError as err:
                    if task.cancelled():
                        raise
                    if cancellation is None:
                        cancellation = err
                except Exception:
                    break
            task.result()
            if cancellation is not None:
                raise cancellation


async def _async_flush(
    pending: list[tuple[HomeAssistant, dict[str, Any], tuple[Any, ...]]],
) -> None:
    for hass, payload, contents in pending:
        await _async_emit(hass, payload, contents)


def queue_completion_event(
    hass: HomeAssistant, payload: dict[str, Any], contents: Iterable[Any]
) -> None:
    """Capture message membership on-loop without traversing retained results."""
    snapshot = tuple(contents)
    if (pending := _PENDING.get()) is not None:
        pending.append((hass, payload, snapshot))
    elif any(is_tool_result_content(item) for item in snapshot):
        # Standalone helper callers have no response boundary to await the event.
        hass.async_create_task(
            _async_emit(hass, payload, snapshot), "EOAI conversation completion"
        )
    else:
        # Plain text conversion does not traverse payloads; preserve synchronous
        # notification for standalone local/error helpers without Tool history.
        try:
            payload["messages"] = [item.as_dict() for item in snapshot]
            hass.bus.async_fire(EVENT_CONVERSATION_FINISHED, payload)
        except Exception as err:
            log_handled_failure(_LOGGER, "Completion notification failed", err)
