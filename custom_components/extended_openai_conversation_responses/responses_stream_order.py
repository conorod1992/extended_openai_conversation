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
_PART_SNAPSHOTS = {
    "response.output_text.done",
    "response.refusal.done",
    "response.content_part.done",
}
_CONTENT_EVENTS = {
    *_PART_SNAPSHOTS,
    "response.output_text.delta",
    "response.refusal.delta",
    "response.output_text.annotation.added",
    "response.content_part.added",
}
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


type _MessageParts = tuple[tuple[Any, Any, Any], ...]


def _part_identity(part: Any) -> tuple[Any, Any, Any]:
    return (
        getattr(part, "type", None),
        getattr(part, "text", None),
        getattr(part, "refusal", None),
    )


def _item_identity(item: Any) -> tuple[Any, ...]:
    identity = tuple(
        getattr(item, field, None)
        for field in ("type", "id", "call_id", "name", "arguments")
    )
    if identity[0] != "message":
        return identity
    return (
        *identity,
        getattr(item, "role", None),
        getattr(item, "phase", None),
        tuple(
            _part_identity(part)
            for part in responses_array(getattr(item, "content", ()), "message content")
        ),
    )


def _reconcile_item_metadata(streamed: Any, terminal: Any) -> None:
    """Retain terminal details alongside the validated streamed item body."""
    kind = getattr(terminal, "type", None)
    if kind == "web_search_call":
        status = getattr(terminal, "status", None)
        prior_status = getattr(streamed, "status", None)
        if prior_status and status and prior_status != status:
            raise _malformed("conflicting hosted search status")
        action = getattr(terminal, "action", None)
        prior_action = getattr(streamed, "action", None)
        if action is not None and prior_action is not None:
            for field in ("type", "query", "url", "pattern"):
                before = getattr(prior_action, field, None)
                after = getattr(action, field, None)
                if before is not None and after is not None and before != after:
                    raise _malformed("conflicting hosted search action")
        if action is not None:
            streamed.action = action
        if status is not None:
            streamed.status = status
    elif kind == "message":
        streamed_parts = getattr(streamed, "content", None) or ()
        terminal_parts = getattr(terminal, "content", None) or ()
        for before, after in zip(streamed_parts, terminal_parts, strict=True):
            annotations = getattr(after, "annotations", None)
            if annotations:
                before.annotations = annotations


@dataclass
class _IndexedContentEvent:
    """Keep the provider's event intact while associating it with a known item."""

    event: Any
    output_index: int

    def __getattr__(self, name: str) -> Any:
        return getattr(self.event, name)


@dataclass(slots=True)
class _OutputItem:
    """Own one index's wire identity, completion and deferred presentation."""

    item_id: str | None = None
    signature: tuple[Any, ...] | None = None
    completion: tuple[Any, ...] | None = None
    completed_item: Any = None
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
        self.completed_item = item

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


@dataclass(slots=True)
class _MessagePart:
    kind: str
    chunks: list[str] = field(default_factory=list)
    snapshot: str | None = None
    emitted: int = 0
    received: int = 0

    def complete(self, text: str) -> None:
        valid = (
            text == self.snapshot
            if self.snapshot is not None
            else text.startswith("".join(self.chunks))
        )
        if not valid:
            raise _malformed("completed message text conflicts with streamed text")
        self.snapshot = text

    def remaining(self) -> Iterator[str]:
        if not self.emitted:
            yield from self.chunks
        if self.snapshot is not None and (tail := self.snapshot[self.received :]):
            yield tail


