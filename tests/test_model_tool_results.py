"""Tests for model-facing tool result compaction."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import model_tool_results
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)


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


def test_installed_knowledge_get_omits_terminal_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The installed knowledge wrapper compacts the terminal get cursor."""

    async def fake_knowledge(
        _agent: Any, operation: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        assert operation == "get"
        assert arguments == {"record_id": "doc-1"}
        return {
            "content": "document text",
            "has_more": False,
            "next_start_character": None,
        }

    # Register all three attributes with monkeypatch before installation so the
    # module's direct assignments are fully restored at test teardown.
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity,
        "_execute_function_tool",
        ExtendedOpenAIAgentEntity._execute_function_tool,
    )
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity,
        "_async_execute_memory_tool",
        ExtendedOpenAIAgentEntity._async_execute_memory_tool,
    )
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity,
        "_async_execute_knowledge_tool",
        fake_knowledge,
    )
    monkeypatch.setattr(model_tool_results, "_INSTALLED", False)

    model_tool_results.install_model_tool_result_compaction()

    compacted = asyncio.run(
        ExtendedOpenAIAgentEntity._async_execute_knowledge_tool(
            object(), "get", {"record_id": "doc-1"}
        )
    )

    assert compacted == {
        "content": "document text",
        "has_more": False,
    }
