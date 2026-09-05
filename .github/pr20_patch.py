from pathlib import Path

entity_path = Path("custom_components/extended_openai_conversation_responses/entity.py")
text = entity_path.read_text()

replacements = [
    (
        """        request_usage = request_usage or RequestUsage()
        current_tool_calls: dict[int, dict[str, Any]] = {}
        first_chunk = True
""",
        """        request_usage = request_usage or RequestUsage()
        current_tool_calls: dict[int, dict[str, Any]] = {}
        first_chunk = True
        refusal_seen = False
""",
        "chat init",
    ),
    (
        """                if content_value:
                    yield {"content": content_value}

            if delta.tool_calls:
""",
        """                if content_value:
                    yield {"content": content_value}

            refusal_value = getattr(delta, "refusal", None)
            if refusal_value:
                refusal_seen = True
                if not isinstance(refusal_value, str):
                    _LOGGER.warning(
                        "Received non-string refusal from API: %s (type: %s)",
                        refusal_value,
                        type(refusal_value),
                    )
                    refusal_value = str(refusal_value)
                if refusal_value:
                    yield {"content": refusal_value}

            if delta.tool_calls:
""",
        "chat refusal",
    ),
    (
        """            if choice.finish_reason == "length":
                raise TokenLengthExceededError(
                    self.subentry.data.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)
                )

            # Keep consuming after the stop chunk so providers that honor
""",
        """            if choice.finish_reason == "length":
                raise TokenLengthExceededError(
                    self.subentry.data.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)
                )
            if choice.finish_reason == "content_filter" and not refusal_seen:
                raise HomeAssistantError(
                    "OpenAI response was blocked by the provider content filter"
                )

            # Keep consuming after the stop chunk so providers that honor
""",
        "chat content filter",
    ),
    (
        """        request_usage = request_usage or RequestUsage()
        response_text_lengths: dict[tuple[int | None, int | None], int] = {}
        url_citations: dict[tuple[int | None, int | None], list[dict[str, Any]]] = {}
        async for event in result:
""",
        """        request_usage = request_usage or RequestUsage()
        response_text_lengths: dict[tuple[int | None, int | None], int] = {}
        response_refusal_lengths: dict[tuple[int | None, int | None], int] = {}
        url_citations: dict[tuple[int | None, int | None], list[dict[str, Any]]] = {}
        terminal_event_seen = False
        async for event in result:
""",
        "responses init",
    ),
    (
        """            if event_type == "response.output_text.annotation.added":
""",
        """            if event_type == "response.refusal.delta":
                refusal_delta = getattr(event, "delta", None)
                if refusal_delta:
                    if not isinstance(refusal_delta, str):
                        refusal_delta = str(refusal_delta)
                    part_key = (
                        getattr(event, "output_index", None),
                        getattr(event, "content_index", None),
                    )
                    response_refusal_lengths[part_key] = response_refusal_lengths.get(
                        part_key, 0
                    ) + len(refusal_delta)
                    yield {"content": refusal_delta}
                continue

            if event_type == "response.refusal.done":
                refusal = getattr(event, "refusal", None)
                if refusal:
                    if not isinstance(refusal, str):
                        refusal = str(refusal)
                    part_key = (
                        getattr(event, "output_index", None),
                        getattr(event, "content_index", None),
                    )
                    already_streamed = response_refusal_lengths.get(part_key, 0)
                    if len(refusal) > already_streamed:
                        yield {"content": refusal[already_streamed:]}
                    response_refusal_lengths[part_key] = max(
                        already_streamed, len(refusal)
                    )
                continue

            if event_type == "response.output_text.annotation.added":
""",
        "responses refusal",
    ),
    (
        """            if event_type in {"response.completed", "response.incomplete"}:
                response = event.response
""",
        """            if event_type in {"response.completed", "response.incomplete"}:
                if event_type == "response.completed":
                    terminal_event_seen = True
                response = event.response
""",
        "responses terminal",
    ),
    (
        """            if event_type in {"error", "response.error"}:
                reason = getattr(event, "message", None) or "unknown reason"
                raise HomeAssistantError(f"OpenAI response error: {reason}")

    async def _execute_function_tool(
""",
        """            if event_type in {"error", "response.error"}:
                reason = getattr(event, "message", None) or "unknown reason"
                raise HomeAssistantError(f"OpenAI response error: {reason}")

        if not terminal_event_seen:
            raise HomeAssistantError(
                "OpenAI Responses stream ended before a terminal event"
            )

    async def _execute_function_tool(
""",
        "responses EOF",
    ),
]

for old, new, label in replacements:
    if old not in text:
        raise SystemExit(f"{label} marker not found")
    text = text.replace(old, new, 1)

entity_path.write_text(text)

existing_path = Path("tests/test_responses_api.py")
existing = existing_path.read_text()
function_marker = "async def test_responses_annotation_event_after_cited_text_is_observed"
start = existing.index(function_marker)
end = existing.index(
    "    entity = ExtendedOpenAIBaseLLMEntity.__new__",
    start,
)
segment = existing[start:end]
if "_completed_event()" not in segment:
    closing = "        ]\n    )\n"
    if closing not in segment:
        raise SystemExit("annotation test stream closing marker not found")
    segment = segment.replace(
        closing,
        "            _completed_event(),\n        ]\n    )\n",
        1,
    )
    existing = existing[:start] + segment + existing[end:]
    existing_path.write_text(existing)
