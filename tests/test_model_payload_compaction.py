"""Regression tests for lossless static model-payload compaction."""

from __future__ import annotations

import json
from typing import Any

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    KNOWLEDGE_PROMPT,
    MEMORY_PROMPT,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    archive_tools,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    build_loader_tool,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    guest_mode_restrict_tool,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    knowledge_tools,
)
from custom_components.extended_openai_conversation_responses.memory import memory_tools
from custom_components.extended_openai_conversation_responses.model_payload import (
    KNOWLEDGE_GUIDANCE,
    PERSISTENT_MEMORY_GUIDANCE,
    prepare_model_function_tools,
)
from custom_components.extended_openai_conversation_responses.request import (
    CONTINUE_CONVERSATION_TOOL,
    format_function_tools,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    temporary_memory_tools,
)


def _without_descriptions(value: Any) -> Any:
    """Remove prose while retaining every machine-enforced schema constraint."""
    if isinstance(value, dict):
        return {
            key: _without_descriptions(child)
            for key, child in value.items()
            if key != "description"
        }
    if isinstance(value, list):
        return [_without_descriptions(child) for child in value]
    return value


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def test_integration_tool_compaction_preserves_schema_constraints() -> None:
    """Compaction changes prose, not executable metadata or validation structure."""
    original = [
        *memory_tools(),
        *temporary_memory_tools(),
        *knowledge_tools(),
        *archive_tools(),
        guest_mode_restrict_tool(),
        CONTINUE_CONVERSATION_TOOL,
    ]
    compacted = prepare_model_function_tools(original)
    original_by_name = {tool["spec"]["name"]: tool for tool in original}
    compacted_by_name = {tool["spec"]["name"]: tool for tool in compacted}

    assert "memory_add" in original_by_name
    assert "memory_add" not in compacted_by_name
    assert "memory_upsert" in compacted_by_name
    assert set(compacted_by_name) == set(original_by_name) - {"memory_add"}

    for name, tool in compacted_by_name.items():
        original_tool = original_by_name[name]
        assert tool.get("function") == original_tool.get("function")
        assert _without_descriptions(tool["spec"]) == _without_descriptions(
            original_tool["spec"]
        )

    assert len(_compact_json(compacted)) < len(_compact_json(original)) * 0.9


def test_generic_user_tool_is_not_rewritten() -> None:
    """User-authored/configured tools are outside the compaction policy."""
    tool = {
        "spec": {
            "name": "my_custom_tool",
            "description": "Every word here belongs to the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {
                        "type": "string",
                        "description": "Keep this custom explanation exactly.",
                    }
                },
            },
        },
        "function": {"type": "native", "name": "get_user_from_user_id"},
    }

    assert prepare_model_function_tools([tool]) == [tool]


def test_function_group_loader_keeps_full_catalogue_with_less_boilerplate() -> None:
    """Every group remains described while the repeated loader prose is shorter."""
    loader = build_loader_tool(
        [
            {
                "id": "travel",
                "name": "Travel",
                "description": "Routes, destinations, and travel-time helpers.",
            },
            {
                "id": "media",
                "name": "Media",
                "description": "TV and local media helpers.",
            },
        ]
    )
    compacted = prepare_model_function_tools([loader])[0]
    description = compacted["spec"]["description"]

    assert (
        "travel: Travel — Routes, destinations, and travel-time helpers."
        in description
    )
    assert "media: Media — TV and local media helpers." in description
    assert "performs no action" in description
    assert len(description) < len(loader["spec"]["description"])


def test_provider_formatter_hides_legacy_memory_add() -> None:
    """The backend-compatible add operation is absent from provider tool schemas."""
    formatted = format_function_tools(memory_tools(), API_MODE_RESPONSES)
    names = {tool["name"] for tool in formatted}

    assert "memory_add" not in names
    assert "memory_upsert" in names


def test_compact_guidance_retains_memory_and_knowledge_safety_semantics() -> None:
    """The largest fixed guidance blocks keep their behavioral requirements."""
    assert len(PERSISTENT_MEMORY_GUIDANCE) < len(MEMORY_PROMPT) * 0.7
    assert len(KNOWLEDGE_GUIDANCE) < len(KNOWLEDGE_PROMPT) * 0.7

    for phrase in (
        "memory_upsert",
        "source=implicit",
        "household scope",
        "secrets or credentials",
        "Current user statements override stored facts",
        "confirm before broad deletion",
    ):
        assert phrase in PERSISTENT_MEMORY_GUIDANCE

    for phrase in (
        "search rather than guess",
        "short discriminative keywords",
        "never invent IDs",
        "knowledge_list without a query",
        "knowledge_get",
        "rather than inventing an answer",
    ):
        assert phrase in KNOWLEDGE_GUIDANCE
