"""Serialize indexed Responses items for HA's single active assistant entry."""

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from .non_streaming import completed_responses_output_events
from .provider_errors import ProviderStreamError
from .provider_output_budget import MAX_OUTPUT_ITEMS, ProviderOutputBudget

_ADDED = "response.output_item.added"
_DONE = "response.output_item.done"
_ARGUMENT_DELTA = "response.function_call_arguments.delta"
_ARGUMENT_DONE = "response.function_call_arguments.done"
_USAGE = {"response.usage", "response.usage.updated"}
_RESPONSE_EVENTS = {
    "response.created",
    "response.in_progress",
    "response.queued",
    "response.completed",
    "response.incomplete",
    "response.failed",
    "response.error",
    *_USAGE,
}


def _malformed(reason: str) -> ProviderStreamError:
    return ProviderStreamError(
        f"Provider returned malformed data: {reason}",
        error_type="invalid_event_sequence",
    )


def _item_identity(item: Any) -> tuple[Any, ...]:
    return tuple(
        getattr(item, field, None)
        for field in ("type", "id", "call_id", "name", "arguments")
    )


@dataclass(slots=True)
class _OutputItem:
    """Own one index's wire identity, completion and deferred presentation."""

    item_id: str | None = None
    signature: tuple[Any, ...] | None = None
    completion: tuple[Any, ...] | None = None
    arguments: str | None = None
    opening: Any = None
    presented: bool = False
    done: bool = False
    pending: list[Any] = field(default_factory=list)
    pending_weight: int = 0

    @property
    def ready(self) -> bool:
        return self.presented or self.opening is not None

    def observe(self, event: Any, kind: str, item_id: str | None) -> None:
        if item_id:
            if self.item_id and self.item_id != item_id:
                reason = (
                    "conflicting argument item"
                    if kind in {_ARGUMENT_DELTA, _ARGUMENT_DONE}
                    else "conflicting output item id"
                )
                raise _malformed(reason)
            self.item_id = item_id
        if self.done:
            if kind == _DONE:
                raise _malformed("repeated completed output item")
            if kind == _ADDED or kind.endswith(".delta"):
                raise _malformed("content after completed output item")

        if kind in {_ADDED, _DONE}:
            item = event.item
            signature = tuple(
                getattr(item, field, None) for field in ("type", "call_id", "name")
            )
            if self.signature is not None and self.signature != signature:
                raise _malformed("conflicting output item")
            self.signature = signature
            if kind == _ADDED:
                if self.ready:
                    raise _malformed("repeated output item start")
                self.opening = event
            else:
                self.check_arguments(getattr(item, "arguments", None))
                self.done = True
                self.completion = _item_identity(item)
                # A done-only provider still needs an opening to select the
                # correct role and phase before presenting deferred content.
                if not self.ready:
                    self.opening = SimpleNamespace(
                        type=_ADDED, item=item, output_index=event.output_index
                    )
        else:
            self.observe_arguments(event, kind)

    def observe_arguments(self, event: Any, kind: str) -> None:
        if kind == _ARGUMENT_DELTA:
            self.arguments = (self.arguments or "") + event.delta
        elif kind == _ARGUMENT_DONE:
            self.check_arguments(event.arguments)

    def check_arguments(self, arguments: str | None) -> None:
        if self.arguments is not None and self.arguments != arguments:
            raise _malformed("conflicting tool arguments")

    def buffer(self, event: Any, budget: ProviderOutputBudget) -> None:
        self.pending_weight += budget.buffer(event)
        self.pending.append(event)

    def release(self, budget: ProviderOutputBudget) -> Iterator[Any]:
        opening, self.opening = self.opening, None
        events, self.pending = self.pending, []
        budget.release_buffer(self.pending_weight, len(events))
        self.pending_weight = 0
        self.presented = True
        if opening is not None:
            yield opening
        for event in events:
            if event is not opening:
                yield event


