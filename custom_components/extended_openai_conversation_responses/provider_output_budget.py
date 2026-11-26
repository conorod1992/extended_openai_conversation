"""Local admission ceilings for one provider response, before EOAI retains output.

These generous character/work ceilings are independent of model token settings and
model-facing tool-result limits. They bound EOAI accumulation, not SDK HTTP reads.
"""

from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from itertools import chain
from typing import Any

from .chat_tool_calls import ChatToolCallResolver, ChatToolDelta
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


def _responses_string_length(value: Any, field: str) -> int:
    if not isinstance(value, str):
        raise ProviderStreamError(
            f"Provider returned malformed data: {field} must be a string",
            error_type="invalid_event_sequence",
        )
    return len(value)


def responses_array(value: Any, field: str) -> list[Any] | tuple[Any, ...]:
    """Validate an external Responses collection before callers iterate it."""
    if not isinstance(value, (list, tuple)):
        raise ProviderStreamError(
            f"Provider returned malformed data: {field} must be an array",
            error_type="invalid_event_sequence",
        )
    return value


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
        self._chat_tools = ChatToolCallResolver(MAX_OUTPUT_ITEMS)

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

    def chat_delta(self, delta: Any) -> list[ChatToolDelta]:
        for value in (getattr(delta, "content", None), getattr(delta, "refusal", None)):
            if value:
                self._account(
                    ("chat", "answer"),
                    content=_text_weight(value),
                )
        raw_calls = getattr(delta, "tool_calls", None) or []
        # Bound temporary normalization work too, including zero-text fragments
        # that consume neither the character nor logical-item allowance.
        if len(raw_calls) > MAX_OUTPUT_NODES:
            _exceeded()
        calls = self._chat_tools.resolve(raw_calls)
        for call in calls:
            self._account(
                ("chat", call.key),
                content=call.identity_characters,
                arguments=len(call.arguments),
            )
        return calls

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
        content_index = getattr(event, "content_index", None)
        if content_index is not None and (
            type(content_index) is not int or content_index < 0
        ):
            raise ProviderStreamError(
                "Provider returned malformed data: invalid content index",
                error_type="invalid_event_sequence",
            )
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
            if (
                arguments is not None
                or (
                    kind.endswith(".done")
                    and getattr(item, "type", None) == "function_call"
                )
            ) and _responses_string_length(
                arguments, "tool arguments"
            ) > MAX_ARGUMENT_CHARACTERS:
                _exceeded()
            self._account(key, native=item)
            if getattr(item, "type", None) == "message":
                parts = getattr(item, "content", ())
                if parts is None and kind == "response.output_item.added":
                    parts = ()  # A streaming opener can be a content placeholder.
                for part in responses_array(parts, "message content"):
                    if getattr(part, "type", None) == "output_text":
                        _responses_string_length(
                            getattr(part, "text", ""), "output text"
                        )
                    elif getattr(part, "type", None) == "refusal":
                        _responses_string_length(
                            getattr(part, "refusal", ""), "refusal text"
                        )
            if index is None and kind.endswith(".done"):
                self._active_unindexed = None
        elif kind in {"response.output_text.delta", "response.refusal.delta"}:
            delta = getattr(event, "delta", None)
            self._text(
                key,
                (
                    key,
                    content_index,
                    kind.removesuffix(".delta"),
                ),
                _responses_string_length(delta, "text delta")
                if kind == "response.output_text.delta"
                else _text_weight(delta),
            )
        elif kind == "response.function_call_arguments.delta":
            self._account(
                key,
                arguments=_responses_string_length(
                    getattr(event, "delta", None), "tool argument delta"
                ),
            )
        elif kind == "response.function_call_arguments.done":
            if (
                _responses_string_length(
                    getattr(event, "arguments", None), "tool arguments"
                )
                > MAX_ARGUMENT_CHARACTERS
            ):
                _exceeded()
        elif kind in {"response.output_text.done", "response.refusal.done"}:
            self._text(
                key,
                (
                    key,
                    content_index,
                    kind.removesuffix(".done"),
                ),
                _responses_string_length(getattr(event, "text", None), "output text")
                if kind == "response.output_text.done"
                else _text_weight(getattr(event, "refusal", None)),
                complete=True,
            )
        elif kind == "response.output_text.annotation.added":
            self._account(
                key, content=output_weight(event.annotation, MAX_OUTPUT_CHARACTERS)
            )

    def reserve_response_item(self, index: int) -> None:
        """Count indexed items even when their first event is only metadata."""
        self._account(("responses", index))

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
