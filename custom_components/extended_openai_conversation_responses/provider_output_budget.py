"""Local admission ceilings for one provider response, before EOAI retains output.

These generous character/work ceilings are independent of model token settings and
model-facing tool-result limits. They bound EOAI accumulation, not SDK HTTP reads.
"""

from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from itertools import chain
from typing import Any

from .provider_errors import ProviderStreamError

MAX_OUTPUT_CHARACTERS = 16 * 1024 * 1024
MAX_ARGUMENT_CHARACTERS = 2 * 1024 * 1024
MAX_OUTPUT_ITEMS = 1024
MAX_BUFFERED_CHARACTERS = 4 * 1024 * 1024
MAX_BUFFERED_EVENTS = 65536
MAX_OUTPUT_EVENTS = 1_000_000
MAX_OUTPUT_NODES = 65536


def _exceeded() -> None:
    raise ProviderStreamError(
        "Provider output exceeded local safety limits",
        error_type="output_limit_exceeded",
    )


def output_weight(value: Any, limit: int) -> int:
    """Count strings and structural overhead without serializing/copying SDK data.

    Iterators keep traversal space bounded even for wide containers. Structural
    overhead also bounds pathological nesting, cyclic objects and scalar floods.
    """
    size = 0
    nodes = 0
    stack = [iter((value,))]
    while stack:
        try:
            item = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        nodes += 1
        if nodes > MAX_OUTPUT_NODES:
            _exceeded()
        size += 16 + (len(item) if isinstance(item, (str, bytes)) else 0)
        if size > limit:
            _exceeded()
        if isinstance(item, Mapping):
            stack.append(chain.from_iterable(item.items()))
        elif isinstance(item, (list, tuple)):
            stack.append(iter(item))
        elif hasattr(item, "__dict__") and not callable(item):
            data = vars(item)
            extra = getattr(item, "__pydantic_extra__", None)
            stack.append(iter((data, extra)) if extra else iter((data,)))
    return size


def _text_weight(value: Any) -> int:
    return (
        len(value)
        if isinstance(value, str)
        else output_weight(value, MAX_OUTPUT_CHARACTERS)
    )


@dataclass
class _ItemSize:
    content: int = 0
    arguments: int = 0
    native: int = 0

    @property
    def retained(self) -> int:
        # Native done/snapshot payloads repeat text/arguments already streamed.
        return max(self.content + self.arguments, self.native)


class ProviderOutputBudget:
    """Shared policy; parsers only supply their format's logical item identities."""

    def __init__(self) -> None:
        self.items: dict[Hashable, _ItemSize] = {}
        self.total = 0
        self.events = 0
        self._started_items = 0
        self._parts: dict[Hashable, int] = {}
        self.buffered_weight = 0
        self.buffered_events = 0
        self._unindexed = 0
        self._active_unindexed: Hashable | None = None

    def event(self) -> None:
        self.events += 1
        if self.events > MAX_OUTPUT_EVENTS:
            _exceeded()

    def _account(
        self, key: Hashable, *, content: int = 0, arguments: int = 0, native: Any = None
    ) -> None:
        if key not in self.items:
            if len(self.items) >= MAX_OUTPUT_ITEMS:
                _exceeded()
            self.items[key] = _ItemSize()
        item = self.items[key]
        previous = item.retained
        if item.arguments + arguments > MAX_ARGUMENT_CHARACTERS:
            _exceeded()
        item.content += content
        item.arguments += arguments
        if native is not None:
            item.native = max(item.native, output_weight(native, MAX_OUTPUT_CHARACTERS))
        self.total += item.retained - previous
        if self.total > MAX_OUTPUT_CHARACTERS:
            _exceeded()

    def chat_delta(self, delta: Any) -> None:
        for value in (getattr(delta, "content", None), getattr(delta, "refusal", None)):
            if value:
                self._account(
                    ("chat", "answer"),
                    content=_text_weight(value),
                )
        for call in getattr(delta, "tool_calls", None) or ():
            key = ("chat", call.index)
            function = call.function
            self._account(
                key,
                content=len(call.id or "") + len(getattr(function, "name", None) or ""),
                arguments=len(getattr(function, "arguments", None) or ""),
            )

    def _text(
        self, key: Hashable, part: Hashable, size: int, *, complete: bool = False
    ) -> None:
        if part not in self._parts and len(self._parts) >= MAX_OUTPUT_ITEMS:
            _exceeded()
        previous = self._parts.get(part, 0)
        current = max(previous, size) if complete else previous + size
        self._account(key, content=current - previous)
        self._parts[part] = current

    def responses_event(self, event: Any) -> None:
        self.event()
        kind = getattr(event, "type", "")
        if kind == "response.output_item.added":
            self._started_items += 1
            if self._started_items > MAX_OUTPUT_ITEMS:
                _exceeded()
        item = getattr(event, "item", None)
        index = getattr(event, "output_index", None)
        if index is not None:
            key: Hashable = ("responses", index)
        else:
            identity = (
                getattr(item, "id", None)
                or getattr(item, "call_id", None)
                or getattr(event, "item_id", None)
            )
            if identity:
                key = ("responses-id", identity)
                if key not in self.items:
                    self._account(key, content=len(identity))
            elif (
                self._active_unindexed is not None
                and kind != "response.output_item.added"
            ):
                key = self._active_unindexed
            else:
                self._unindexed += 1
                key = ("unindexed", self._unindexed)
            if kind == "response.output_item.added":
                self._active_unindexed = key
        if kind in {"response.output_item.added", "response.output_item.done"}:
            arguments = getattr(item, "arguments", None)
            if isinstance(arguments, str) and len(arguments) > MAX_ARGUMENT_CHARACTERS:
                _exceeded()
            self._account(key, native=item)
            if index is None and kind.endswith(".done"):
                self._active_unindexed = None
        elif kind in {"response.output_text.delta", "response.refusal.delta"}:
            self._text(
                key,
                (
                    key,
                    getattr(event, "content_index", None),
                    kind.removesuffix(".delta"),
                ),
                _text_weight(event.delta),
            )
        elif kind == "response.function_call_arguments.delta":
            self._account(key, arguments=len(event.delta))
        elif kind == "response.function_call_arguments.done":
            if len(event.arguments) > MAX_ARGUMENT_CHARACTERS:
                _exceeded()
        elif kind == "response.refusal.done":
            self._text(
                key,
                (key, getattr(event, "content_index", None), "response.refusal"),
                _text_weight(event.refusal),
                complete=True,
            )
        elif kind == "response.output_text.annotation.added":
            self._account(
                key, content=output_weight(event.annotation, MAX_OUTPUT_CHARACTERS)
            )

    def buffer(self, event: Any) -> int:
        weight = output_weight(event, MAX_BUFFERED_CHARACTERS - self.buffered_weight)
        if self.buffered_events >= MAX_BUFFERED_EVENTS:
            _exceeded()
        self.buffered_weight += weight
        self.buffered_events += 1
        return weight

    def release_buffer(self, weight: int, events: int) -> None:
        self.buffered_weight -= weight
        self.buffered_events -= events
