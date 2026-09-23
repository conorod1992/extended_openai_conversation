"""Adapt completed OpenAI responses to the existing stream consumers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any


async def completed_responses_events(response: Any) -> AsyncIterator[Any]:
    """Emit only the events consumed by EOAI's Responses transformer."""
    for output_index, item in enumerate(getattr(response, "output", ())):
        yield SimpleNamespace(type="response.output_item.added", item=item)
        if getattr(item, "type", None) == "message":
            for content_index, part in enumerate(getattr(item, "content", ())):
                part_type = getattr(part, "type", None)
                if part_type == "output_text":
                    text = getattr(part, "text", "")
                    if text:
                        yield SimpleNamespace(
                            type="response.output_text.delta",
                            delta=text,
                            output_index=output_index,
                            content_index=content_index,
                        )
                    for annotation in getattr(part, "annotations", ()):
                        yield SimpleNamespace(
                            type="response.output_text.annotation.added",
                            annotation=annotation,
                            output_index=output_index,
                            content_index=content_index,
                        )
                elif part_type == "refusal":
                    yield SimpleNamespace(
                        type="response.refusal.done",
                        refusal=getattr(part, "refusal", ""),
                        output_index=output_index,
                        content_index=content_index,
                    )
        yield SimpleNamespace(type="response.output_item.done", item=item)
    status = getattr(response, "status", "completed")
    event_type = {
        "completed": "response.completed",
        "incomplete": "response.incomplete",
        "failed": "response.failed",
    }.get(status, "response.failed")
    yield SimpleNamespace(type=event_type, response=response)


async def completed_chat_chunks(response: Any) -> AsyncIterator[Any]:
    """Emit one completed choice and its usage through the existing chat parser."""
    for choice in getattr(response, "choices", ()):
        message = choice.message
        tool_calls = [
            SimpleNamespace(index=index, id=tool.id, function=tool.function)
            for index, tool in enumerate(getattr(message, "tool_calls", ()) or ())
        ]
        delta = SimpleNamespace(
            content=getattr(message, "content", None),
            refusal=getattr(message, "refusal", None),
            tool_calls=tool_calls,
        )
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=delta, finish_reason=choice.finish_reason)],
            usage=None,
        )
    if getattr(response, "usage", None) is not None:
        yield SimpleNamespace(choices=[], usage=response.usage)
