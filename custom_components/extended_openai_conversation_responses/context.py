"""Conversation-history truncation helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any

from homeassistant.components import conversation


@dataclass(slots=True)
class HistoryParts:
    """System prefix and complete user turns."""

    prefix: list[conversation.Content]
    turns: list[list[conversation.Content]]


def partition_history(content: list[conversation.Content]) -> HistoryParts:
    """Group history without separating tool calls from their results."""
    prefix: list[conversation.Content] = []
    turns: list[list[conversation.Content]] = []
    current: list[conversation.Content] = []

    for item in content:
        if isinstance(item, conversation.UserContent):
            if current:
                turns.append(current)
            current = [item]
        elif current:
            current.append(item)
        else:
            prefix.append(item)
    if current:
        turns.append(current)
    return HistoryParts(prefix, turns)


def _weight(items: list[conversation.Content]) -> int:
    """Return a serialization weight used only to allocate reported token usage."""
    payload: list[Any] = []
    for item in items:
        try:
            payload.append(item.as_dict())
        except AttributeError, TypeError, ValueError:
            payload.append(str(item))
    return max(1, len(json.dumps(payload, ensure_ascii=False, default=str).encode()))


def retained_turns(
    parts: HistoryParts,
    observed_input_tokens: int,
    target_tokens: int,
) -> list[list[conversation.Content]]:
    """Choose newest complete turns using provider-reported input token usage."""
    if len(parts.turns) <= 1:
        return parts.turns

    all_items = [*parts.prefix, *(item for turn in parts.turns for item in turn)]
    total_weight = _weight(all_items)
    remaining_weight = total_weight
    retained = list(parts.turns)
    estimated_tokens = observed_input_tokens

    while len(retained) > 1 and estimated_tokens > target_tokens:
        removed = retained.pop(0)
        remaining_weight -= _weight(removed)
        estimated_tokens = math.ceil(
            observed_input_tokens * max(1, remaining_weight) / total_weight
        )
    return retained


def keep_recent_messages(
    content: list[conversation.Content],
    observed_input_tokens: int,
    target_tokens: int,
) -> bool:
    """Discard oldest complete turns while keeping system and recent context."""
    parts = partition_history(content)
    retained = retained_turns(parts, observed_input_tokens, target_tokens)
    updated = [*parts.prefix, *(item for turn in retained for item in turn)]
    if len(updated) == len(content):
        return False
    content[:] = updated
    return True


def select_summary_history(
    content: list[conversation.Content],
    observed_input_tokens: int,
    target_tokens: int,
) -> tuple[list[conversation.Content], list[conversation.Content]] | None:
    """Return older content to summarize and recent valid raw content to retain."""
    parts = partition_history(content)
    if len(parts.turns) <= 1:
        return None

    # Reserve roughly one third of the configured context for the generated summary.
    recent = retained_turns(
        parts, observed_input_tokens, max(1, target_tokens * 2 // 3)
    )
    recent_count = max(1, len(recent))
    older_turns = parts.turns[:-recent_count]
    if not older_turns:
        older_turns = parts.turns[:-1]
        recent = parts.turns[-1:]
    if not older_turns:
        return None
    older = [item for turn in older_turns for item in turn]
    raw_recent = [item for turn in recent for item in turn]
    return older, [*parts.prefix, *raw_recent]


def history_as_summary_text(items: list[conversation.Content]) -> str:
    """Render selected history as inert text for a bounded summarization request."""
    lines: list[str] = []
    for item in items:
        role = getattr(item, "role", "unknown")
        if content := getattr(item, "content", None):
            lines.append(f"{role}: {content}")
        for tool_call in getattr(item, "tool_calls", None) or []:
            lines.append(
                f"assistant tool call {tool_call.tool_name}: "
                f"{json.dumps(tool_call.tool_args, ensure_ascii=False)}"
            )
        if isinstance(item, conversation.ToolResultContent):
            lines.append(
                f"tool result {item.tool_name}: "
                f"{json.dumps(item.tool_result, ensure_ascii=False, default=str)}"
            )
        native = getattr(item, "native", None)
        if native is not None and not content:
            if hasattr(native, "model_dump"):
                native = native.model_dump(exclude_none=True)
            lines.append(
                f"assistant native item: {json.dumps(native, ensure_ascii=False, default=str)}"
            )
    return "\n".join(lines)
