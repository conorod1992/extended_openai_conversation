"""Regression tests for persistent-memory lexical derivation caching."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import math
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    memory as memory_module,
)
from custom_components.extended_openai_conversation_responses.memory import (
    MemoryRecord,
    PersistentMemory,
    _bm25_score as cached_memory_bm25_score,
    _cached_memory_record_terms,
    _cached_memory_record_token_set,
    _cached_memory_term_frequencies,
    _cached_memory_tokens,
    _normalize as cached_memory_normalize,
    _tokens as cached_memory_tokens,
)


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


def test_record_terms_reuse_cached_immutable_tuple() -> None:
    """One immutable record revision reuses its cached token tuple directly."""
    _cached_memory_record_terms.cache_clear()
    record = _RECORDS[0]

    first = _cached_memory_record_terms(record)
    second = _cached_memory_record_terms(record)

    assert "celsiu" in first
    assert second is first
    info = _cached_memory_record_terms.cache_info()
    assert info.misses == 1
    assert info.hits == 1


def test_record_token_set_reuses_cached_immutable_membership() -> None:
    """Search membership tests reuse one frozenset per immutable record revision."""
    _cached_memory_record_terms.cache_clear()
    _cached_memory_record_token_set.cache_clear()
    record = _RECORDS[0]

    first = _cached_memory_record_token_set(record)
    second = _cached_memory_record_token_set(record)

    assert "temperature" in first
    assert second is first
    assert isinstance(first, frozenset)
    info = _cached_memory_record_token_set.cache_info()
    assert info.misses == 1
    assert info.hits == 1


def test_replaced_record_naturally_gets_new_cached_terms() -> None:
    """Memory updates replace the frozen record and therefore use a new cache key."""
    _cached_memory_record_terms.cache_clear()
    original = _RECORDS[0]
    updated = replace(
        original,
        content="User prefers temperatures in Fahrenheit.",
        updated_at="2026-08-02T10:00:00+00:00",
    )

    original_terms = _cached_memory_record_terms(original)
    updated_terms = _cached_memory_record_terms(updated)

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

    assert second == {"preference", "temperature", "unit"}
    assert "mutated" not in second
    assert cached_memory_normalize(value) == "preferences temperature units"
    info = _cached_memory_tokens.cache_info()
    assert info.misses == 1
    assert info.hits == 1


def test_bm25_frequency_cache_preserves_exact_score() -> None:
    """BM25 math is unchanged while repeated document frequencies are reused."""
    _cached_memory_term_frequencies.cache_clear()
    query_terms = ["temperature", "unit"]
    document_terms = ("temperature", "temperature", "unit", "celsiu")
    document_frequency = {"temperature": 2, "unit": 1}

    # Independent reference calculation for this fixed document.
    idfs = [math.log(1 + (3 - df + 0.5) / (df + 0.5)) for df in (2, 1)]
    expected = sum(
        idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * 4 / 4.5))
        for idf, tf in zip(idfs, (2, 1), strict=True)
    ) / sum(idf * 2.2 / 1.3 for idf in idfs)
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
async def test_search_results_are_unchanged_after_cache_warmup(
    scope: str | tuple[str, ...],
    query: str,
    category: str | None,
) -> None:
    """The authoritative search returns identical results with cached pure helpers."""
    original_manager = await _manager()
    cached_manager = await _manager()
    memory_module._cached_memory_record_terms.cache_clear()
    memory_module._cached_memory_record_token_set.cache_clear()
    memory_module._cached_memory_tokens.cache_clear()
    memory_module._cached_memory_term_frequencies.cache_clear()
    expected = await original_manager.async_search(
        scope, query, category=category, limit=5
    )

    actual = await cached_manager.async_search(scope, query, category=category, limit=5)

    assert _ids(actual) == _ids(expected)