def _message_text(event: Any) -> tuple[str, str] | None:
    kind = event.type
    if kind == "response.content_part.done":
        part = getattr(event, "part", None)
        part_type = getattr(part, "type", None)
        if part_type not in {"output_text", "refusal"}:
            return None
        value = getattr(part, "text" if part_type == "output_text" else "refusal", None)
    elif kind in _TEXT_EVENTS:
        part_type = "refusal" if ".refusal." in kind else "output_text"
        attr = (
            "delta"
            if kind.endswith(".delta")
            else ("refusal" if part_type == "refusal" else "text")
        )
        value = getattr(event, attr, None)
    else:
        return None
    if not isinstance(value, str):
        if part_type == "refusal" and value is not None:
            value = str(value)  # Legacy compatible providers use refusal objects.
        else:
            raise _malformed("invalid message text")
    return part_type, value


class ResponseMessageText:
    """Assemble one ordered assistant item before publishing ambiguous text."""

    def __init__(self) -> None:
        self.opened = False
        self.phase: str | None = None
        self.parts: dict[int, _MessagePart] = {}
        self.unindexed = False

    def start(self, item: Any) -> None:
        if self.opened or self.parts:
            raise _malformed("message started before previous text completed")
        self.opened = True
        self.phase = getattr(item, "phase", None)

    def observe(self, event: Any) -> str:
        """Stream the first part only when its phase is already established."""
        if (text := _message_text(event)) is None:
            return ""
        part_type, value = text
        index = getattr(event, "content_index", None)
        if index is None:
            self.unindexed = True
            index = 0
        part_state = self.parts.setdefault(index, _MessagePart(part_type))
        if part_state.kind != part_type:
            raise _malformed("conflicting message content part type")
        if event.type.endswith(".delta"):
            if part_state.snapshot is not None:
                raise _malformed("text delta after completed content part")
            if value:
                part_state.chunks.append(value)
                part_state.received += len(value)
            if index == 0 and self.phase in {"commentary", "final_answer"}:
                part_state.emitted += len(value)
                return value
        else:
            part_state.complete(value)
        return ""

    def complete(self, item: Any) -> Iterator[str]:
        """Reconcile snapshots before emitting the remaining parts in order."""
        content = getattr(item, "content", None)
        phase = getattr(item, "phase", None)
        if phase is None and content is None:
            phase = self.phase
        if (self.phase == "commentary" and phase != "commentary") or (
            self.phase == "final_answer" and phase not in {None, "final_answer"}
        ):
            raise _malformed("conflicting assistant message phase")
        if content is not None:
            if self.unindexed and len(content) > 1:
                raise _malformed("unindexed text belongs to an ambiguous message part")
            if self.parts.keys() - range(len(content)):
                raise _malformed("completed message is missing a streamed part")
            for index, native in enumerate(content):
                part_type = getattr(native, "type", None)
                if part_type not in {"output_text", "refusal"}:
                    if index in self.parts:
                        raise _malformed("conflicting completed message content part")
                    continue
                text = getattr(
                    native, "text" if part_type == "output_text" else "refusal", ""
                )
                part = self.parts.setdefault(index, _MessagePart(part_type))
                if part.kind != part_type:
                    raise _malformed("conflicting completed message content part")
                part.complete(text)
        yield from self.remaining()

    def received_length(self, index: int | None) -> int:
        part = self.parts.get(index or 0)
        if part is None:
            return 0
        return len(part.snapshot) if part.snapshot is not None else part.received

    def remaining(self) -> Iterator[str]:
        for _, part in sorted(self.parts.items()):
            yield from part.remaining()


_TEXT_EVENTS = {
    "response.output_text.delta",
    "response.output_text.done",
    "response.refusal.delta",
    "response.refusal.done",
}


