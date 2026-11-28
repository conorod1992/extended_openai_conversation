"""Extract user-facing final answers without modifying native replay history."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Literal

from homeassistant.components import conversation

from .ha_tool_result_compat import is_tool_result_content
from .provider_content import answer_text

type ProviderOutputKind = Literal[
    "answer", "commentary", "reasoning", "hosted_tool", "replay_only"
]


def classify_provider_output(item: Any) -> ProviderOutputKind:
    """Interpret provider metadata without changing its retained replay value.

    Legacy Chat output and unphased Responses messages are answers. Recognized
    progress/replay items are never spoken; unknown native types remain replay
    content rather than being guessed to contain a final answer.
    """
    native_type = (
        item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
    )
    if not native_type or native_type == "message":
        role = (
            item.get("role") if isinstance(item, dict) else getattr(item, "role", None)
        )
        if role not in (None, "assistant"):
            return "replay_only"
        phase = (
            item.get("phase")
            if isinstance(item, dict)
            else getattr(item, "phase", None)
        )
        return "commentary" if phase == "commentary" else "answer"
    if native_type == "reasoning":
        return "reasoning"
    if native_type == "web_search_call":
        return "hosted_tool"
    return "replay_only"


def responses_answer_text(output: Sequence[Any]) -> str:
    """Read completed assistant answer parts, including unphased text."""
    return "".join(
        answer_text(part)
        for item in output
        if getattr(item, "type", None) == "message"
        and getattr(item, "role", None) == "assistant"
        and getattr(item, "status", None) in (None, "completed")
        and classify_provider_output(item) == "answer"
        for part in (getattr(item, "content", None) or ())
        if getattr(part, "type", None) == "output_text"
    )


@asynccontextmanager
async def provider_output_presentation(
    chat_log: Any,
) -> AsyncIterator[Callable[[Any], None]]:
    """Filter listener text while ChatLog keeps the complete original stream.

    HA excludes native metadata from listener deltas. Bind its interpretation
    before yielding the item's text. The Responses assembler resolves ambiguous
    opening phases before yielding text to this listener.
    """
    listener = getattr(chat_log, "delta_listener", None)
    kind: ProviderOutputKind = "answer"

    def select(item: Any) -> None:
        nonlocal kind
        kind = classify_provider_output(item)

    def present(log: Any, delta: dict[str, Any]) -> None:
        if kind != "answer":
            delta = {
                key: value
                for key, value in delta.items()
                if key not in {"content", "thinking_content"}
            }
        if delta and listener is not None:
            listener(log, delta)

    if listener is not None:
        chat_log.delta_listener = present
    try:
        yield select
    finally:
        if listener is not None and chat_log.delta_listener is present:
            chat_log.delta_listener = listener


@contextmanager
def separate_assistant_messages(chat_log: Any, *, enabled: bool) -> Iterator[None]:
    """Separate spoken answer messages after progress filtering and cleanup."""
    listener = getattr(chat_log, "delta_listener", None)
    if not enabled or listener is None:
        yield
        return
    spoke_text = False
    starts_message = False

    def present(log: Any, delta: dict[str, Any]) -> None:
        nonlocal spoke_text, starts_message
        if delta.get("role") == "assistant":
            starts_message = True
        content = delta.get("content")
        if isinstance(content, str) and content:
            if starts_message and spoke_text:
                delta = {**delta, "content": "\n\n" + content}
            spoke_text = True
            starts_message = False
        listener(log, delta)

    chat_log.delta_listener = present
    try:
        yield
    finally:
        if chat_log.delta_listener is present:
            chat_log.delta_listener = listener


def final_assistant_text(
    content: Sequence[conversation.Content], *, separator: str = "\n\n"
) -> str:
    """Return the final text for display."""
    return final_assistant_answer(content, separator=separator) or ""


def final_assistant_answer(
    content: Sequence[conversation.Content], *, separator: str = "\n\n"
) -> str | None:
    """Combine the final answer after the latest user/tool exchange.

    Responses may emit several message items, interspersed with reasoning or
    hosted-tool records. Commentary is progress, not part of the final answer.
    Native items and their IDs remain untouched for subsequent provider replay.
    """
    texts: list[str] = []
    found_answer = False
    for item in reversed(content):
        if isinstance(item, conversation.UserContent) or is_tool_result_content(item):
            break
        if not isinstance(item, conversation.AssistantContent) or getattr(
            item, "tool_calls", None
        ):
            continue
        native = getattr(item, "native", None)
        if classify_provider_output(native) != "answer":
            continue
        if item.content is not None or getattr(native, "type", None) == "message":
            found_answer = True
            if item.content:
                texts.append(item.content)
    return separator.join(reversed(texts)) if found_answer else None


def responses_leave_tool_results_unanswered(
    content: Sequence[conversation.Content],
) -> bool:
    """Check the current exchange while ignoring replay-only provider output."""
    for item in reversed(content):
        if isinstance(item, conversation.UserContent):
            break
        if is_tool_result_content(item):
            return True
        if (
            isinstance(item, conversation.AssistantContent)
            and not item.tool_calls
            and classify_provider_output(item.native) == "answer"
            and (
                (item.content or "").strip()
                # A completed assistant message can deliberately be empty.
                or getattr(item.native, "type", None) == "message"
            )
        ):
            break
    return False
