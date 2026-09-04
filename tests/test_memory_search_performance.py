"""Regression tests for persistent-memory lexical derivation caching."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import memory as memory_module
from custom_components.extended_openai_conversation_responses.memory import (
    MemoryRecord,
    PersistentMemory,
)
from custom_components.extended_openai_conversation_responses.performance import (
    _cached_memory_record_terms,
    _cached_memory_term_frequencies,
    _cached_memory_tokens,
    cached_memory_bm25_score,
    cached_memory_normalize,
    cached_memory_record_token_list,
    cached_memory_tokens,
)

_ORIGINAL_SEARCH = PersistentMemory.async_search
_ORIGINAL_RECORD_TOKEN_LIST = memory_module._record_token_list
_ORIGINAL_TOKENS = memory_module._tokens
_ORIGINAL_NORMALIZE = memory_module._normalize
_ORIGINAL_BM25 = memory_module._bm25_score


class _Storage:
    """Minimal detached memory storage double."""

    def __init__(self, records: list[MemoryRecord]) -> None:
        self.data = {"memories": [asdict(record) for record in records]}

    async def async_load(self) -> dict[str, Any]:
        return deepcopy(self.data)

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = deepcopy(data)


def _record(
    memory_id: str,
    user_id: str,
    content: str,
    category: str,
    *,
    importance: str = "normal",
    subject: str | None = None,
    key: str | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        content=content,
        category=category,
        source="explicit",
        created_at="2026-08-01T10:00:00+00:00",
        updated_at="2026-08-01T10:00:00+00:00",
        importance=importance,
        subject=subject,
        key=key,
        last_confirmed_at="2026-08-01T10:00:00+00:00",
    )


_RECORDS = [
    _record(
        "celsius",
        "alice",
        "User prefers temperatures in Celsius.",
        "preferences",
        subject="temperature units",
        key="preferences.temperature.units",
    ),
    _record(
        "oscar",
        "alice",
        "Oscar is the user's dog.",
        "pets",
        importance="high",
        subject="Oscar",
        key="pets.oscar",
    ),
    _record(
        "bins",
        "shared:household",
        "Household bins go out every Friday evening.",
        "home",
        subject="bins",
        key="home.bins.collection",
    ),
]


async def _manager() -> PersistentMemory:
    manager = PersistentMemory(_Storage(_RECORDS))
    await manager.async_initialize()
    return manager


def _ids(records: list[MemoryRecord]) -> list[str]:
    return [record.memory_id for record in records]


def test_record_terms_are_cached_without_sharing_mutable_lists() -> None:
    """One immutable record revision is tokenized once while callers get fresh lists."""
    _cached_memory_record_terms.cache_clear()
    record = _RECORDS[0]

    first = cached_memory_record_token_list(record)
    second = cached_memory_record_token_list(record)

    assert first == _ORIGINAL_RECORD_TOKEN_LIST(record)
    assert second == first
    assert second is not first
    first.append("mutated")
    assert "mutated" not in cached_memory_record_token_list(record)
    info = _cached_memory_record_terms.cache_info()
    assert info.misses == 1
    assert info.hits == 2


def test_replaced_record_naturally_gets_new_cached_terms() -> None:
    """Memory updates replace the frozen record and therefore use a new cache key."""
    _cached_memory_record_terms.cache_clear()
    original = _RECORDS[0]
    updated = replace(
        original,
        content="User prefers temperatures in Fahrenheit.",
        updated_at="2026-08-02T10:00:00+00:00",
    )

    original_terms = cached_memory_record_token_list(original)
    updated_terms = cached_memory_record_token_list(updated)

    assert "celsiu" in original_terms
    assert "fahrenheit" in updated_terms
    assert "celsiu" not in updated_terms
    assert _cached_memory_record_terms.cache_info().misses == 2


def test_string_lexical_caches_preserve_fresh_mutable_sets() -> None:
    """Metadata token/normalization caches retain the original value semantics."""
    _cached_memory_tokens.cache_clear()
    value = "Preferences temperature units"

    first = cached_memory_tokens(value)
    first.add("mutated")
    second = cached_memory_tokens(value)

    assert second == _ORIGINAL_TOKENS(value)
    assert "mutated" not in second
    assert cached_memory_normalize(value) == _ORIGINAL_NORMALIZE(value)
    info = _cached_memory_tokens.cache_info()
    assert info.misses == 1
    assert info.hits == 1


def test_bm25_frequency_cache_preserves_exact_score() -> None:
    """BM25 math is unchanged while repeated document frequencies are reused."""
    _cached_memory_term_frequencies.cache_clear()
    query_terms = ["temperature", "unit"]
    document_terms = ["temperature", "temperature", "unit", "celsiu"]
    document_frequency = {"temperature": 2, "unit": 1}

    expected = _ORIGINAL_BM25(
        query_terms, document_terms, document_frequency, 3, 4.5
    )
    first = cached_memory_bm25_score(
        query_terms, document_terms, document_frequency, 3, 4.5
    )
    second = cached_memory_bm25_score(
        query_terms, document_terms, document_frequency, 3, 4.5
    )

    assert first == expected
    assert second == expected
    info = _cached_memory_term_frequencies.cache_info()
    assert info.misses == 1
    assert info.hits == 1


@pytest.mark.parametrize(
    ("scope", "query", "category"),
    [
        ("alice", "Celsius", None),
        ("alice", "What temperature units do I normally use?", None),
        ("alice", "Oscra dog", None),
        ("alice", "Oscar", "pets"),
        (("alice", "shared:household"), "Friday bins", None),
        ("alice", "Friday bins", None),
        ("alice", "completely unrelated phrase", None),
    ],
)
async def test_existing_search_algorithm_is_unchanged_with_cached_helpers(
    monkeypatch: pytest.MonkeyPatch,
    scope: str | tuple[str, ...],
    query: str,
    category: str | None,
) -> None:
    """The authoritative search returns identical results with cached pure helpers."""
    original_manager = await _manager()
    cached_manager = await _manager()
    expected = await _ORIGINAL_SEARCH(
        original_manager, scope, query, category=category, limit=5
    )

    monkeypatch.setattr(
        memory_module, "_record_token_list", cached_memory_record_token_list
    )
    monkeypatch.setattr(memory_module, "_tokens", cached_memory_tokens)
    monkeypatch.setattr(memory_module, "_normalize", cached_memory_normalize)
    monkeypatch.setattr(memory_module, "_bm25_score", cached_memory_bm25_score)
    actual = await _ORIGINAL_SEARCH(
        cached_manager, scope, query, category=category, limit=5
    )

    assert _ids(actual) == _ids(expected)