@dataclass(slots=True)
class _DeferredMessageSnapshots:
    """Bound anonymous snapshots whose output item is not known yet."""

    events: list[Any] = field(default_factory=list)
    weight: int = 0

    def add(self, event: Any, budget: ProviderOutputBudget) -> None:
        self.weight += budget.buffer(event)
        self.events.append(event)

    def clear(self, budget: ProviderOutputBudget) -> None:
        budget.release_buffer(self.weight, len(self.events))
        self.events.clear()
        self.weight = 0

    def confirms(self, previous: _MessageParts, candidate: Any) -> bool:
        """Require evidence for one new message and account for any late duplicates."""
        content = getattr(candidate, "content", None)
        if (
            not self.events
            or getattr(candidate, "type", None) != "message"
            or getattr(candidate, "role", None) not in (None, "assistant")
            or content is None
        ):
            return False
        parts = responses_array(content, "message content")
        confirmed = False
        for event in self.events:
            if (text := _message_text(event)) is None:
                continue
            part_type, value = text
            snapshot = (
                (part_type, value, None)
                if part_type == "output_text"
                else (part_type, None, value)
            )
            content_index = getattr(event, "content_index", None)
            index = content_index or 0
            if (
                (content_index is not None or len(parts) == 1)
                and index < len(parts)
                and _part_identity(parts[index]) == snapshot
            ):
                confirmed = True
            elif index >= len(previous) or previous[index] != snapshot:
                return False
        return confirmed


