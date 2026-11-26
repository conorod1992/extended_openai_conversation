"""Serialize indexed Responses items for HA's single active assistant entry."""

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from .non_streaming import completed_responses_output_events
from .provider_errors import ProviderStreamError, ensure_successful_responses_result
from .provider_output_budget import (
    MAX_ARGUMENT_CHARACTERS,
    MAX_OUTPUT_ITEMS,
    ProviderOutputBudget,
    responses_array,
)

_ADDED = "response.output_item.added"
_DONE = "response.output_item.done"
_ARGUMENT_DELTA = "response.function_call_arguments.delta"
_ARGUMENT_DONE = "response.function_call_arguments.done"
_ARGUMENT_EVENTS = {_ARGUMENT_DELTA, _ARGUMENT_DONE}
_PART_SNAPSHOTS = {"response.output_text.done", "response.refusal.done"}
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
    arguments_done: bool = False
    opening: Any = None
    presented: bool = False
    done: bool = False
    pending: list[Any] = field(default_factory=list)
    pending_weight: int = 0

    @property
    def ready(self) -> bool:
        return self.presented or self.opening is not None

    def bind_identity(self, item_id: str | None, kind: str) -> None:
        if item_id:
            if self.item_id and self.item_id != item_id:
                reason = (
                    "conflicting argument item"
                    if kind in _ARGUMENT_EVENTS
                    else "conflicting output item id"
                )
                raise _malformed(reason)
            self.item_id = item_id

    def observe(self, event: Any, kind: str, item_id: str | None) -> None:
        self.bind_identity(item_id, kind)
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
                self.complete(item)
                # A done-only provider still needs an opening to select the
                # correct role and phase before presenting deferred content.
                if not self.ready:
                    self.opening = SimpleNamespace(
                        type=_ADDED, item=item, output_index=event.output_index
                    )
        else:
            self.observe_arguments(event, kind)

    def observe_arguments(self, event: Any, kind: str) -> None:
        identity = self.signature or self.completion
        if kind in _ARGUMENT_EVENTS and identity and identity[0] != "function_call":
            raise _malformed("tool arguments belong to a non-function output item")
        if kind == _ARGUMENT_DELTA:
            if self.arguments_done:
                raise _malformed("tool argument delta after completion")
            if len(self.arguments or "") + len(event.delta) > MAX_ARGUMENT_CHARACTERS:
                raise ProviderStreamError(
                    "Provider output exceeded local safety limits",
                    error_type="output_limit_exceeded",
                )
            self.arguments = (self.arguments or "") + event.delta
        elif kind == _ARGUMENT_DONE:
            self.check_arguments(event.arguments)
            self.arguments = event.arguments
            self.arguments_done = True

    def complete(self, item: Any) -> None:
        arguments = getattr(item, "arguments", None)
        self.check_arguments(arguments)
        self.arguments = arguments
        self.arguments_done = self.done = True
        self.completion = _item_identity(item)

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
    legacy_message_finished = False
    legacy_completions: dict[str, _OutputItem] = {}
    last_legacy_completed: _OutputItem | None = None
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
                if legacy.arguments is not None:
                    raise _malformed("tool arguments have no completed output item")
                response = getattr(event, "response", None)
                ensure_successful_responses_result(response)
                output = responses_array(
                    getattr(response, "output", ()), "response output"
                )
                if output:
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
                                    for part in responses_array(
                                        getattr(item, "content", ()), "message content"
                                    )
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
            if index is None and kind in _ARGUMENT_EVENTS:
                if item_id:
                    index = owners.get(item_id)
                    if index is None and (
                        legacy_item := legacy_completions.get(item_id)
                    ):
                        legacy_item.observe_arguments(event, kind)
                        yield event
                        continue
                elif not legacy_start and legacy.arguments is None:
                    candidates = [
                        candidate
                        for candidate, item in items.items()
                        if item.signature and item.signature[0] == "function_call"
                    ]
                    last_was_function = bool(
                        last_legacy_completed
                        and last_legacy_completed.completion
                        and last_legacy_completed.completion[0] == "function_call"
                    )
                    if len(candidates) + last_was_function > 1:
                        raise _malformed("ambiguous unindexed tool arguments")
                    if candidates:
                        index = candidates[0]
                    elif last_was_function and last_legacy_completed is not None:
                        last_legacy_completed.observe_arguments(event, kind)
                        yield event
                        continue
            if index is None:
                # Older providers and synthetic streams may omit all indices.
                if kind in _PART_SNAPSHOTS and (
                    (
                        item_id
                        and (
                            item_id in legacy_completions
                            or (item_id in owners and items[owners[item_id]].done)
                        )
                    )
                    or (not item_id and legacy_message_finished)
                ):
                    continue
                legacy_output_seen = True
                if kind == _ADDED:
                    legacy.bind_identity(item_id, kind)
                    legacy_start = True
                    legacy_message_finished = False
                elif kind == _DONE:
                    legacy.bind_identity(item_id, kind)
                    legacy.complete(event.item)
                    if completed_id := legacy.item_id:
                        if completed_id in legacy_completions:
                            raise _malformed("repeated completed output item")
                        legacy_completions[completed_id] = legacy
                    last_legacy_completed = legacy
                    legacy_message_finished = (
                        getattr(event.item, "type", None) == "message"
                    )
                    legacy_items.append(_item_identity(event.item))
                    legacy = _OutputItem()
                    legacy_start = False
                else:
                    if kind in _ARGUMENT_EVENTS:
                        legacy.bind_identity(item_id, kind)
                        legacy.observe_arguments(event, kind)
                    elif kind in {
                        "response.output_text.delta",
                        "response.refusal.delta",
                        "response.content_part.added",
                    }:
                        legacy_message_finished = False
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
            legacy_opener_matches = (
                index == next_index
                and legacy_start
                and not item.ready
                and kind != _ADDED
                and legacy.item_id == item_id
            )
            if legacy_opener_matches:
                item.presented = True
            if legacy_opener_matches or (
                item_id and legacy.item_id == item_id and legacy.arguments is not None
            ):
                if item.arguments is not None and legacy.arguments is not None:
                    if not item.arguments_done or not legacy.arguments_done:
                        raise _malformed("ambiguous mixed-index tool arguments")
                    item.check_arguments(legacy.arguments)
                elif legacy.arguments is not None:
                    item.arguments = legacy.arguments
                    item.arguments_done = legacy.arguments_done
                legacy = _OutputItem()
                legacy_start = False

            was_done = item.done
            item.observe(event, kind, item_id)
            if was_done and kind in _PART_SNAPSHOTS:
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
