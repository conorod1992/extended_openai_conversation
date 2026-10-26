"""Adapt concurrent Responses output lifetimes to HA's sequential accumulator."""

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from typing import Any

from .provider_errors import ProviderStreamError


async def _validated_output_events(
    events: AsyncIterator[Any],
    recover_usage: Callable[[], Awaitable[None]] | None = None,
) -> AsyncGenerator[Any]:
    """Validate wire identities and arguments before presentation can buffer them."""
    terminal_seen = False
    completed_items: set[tuple[Any, Any]] = set()
    output_items: dict[Any, tuple[Any, Any, Any]] = {}
    argument_fragments: dict[Any, str] = {}
    try:
        async for event in events:
            event_type = getattr(event, "type", "")
            if (
                terminal_seen
                and event_type.startswith("response.")
                and event_type not in {"response.usage", "response.usage.updated"}
            ):
                raise ProviderStreamError(
                    "Provider returned malformed data: event after terminal response",
                    error_type="invalid_event_sequence",
                )

            if event_type in {
                "response.output_item.added",
                "response.output_item.done",
            }:
                index = getattr(event, "output_index", None)
                item = event.item
                identity = (
                    getattr(item, "id", None),
                    getattr(item, "call_id", None),
                    getattr(item, "name", None),
                )
                if (
                    index is not None
                    and index in output_items
                    and output_items[index] != identity
                ):
                    raise ProviderStreamError(
                        "Provider returned malformed data: conflicting output item",
                        error_type="invalid_event_sequence",
                    )
                if index is not None:
                    if identity[0] and any(
                        other_index != index and other_identity[0] == identity[0]
                        for other_index, other_identity in output_items.items()
                    ):
                        raise ProviderStreamError(
                            "Provider returned malformed data: duplicate output item id",
                            error_type="invalid_event_sequence",
                        )
                    output_items[index] = identity
                    if (
                        event_type == "response.output_item.done"
                        and index in argument_fragments
                        and getattr(item, "arguments", None)
                        != argument_fragments[index]
                    ):
                        raise ProviderStreamError(
                            "Provider returned malformed data: conflicting tool arguments",
                            error_type="invalid_event_sequence",
                        )
            elif event_type in {
                "response.function_call_arguments.delta",
                "response.function_call_arguments.done",
            }:
                index = getattr(event, "output_index", None)
                if (
                    index in output_items
                    and getattr(event, "item_id", None) != output_items[index][0]
                ):
                    raise ProviderStreamError(
                        "Provider returned malformed data: conflicting argument item",
                        error_type="invalid_event_sequence",
                    )

                if event_type == "response.function_call_arguments.delta":
                    argument_fragments[index] = (
                        argument_fragments.get(index, "") + event.delta
                    )
                elif (
                    index in argument_fragments
                    and event.arguments != argument_fragments[index]
                ):
                    raise ProviderStreamError(
                        "Provider returned malformed data: conflicting tool arguments",
                        error_type="invalid_event_sequence",
                    )

            if event_type == "response.output_item.done":
                item = event.item
                key = (getattr(event, "output_index", None), getattr(item, "id", None))
                if key != (None, None):
                    if key in completed_items:
                        raise ProviderStreamError(
                            "Provider returned malformed data: repeated completed output item",
                            error_type="invalid_event_sequence",
                        )
                    completed_items.add(key)
            if event_type == "response.completed":
                terminal_seen = True
            yield event

    except ProviderStreamError:
        # Recover from the raw owned stream before closing normalized iteration.
        # This never interprets buffered output or executes any Function calls.
        if recover_usage is not None:
            await recover_usage()
        raise
    finally:
        if (close := getattr(events, "aclose", None)) is not None:
            await close()


async def ordered_output_events(
    events: AsyncIterator[Any],
    *,
    recover_usage: Callable[[], Awaitable[None]] | None = None,
) -> AsyncGenerator[Any]:
    """Stream the leading item live and buffer later items until their turn.

    Responses identifies output by index; HA has only one current assistant
    content entry. Keep all indexed events together, including text, refusals,
    arguments and native completion. No payload is copied or reinterpreted.
    """
    next_index = 0
    pending: dict[int, list[Any]] = {}
    known_indices: set[int] = set()
    async with aclosing(_validated_output_events(events, recover_usage)) as validated:
        async for event in validated:
            index = getattr(event, "output_index", None)
            if index is not None and event.type in {
                "response.output_item.added",
                "response.output_item.done",
            }:
                known_indices.add(index)
            if index is None or index not in known_indices:
                if event.type == "response.completed" and pending:
                    # This rejection belongs to ordering, outside wire validation.
                    # Drain once while the normalized/raw stream is still owned.
                    if recover_usage is not None:
                        await recover_usage()
                    raise ProviderStreamError(
                        "Provider returned malformed data: unfinished output item",
                        error_type="invalid_event_sequence",
                    )
                yield event
                continue
            if index != next_index:
                if index < next_index:
                    # Identities/completions were checked in original wire order.
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