async def ordered_output_events(
    events: AsyncIterator[Any],
    *,
    recover_usage: Callable[[], Awaitable[None]] | None = None,
    record_web_search: Callable[[], None] | None = None,
) -> AsyncGenerator[Any]:
    """Validate in wire order and present each indexed item as one HA entry."""
    budget = ProviderOutputBudget()
    items: dict[int, _OutputItem] = {}
    owners: dict[str, int] = {}
    next_index = 0
    terminal_seen = False
    legacy = _OutputItem()
    legacy_start = False
    legacy_message_active = False
    implicit_legacy_text = False
    legacy_completions: dict[str, _OutputItem] = {}
    legacy_native: list[Any] = []
    last_legacy_completed: _OutputItem | None = None
    legacy_items: list[tuple[Any, ...]] = []
    legacy_output_seen = False
    # A snapshot without an owner can be late or introduce an implicit message.
    finished_parts: _MessageParts | None = None
    deferred_snapshots = _DeferredMessageSnapshots()

    def advance_message_boundary(completion: tuple[Any, ...] | None = None) -> None:
        nonlocal finished_parts
        if finished_parts is not None:
            deferred_snapshots.clear(budget)
        finished_parts = (
            completion[-1] if completion and completion[0] == "message" else None
        )

    try:
        async for event in events:
            index = getattr(event, "output_index", None)
            if index is not None and (type(index) is not int or index < 0):
                raise _malformed("invalid output index")
            kind = getattr(event, "type", "")
            anonymous_output_index = None
            late_snapshot = defer_snapshot = False
            if index is None and kind in _CONTENT_EVENTS:
                content_owner = getattr(event, "item_id", None)
                if isinstance(content_owner, str) and content_owner in owners:
                    index = owners[content_owner]
                    event = _IndexedContentEvent(event, index)
                elif not content_owner and (active := items.get(next_index)):
                    if (
                        active.presented
                        and active.signature
                        and active.signature[0] == "message"
                    ):
                        # The visible indexed message already owns anonymous parts.
                        anonymous_output_index = next_index
                if index is None and kind in _PART_SNAPSHOTS:
                    late_snapshot = (
                        isinstance(content_owner, str)
                        and content_owner in legacy_completions
                    )
                    defer_snapshot = (
                        not content_owner
                        and anonymous_output_index is None
                        and finished_parts is not None
                    )
            budget.responses_event(
                event,
                anonymous_output_index=anonymous_output_index,
                discard_snapshot=late_snapshot or defer_snapshot,
            )
            if terminal_seen and kind.startswith("response.") and kind not in _USAGE:
                raise _malformed("event after terminal response")
            if late_snapshot:
                continue
            if defer_snapshot:
                deferred_snapshots.add(event, budget)
                continue

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
                if len(output) > MAX_OUTPUT_ITEMS:
                    raise ProviderStreamError(
                        "Provider output exceeded local safety limits",
                        error_type="output_limit_exceeded",
                    )
                completed_count = next_index + len(legacy_items)
                final = output[-1] if len(output) == completed_count + 1 else None
                snapshot_message = (
                    finished_parts is not None
                    and deferred_snapshots.confirms(finished_parts, final)
                )
                advance_message_boundary()
                recovered = None
                if (
                    legacy_start
                    or legacy_message_active
                    or (implicit_legacy_text and output)
                    or snapshot_message
                ):
                    if (
                        final is None
                        or getattr(final, "type", None) != "message"
                        or (legacy.signature and legacy.signature[0] != "message")
                        or (
                            implicit_legacy_text
                            and (
                                getattr(final, "role", None) not in {None, "assistant"}
                                or getattr(final, "content", None) is None
                            )
                        )
                    ):
                        raise _malformed("unfinished legacy output item")
                    final_id = getattr(final, "id", None)
                    if final_id and (
                        final_id in legacy_completions or final_id in owners
                    ):
                        raise _malformed("repeated completed output item")
                    legacy.bind_identity(final_id, _DONE)
                    recovered = SimpleNamespace(type=_DONE, item=final)
                    budget.responses_event(recovered)
                    legacy_items.append(_item_identity(final))
                    legacy_native.append(final)
                if output:
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
                        completed_native = [
                            items[index].completed_item for index in range(next_index)
                        ]
                        completed_native.extend(legacy_native)
                        for streamed, terminal in zip(
                            completed_native, output, strict=True
                        ):
                            if streamed is not terminal:
                                _reconcile_item_metadata(streamed, terminal)
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
                if recovered is not None:
                    yield recovered
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
                legacy_output_seen = True
                if kind == _ADDED:
                    if legacy_start:
                        raise _malformed("unfinished legacy output item")
                    legacy.observe(event, kind, item_id)
                    legacy_start = True
                    implicit_legacy_text = False
                    advance_message_boundary()
                elif kind == _DONE:
                    if legacy.signature and legacy.signature[0] != getattr(
                        event.item, "type", None
                    ):
                        raise _malformed("conflicting legacy output item")
                    legacy.bind_identity(item_id, kind)
                    legacy.complete(event.item)
                    if completed_id := legacy.item_id:
                        if completed_id in legacy_completions:
                            raise _malformed("repeated completed output item")
                        legacy_completions[completed_id] = legacy
                    if (
                        record_web_search is not None
                        and getattr(event.item, "type", None) == "web_search_call"
                    ):
                        record_web_search()
                    last_legacy_completed = legacy
                    assert legacy.completion is not None
                    advance_message_boundary(legacy.completion)
                    legacy_items.append(legacy.completion)
                    legacy_native.append(legacy.completed_item)
                    legacy = _OutputItem()
                    legacy_start = legacy_message_active = implicit_legacy_text = False
                else:
                    if kind in _ARGUMENT_EVENTS:
                        legacy.bind_identity(item_id, kind)
                        legacy.observe_arguments(event, kind)
                    elif kind in _TEXT_EVENTS or kind.startswith(
                        "response.content_part."
                    ):
                        advance_message_boundary()
                        if anonymous_output_index is None:
                            if item_id or (
                                kind.startswith("response.content_part.")
                                or getattr(event, "content_index", None) is not None
                            ):
                                legacy.bind_identity(item_id, kind)
                                legacy_message_active = True
                            elif kind in _TEXT_EVENTS:
                                implicit_legacy_text = True
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
                legacy_start = legacy_message_active = implicit_legacy_text = False

            was_done = item.done
            item.observe(event, kind, item_id)
            if (
                kind == _DONE
                and record_web_search is not None
                and getattr(event.item, "type", None) == "web_search_call"
            ):
                record_web_search()
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
            if not item.presented:
                advance_message_boundary()
            if not item.presented or item.pending:
                for frame in item.release(budget):
                    yield frame
            if not is_opening:
                yield event
            while item.completion is not None:
                advance_message_boundary(item.completion)
                next_index += 1
                following = items.get(next_index)
                if following is None or not following.ready:
                    break
                item = following
                advance_message_boundary()
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
