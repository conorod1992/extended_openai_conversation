"""Tests for model-facing tool result compaction."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses import (
    model_tool_results,
    runtime_hardening,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)


async def test_tool_result_guard_bounds_outermost_string_result() -> None:
    """A model-facing tool result remains bounded after dispatch."""

    class FakeEntity(ExtendedOpenAIAgentEntity):
        async def _async_dispatch_function_tool(self, *_args):
            return SimpleNamespace(
                tool_result={
                    "result": "x"
                    * (runtime_hardening.MAX_MODEL_TOOL_RESULT_CHARACTERS + 100)
                }
            )

    content = await object.__new__(FakeEntity)._execute_function_tool({}, {}, None, [])
    result = tool_result_data(content)["result"]
    assert len(result) <= runtime_hardening.MAX_MODEL_TOOL_RESULT_CHARACTERS
    assert runtime_hardening._TOOL_RESULT_TRUNCATION_LABEL in result


def test_compact_memory_result_preserves_non_mapping_list_items() -> None:
    """Only memory mappings are sparsified in heterogeneous result lists."""
    result = {
        "memories": [
            {"id": "memory-1", "subject": None, "content": "remember me"},
            "opaque-entry",
        ]
    }

    compacted = model_tool_results._compact_memory_result(result)

    assert compacted == {
        "memories": [
            {"id": "memory-1", "content": "remember me"},
            "opaque-entry",
        ]
    }
    assert result["memories"][0]["subject"] is None


def test_knowledge_search_payload_omits_default_source_filter() -> None:
    """A no-op source-filter envelope is omitted only when no filter was requested."""
    result = {
        "items": [],
        "source_filter": {
            "applied_source_ids": [],
            "ignored_source_ids": [],
            "fell_back_to_all_sources": False,
        },
    }

    compacted = model_tool_results.knowledge_search_payload(
        result,
        filter_requested=False,
        policy_filter_applied=False,
    )

    assert compacted == {"items": []}
    assert "source_filter" in result


async def test_owned_knowledge_get_omits_terminal_cursor() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from custom_components.extended_openai_conversation_responses.guest_mode import (
        GuestCapabilityPolicy,
    )

    agent = object.__new__(ExtendedOpenAIAgentEntity)
    agent.subentry = SimpleNamespace(data={"knowledge_enabled": True})
    agent._effective_guest_policy = GuestCapabilityPolicy.unrestricted
    agent._knowledge = SimpleNamespace(
        source_count=1,
        async_get_section=AsyncMock(
            return_value={
                "content": "document text",
                "has_more": False,
                "next_start_character": None,
            }
        ),
    )
    compacted = await agent._async_execute_knowledge_tool("get", {"source_id": "doc-1"})
    assert compacted == {"content": "document text", "has_more": False}
    agent._knowledge.async_get_section.assert_awaited_once_with("doc-1", 0, 6000)
