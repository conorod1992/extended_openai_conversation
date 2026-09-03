"""Regression tests for lossless model-facing tool-result compaction."""

from __future__ import annotations

import json
from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses.memory import (
    MemoryRecord,
    memory_as_dict,
)
from custom_components.extended_openai_conversation_responses.model_tool_results import (
    _compact_json_result_content,
    _compact_memory_result,
    compact_tool_json,
    knowledge_search_payload,
    model_memory_as_dict,
    omit_null_paging_cursor,
)


def _memory(**overrides) -> MemoryRecord:
    values = {
        "memory_id": "memory-1",
        "user_id": "user-1",
        "content": "Prefers tea",
        "category": "preference",
        "source": "explicit",
        "created_at": "2026-09-03T00:00:00+00:00",
        "updated_at": "2026-09-03T00:00:00+00:00",
        "importance": "normal",
        "subject": None,
        "key": None,
        "valid_from": None,
        "last_confirmed_at": None,
    }
    values.update(overrides)
    return MemoryRecord(**values)


def test_compact_tool_json_preserves_exact_parsed_value() -> None:
    """Whitespace removal must not change any JSON value or Unicode text."""
    value = {
        "status": "ok",
        "message": "Carlow café",
        "items": [{"id": 1, "enabled": True}, None],
    }
    original = json.dumps(value, ensure_ascii=False)
    compacted = compact_tool_json(value)

    assert json.loads(compacted) == json.loads(original) == value
    assert len(compacted) < len(original)
    assert '": "' not in compacted


def test_tool_result_content_compaction_keeps_non_json_unchanged() -> None:
    """Only JSON result strings are rewritten; arbitrary configured-tool text is safe."""
    json_result = SimpleNamespace(
        tool_result={"result": json.dumps({"a": 1, "b": [2, 3]})}
    )
    text_result = SimpleNamespace(tool_result={"result": "plain custom tool output"})

    _compact_json_result_content(json_result)
    _compact_json_result_content(text_result)

    assert json_result.tool_result["result"] == '{"a":1,"b":[2,3]}'
    assert text_result.tool_result["result"] == "plain custom tool output"


def test_model_memory_projection_omits_only_absent_optional_fields() -> None:
    """The admin serializer stays full while model records become sparse."""
    record = _memory()
    admin = memory_as_dict(record)
    model = model_memory_as_dict(record)

    for key in ("subject", "key", "valid_from", "last_confirmed_at"):
        assert key in admin
        assert admin[key] is None
        assert key not in model
    for key in (
        "memory_id",
        "content",
        "category",
        "source",
        "created_at",
        "updated_at",
        "importance",
    ):
        assert model[key] == admin[key]


def test_model_memory_projection_keeps_every_populated_optional_field() -> None:
    """No populated memory metadata may disappear from model-facing results."""
    record = _memory(
        subject="hot drinks",
        key="drink.preference",
        valid_from="2026-09-01T00:00:00+00:00",
        last_confirmed_at="2026-09-03T00:00:00+00:00",
    )

    assert model_memory_as_dict(record) == memory_as_dict(record)


def test_nested_memory_results_only_remove_null_optional_fields() -> None:
    """Statuses, IDs, scope labels, content and populated fields remain unchanged."""
    raw = {
        "status": "updated",
        "memory": {
            "memory_id": "m1",
            "content": "Tea",
            "category": "preference",
            "source": "explicit",
            "importance": "normal",
            "subject": None,
            "key": "drink.preference",
            "valid_from": None,
            "last_confirmed_at": "2026-09-03T00:00:00+00:00",
            "scope_id": "shared:household",
            "scope": "Shared household",
        },
    }

    compacted = _compact_memory_result(raw)

    assert compacted["status"] == "updated"
    assert compacted["memory"]["memory_id"] == "m1"
    assert compacted["memory"]["scope_id"] == "shared:household"
    assert compacted["memory"]["scope"] == "Shared household"
    assert compacted["memory"]["key"] == "drink.preference"
    assert compacted["memory"]["last_confirmed_at"]
    assert "subject" not in compacted["memory"]
    assert "valid_from" not in compacted["memory"]


def test_knowledge_filter_envelope_removed_only_when_no_filter_exists() -> None:
    """Source filtering diagnostics remain whenever caller or policy filtering matters."""
    raw = {
        "results": [{"source_id": "one", "excerpt": "answer"}],
        "source_filter": {
            "applied_source_ids": [],
            "ignored_source_ids": [],
            "fell_back_to_all_sources": False,
        },
    }

    assert knowledge_search_payload(
        raw, filter_requested=False, policy_filter_applied=False
    ) == {"results": raw["results"]}
    assert (
        knowledge_search_payload(
            raw, filter_requested=True, policy_filter_applied=False
        )
        == raw
    )
    assert (
        knowledge_search_payload(
            raw, filter_requested=False, policy_filter_applied=True
        )
        == raw
    )


def test_paging_cursor_is_removed_only_when_backend_says_no_more() -> None:
    """Paging-critical cursors remain whenever another page exists."""
    complete = {
        "content": "all",
        "has_more": False,
        "next_start_character": None,
        "total_characters": 3,
    }
    paged = {
        "content": "part",
        "has_more": True,
        "next_start_character": 4,
        "total_characters": 10,
    }

    assert omit_null_paging_cursor(complete, "next_start_character") == {
        "content": "all",
        "has_more": False,
        "total_characters": 3,
    }
    assert omit_null_paging_cursor(paged, "next_start_character") == paged
