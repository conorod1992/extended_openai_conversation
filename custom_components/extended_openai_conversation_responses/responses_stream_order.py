"""Adapt concurrent Responses output lifetimes to HA's sequential accumulator."""

from collections.abc import AsyncIterator
from typing import Any

from .provider_errors import ProviderStreamError


async def ordered_output_events(events: AsyncIterator[Any]) -> AsyncIterator[Any]:
    """Stream the leading item live and buffer later items until their turn.

    Responses identifies output by index; HA has only one current assistant
    content entry. Keep all indexed events together, including text, refusals,
    arguments and native completion. No payload is copied or reinterpreted.
    """
    next_index = 0
    pending: dict[int, list[Any]] = {}
    async for event in events:
        index = getattr(event, "output_index", None)
        if index is None:
            if event.type == "response.completed" and pending:
                raise ProviderStreamError(
                    "Provider returned malformed data: unfinished output item",
                    error_type="invalid_event_sequence",
                )
            yield event
            continue
        if index != next_index:
            if index < next_index:
                # Let the transformer reject duplicate completions/identities.
                yield event
            else:
                pending.setdefault(index, []).append(event)
            continue
        yield event
        if event.type != "response.output_item.done":
            continue
        next_index += 1
        while next_index in pending:
            buffered = pending.pop(next_index)
            for buffered_event in buffered:
                yield buffered_event
            if buffered[-1].type != "response.output_item.done":
                break
            next_index += 1
