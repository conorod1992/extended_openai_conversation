"""Extract user-facing final answers without modifying native replay history."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from homeassistant.components import conversation

from .ha_tool_result_compat import is_tool_result_content


def final_assistant_text(
    content: Sequence[conversation.Content], *, separator: str = "\n\n"
) -> str:
    """Combine the final answer after the latest user/tool exchange.

    Responses may emit several message items, interspersed with reasoning or
    hosted-tool records. Commentary is progress, not part of the final answer.
    Native items and their IDs remain untouched for subsequent provider replay.
    """
    texts: list[str] = []
    for item in reversed(content):
        if isinstance(item, conversation.UserContent) or is_tool_result_content(item):
            break
        if not isinstance(item, conversation.AssistantContent) or getattr(
            item, "tool_calls", None
        ):
            continue
        native: Any = getattr(item, "native", None)
        native_type = (
            native.get("type")
            if isinstance(native, dict)
            else getattr(native, "type", None)
        )
        phase = (
            native.get("phase")
            if isinstance(native, dict)
            else getattr(native, "phase", None)
        )
        if phase == "commentary" or (native_type and native_type != "message"):
            continue
        if item.content:
            texts.append(item.content)
    return separator.join(reversed(texts))