async def ordered_output_events(
    events: AsyncIterator[Any],
    *,
    recover_usage: Callable[[], Awaitable[None]] | None = None,
) -> AsyncGenerator[Any]:
    """Validate in wire order and present each indexed item as one HA entry."""
    budget = ProviderOutputBudget()
    items: dict[int, _OutputItem] = {}
    owners: dict[str, int] = {}
    next_index = 0
    terminal_seen = False
    legacy = _OutputItem()
    legacy_start = False
    legacy_completions: set[str] = set()
    legacy_items: list[tuple[Any, ...]] = []
    legacy_output_seen = False
    try:
        async for event in events:
            index = getattr(event, "output_index", None)
            if index is not None and (type(index) is not int or index < 0):
                raise _malformed("invalid output index")
            budget.responses_event(event)
            kind = getattr(event, "type", "")
            if terminal_seen and kind.startswith("response.") and kind not in _USAGE:
                raise _malformed("event after terminal response")

            if kind == "response.completed":
                if any(index >= next_index for index in items):
                    raise _malformed("unfinished output item")
                response = getattr(event, "response", None)
                output = getattr(response, "output", ())
                if isinstance(output, (list, tuple)) and output:
                    if len(output) > MAX_OUTPUT_ITEMS:
                        raise ProviderStreamError(
                            "Provider output exceeded local safety limits",
                            error_type="output_limit_exceeded",
                        )
                    if items or legacy_output_seen:
                        completed = [
                            items[index].completion for index in range(next_index)
                        ]
                        completed.extend(legacy_items)
                        if tuple(completed) != tuple(
                            _item_identity(item) for item in output
                        ):
                            raise _malformed(
                                "terminal output conflicts with streamed items"
                            )
                    else:
                        # Local tool dispatch requires item lifecycle evidence.
                        # A terminal-only response can recover assistant text/refusal.
                        messages = [
                            item
                            for item in output
                            if getattr(item, "type", None) == "message"
                        ]
                        if (
                            getattr(response, "error", None) is not None
                            or not messages
                            or any(
                                getattr(item, "type", None)
                                not in {"message", "reasoning"}
                                for item in output
                            )
                            or any(
                                getattr(item, "role", None) != "assistant"
                                or any(
                                    getattr(part, "type", None)
                                    not in {"output_text", "refusal"}
                                    for part in getattr(item, "content", ())
                                )
                                for item in messages
                            )
                        ):
                            raise _malformed("ambiguous terminal-only output")
                        ids = [
                            identity
                            for item in output
                            if (identity := getattr(item, "id", None))
                        ]
                        if len(ids) != len(set(ids)):
                            raise _malformed("duplicate terminal output item id")
                        for frame in completed_responses_output_events(response):
                            budget.responses_event(frame)
                            yield frame
                terminal_seen = True

            if kind in _RESPONSE_EVENTS or not kind.startswith("response."):
                yield event
                continue

            item_id = (
                getattr(event.item, "id", None)
                if kind in {_ADDED, _DONE}
                else getattr(event, "item_id", None)
            )
            if index is None:
                # Older providers and synthetic streams may omit all indices.
                legacy_output_seen = True
                if kind == _ADDED:
                    legacy.item_id = item_id
                    legacy_start = True
                elif kind == _DONE:
                    if item_id:
                        if item_id in legacy_completions:
                            raise _malformed("repeated completed output item")
                        legacy_completions.add(item_id)
                    legacy_items.append(_item_identity(event.item))
                    legacy = _OutputItem()
                    legacy_start = False
                else:
                    legacy.observe_arguments(event, kind)
                yield event
                continue

            item = items.get(index)
            if item is None:
                budget.reserve_response_item(index)
                item = items[index] = _OutputItem()
            if item_id and owners.setdefault(item_id, index) != index:
                raise _malformed("duplicate output item id")

            # Keep a legacy opener when it already introduced this item before
            # a compatible provider supplied its index.
            if (
                index == next_index
                and legacy_start
                and not item.ready
                and kind != _ADDED
                and legacy.item_id == item_id
            ):
                item.presented = True
                item.arguments = legacy.arguments
                legacy = _OutputItem()
                legacy_start = False

            was_done = item.done
            item.observe(event, kind, item_id)
            if was_done and kind in {
                "response.output_text.done",
                "response.refusal.done",
            }:
                # A late part snapshot cannot alter already finalized content.
                continue
            if index < next_index:
                yield event
                continue
            if index > next_index or not item.ready:
                item.buffer(event, budget)
                continue

            is_opening = event is item.opening
            if not item.presented or item.pending:
                for frame in item.release(budget):
                    yield frame
            if not is_opening:
                yield event
            while item.done:
                next_index += 1
                following = items.get(next_index)
                if following is None or not following.ready:
                    break
                item = following
                for frame in item.release(budget):
                    yield frame
    except ProviderStreamError:
        # Drain usage while the normalized/raw stream is still owned.
        if recover_usage is not None:
            await recover_usage()
        raise
    finally:
        if (close := getattr(events, "aclose", None)) is not None:
            await close()
