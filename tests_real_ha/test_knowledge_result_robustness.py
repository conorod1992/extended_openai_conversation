"""Knowledge result robustness through Home Assistant and the real provider wire."""

from __future__ import annotations

import json
from typing import Any

from custom_components.extended_openai_conversation_responses.knowledge import (
    MAX_EXCERPT_CHARACTERS,
)
from homeassistant.core import HomeAssistant
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _chat_tool_result,
    _knowledge_agent,
    _say,
)
from tests_real_ha.test_provider_wire_e2e import _install_wire, _speech
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
)

_INTERIOR_MARKER = "interior-nebula-cobalt"
_PREFIX_MARKER = "prefix-saffron-marker"
_SUFFIX_MARKER = "suffix-emerald-marker"
_SEARCH_CALL = "call-knowledge-interior-search"
_INVALID_CALL = "call-knowledge-invalid-get"
_INVALID_SOURCE_ID = "deleted-source-does-not-exist"


def _tool_message_content(body: dict[str, Any], call_id: str) -> str:
    """Return the exact serialized Chat Completions tool-result payload."""
    return next(
        item["content"]
        for item in body["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == call_id
    )


async def test_long_source_search_returns_bounded_relevant_interior_excerpt(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """Search must send a bounded interior chunk, never the whole Knowledge source."""
    agent = await _knowledge_agent(hass, API_MODE_CHAT_COMPLETIONS)
    prefix = f"{_PREFIX_MARKER} " + ("irrelevant prefix material " * 110)
    relevant = (
        "The telescope alignment exception uses the unique reference token "
        f"{_INTERIOR_MARKER}. This is the only relevant procedure for the query. "
    )
    suffix = ("irrelevant suffix material " * 110) + f" {_SUFFIX_MARKER}"
    source = await agent._knowledge.async_create(
        "Long observatory operations handbook",
        "Large handbook containing many unrelated operational procedures",
        prefix + relevant + suffix,
    )
    assert len(source.content) > MAX_EXCERPT_CHARACTERS * 2

    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                _SEARCH_CALL,
                "knowledge_search",
                {"query": "telescope alignment exception reference token", "limit": 5},
            ),
            _chat_sse_text("The interior reference was found."),
        ],
    )

    result = await _say(hass, agent, "Find the telescope alignment exception reference token")

    assert _speech(result) == "The interior reference was found."
    assert len(wire.requests) == 2
    search_result = _chat_tool_result(wire.requests[1]["body"], _SEARCH_CALL)
    assert len(search_result["results"]) == 1
    item = search_result["results"][0]
    assert item["source_id"] == source.source_id
    excerpt = item["excerpt"]
    assert _INTERIOR_MARKER in excerpt
    assert len(excerpt) <= MAX_EXCERPT_CHARACTERS
    assert len(excerpt) < len(source.content)
    assert _PREFIX_MARKER not in excerpt
    assert _SUFFIX_MARKER not in excerpt


async def test_invalid_knowledge_get_returns_bounded_tool_error_and_conversation_continues(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """An invalid source ID must stay inside the tool loop and allow final recovery."""
    agent = await _knowledge_agent(hass, API_MODE_CHAT_COMPLETIONS)
    # Keep Knowledge provider tools exposed while the model asks for an invalid ID.
    await agent._knowledge.async_create(
        "Valid source",
        "Keeps the Knowledge capability available",
        "A small valid source that is unrelated to the deliberately invalid lookup.",
    )

    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                _INVALID_CALL,
                "knowledge_get",
                {"source_id": _INVALID_SOURCE_ID},
            ),
            _chat_sse_text("That Knowledge source is no longer available."),
        ],
    )

    result = await _say(hass, agent, "Read the requested Knowledge source")

    assert _speech(result) == "That Knowledge source is no longer available."
    assert len(wire.requests) == 2
    serialized = _tool_message_content(wire.requests[1]["body"], _INVALID_CALL)
    assert len(serialized) < 2_000
    envelope = json.loads(serialized)
    assert isinstance(envelope, dict)
    # Tool failures are provider-visible structured payloads rather than escaping
    # the function loop and aborting the Home Assistant conversation.
    assert any(key in envelope for key in ("error", "result"))
    assert "knowledge_get" not in serialized or len(serialized) < 2_000
