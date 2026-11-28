"""Tests for model-facing tool result compaction."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

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
                agent_id="test", tool_call_id="call", tool_name="test",
                tool_result={
                    "result": "x"
                    * (runtime_hardening.MAX_MODEL_TOOL_RESULT_CHARACTERS + 100)
                }
            )

    content = await object.__new__(FakeEntity)._execute_function_tool({}, {}, None, [])
    assert len(tool_result_data(content)["result"]) == (
        runtime_hardening.MAX_MODEL_TOOL_RESULT_CHARACTERS + 100
    )
    result = model_tool_results.model_tool_result_data(content)["result"]
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


@pytest.mark.parametrize(
    ("oversized_records", "error"),
    [
        (True, "record exceeds"),
        (False, "record metadata exceeds"),
    ],
)
def test_record_pages_reject_unpageable_content(oversized_records, error):
    oversized = "x" * (runtime_hardening.MAX_MODEL_TOOL_RESULT_CHARACTERS + 1)
    source = {
        "items": [oversized] if oversized_records else [],
        "scope": "test" if oversized_records else oversized,
        "offset": 3,
    }
    original_items = source["items"].copy()
    with pytest.raises(ValueError, match=error):
        model_tool_results.bounded_record_page(source, "items")
    assert source["items"] == original_items


@pytest.mark.parametrize("offset", [-1, 5])
def test_text_page_requires_a_valid_character_offset(offset):
    with pytest.raises(ValueError, match="outside the file's character range"):
        model_tool_results.bounded_text_page({"content": "four"}, offset)


def test_text_page_rejects_metadata_that_prevents_cursor_progress():
    oversized = "x" * (runtime_hardening.MAX_MODEL_TOOL_RESULT_CHARACTERS + 1)
    with pytest.raises(ValueError, match="File metadata exceeds"):
        model_tool_results.bounded_text_page(
            {"content": "remaining", "metadata": oversized}, 1
        )


def test_oversized_non_result_envelope_reaches_the_provider_as_a_bounded_notice():
    oversized = "x" * (runtime_hardening.MAX_MODEL_TOOL_RESULT_CHARACTERS + 1)
    content = SimpleNamespace(tool_result={"details": oversized})
    wire = model_tool_results.model_tool_result_data(content)
    assert wire["status"] == "output_overflow"
    assert wire["execution_completed"] is True
    assert "details" not in wire and oversized not in wire["message"]
    assert content.tool_result == {"details": oversized}


@pytest.mark.parametrize("guest", [False, True])
def test_knowledge_search_bounds_both_filter_lists_after_search(guest):
    applied = [f"applied-{i}-" + '"' * 150 for i in range(200)]
    ignored = [f"ignored-{i}-" + "\\" * 150 for i in range(200)]
    original = {
        "results": [{"source_id": applied[-1], "snippet": "Matching evidence"}],
        "source_filter": {
            "applied_source_ids": applied,
            "ignored_source_ids": ignored,
            "fell_back_to_all_sources": False,
        },
    }
    result = model_tool_results.knowledge_search_payload(
        original, filter_requested=True, policy_filter_applied=guest
    )
    metadata = result["source_filter"]
    for name, ids in [("applied", applied), ("ignored", ignored)]:
        assert metadata[f"{name}_source_ids_truncated"] is True
        assert metadata[f"{name}_source_count"] == len(ids)
        retained = metadata[f"{name}_source_ids"]
        assert 0 < len(retained) < len(ids)
        assert retained == ids[: len(retained)]
    assert len(model_tool_results.compact_tool_json(metadata)) < 4300
    assert result["results"] == original["results"]
    assert original["source_filter"]["applied_source_ids"] == applied
