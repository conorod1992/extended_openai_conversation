"""Local persistent memory for conversation agents."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import asdict, dataclass
import hashlib
import json
import logging
import math
import re
from typing import Any, Protocol
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_MEMORY_AUTO_CREATE,
    CONF_MEMORY_ENABLED,
    CONF_MEMORY_MODE,
    DEFAULT_MEMORY_AUTO_CREATE,
    DEFAULT_MEMORY_ENABLED,
    DOMAIN,
    MEMORY_MODE_AUTOMATIC,
    MEMORY_MODE_MANUAL,
    MEMORY_MODE_OFF,
    MEMORY_MODES,
)
from .scope import LEGACY_ANONYMOUS_SCOPE_ID

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 2
STORAGE_KEY_PREFIX = f"{DOMAIN}.memory"
EMBEDDING_CACHE_VERSION = 1
EMBEDDING_CACHE_BATCH_SIZE = 64
ANONYMOUS_USER_ID = LEGACY_ANONYMOUS_SCOPE_ID
MAX_MEMORIES_PER_AGENT = 10_000
MAX_CONTENT_LENGTH = 1_000
MAX_CATEGORY_LENGTH = 64
MAX_SUBJECT_LENGTH = 128
MAX_KEY_LENGTH = 160
MAX_SEARCH_LIMIT = 50
MAX_LIST_LIMIT = 100
MEMORY_TOOL_NAMES = {
    "memory_add",
    "memory_upsert",
    "memory_search",
    "memory_list",
    "memory_update",
    "memory_delete",
}
MEMORY_IMPORTANCES = {"low", "normal", "high"}
IMPORTANCE_MULTIPLIERS = {"low": 0.85, "normal": 1.0, "high": 1.2}
MIN_LEXICAL_RELEVANCE_SCORE = 0.08
MIN_SEMANTIC_SIMILARITY = 0.55
EmbeddingProvider = Callable[[list[str]], Awaitable[list[list[float]]]]
EmbeddingTaskScheduler = Callable[[Coroutine[Any, Any, None]], asyncio.Future[Any]]


class _UnsetType:
    """Marker for an omitted optional tool argument."""


_UNSET = _UnsetType()

_TOKEN_PATTERN = re.compile(r"[\w'-]+", re.UNICODE)
_SPACE_PATTERN = re.compile(r"\s+")
_SECRET_PATTERN = re.compile(
    r"(?:password|passcode|api[_ -]?key|access[_ -]?token|auth[_ -]?token|"
    r"security[_ -]?code|secret|pin)\s*(?:is|:|=)\s*\S+|"
    r"\bsk-[A-Za-z0-9_-]{12,}\b",
    re.IGNORECASE,
)
_PAYMENT_CARD_CANDIDATE_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_PAYMENT_CARD_CONTEXT_PATTERN = re.compile(
    r"\b(?:card(?:\s+number)?|credit\s+card|debit\s+card|visa|mastercard|amex)\b",
    re.IGNORECASE,
)
_FINANCIAL_CREDENTIAL_PATTERN = re.compile(
    r"\b(?:cvv2?|cvc2?|card\s+security\s+code)\s*(?:is\s*)?(?::|=)?\s*\d{3,4}\b|"
    r"\b(?:bank\s+)?account\s+(?:number|no\.?)\s*(?:is\s*)?(?::|=)?\s*"
    r"(?:\d[ -]?){5,19}\d\b|"
    r"\b(?:routing\s+number|sort\s+code)\s*(?:is\s*)?(?::|=)?\s*"
    r"(?:\d[ -]?){5,8}\d\b",
    re.IGNORECASE,
)
_IBAN_CANDIDATE_PATTERN = re.compile(
    r"\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]){11,30}\b", re.IGNORECASE
)
_SENSITIVE_IMPLICIT_PATTERN = re.compile(
    r"\b(?:medical diagnosis|health condition|religion|religious belief|"
    r"political affiliation|sexual orientation)\b",
    re.IGNORECASE,
)
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "for",
    "from",
    "i",
    "in",
    "is",
    "it",
    "my",
    "of",
    "on",
    "that",
    "the",
    "to",
    "user",
    "with",
    "what",
    "do",
    "does",
    "did",
    "normally",
    "usually",
    "use",
    "using",
    "how",
}


@dataclass(slots=True, frozen=True)
class MemoryRecord:
    """A concise durable fact."""

    memory_id: str
    user_id: str
    content: str
    category: str
    source: str
    created_at: str
    updated_at: str
    importance: str = "normal"
    subject: str | None = None
    key: str | None = None
    valid_from: str | None = None
    last_confirmed_at: str | None = None


@dataclass(slots=True, frozen=True)
class EmbeddingCacheEntry:
    """Regenerable embedding data kept outside the durable memory store."""

    model: str
    fingerprint: str
    vector: list[float]


class MemoryStorage(Protocol):
    """Persistence boundary for a future alternative memory backend."""

    async def async_load(self) -> Mapping[str, Any] | Sequence[Any] | None:
        """Load stored memory data."""

    async def async_save(self, data: dict[str, Any]) -> None:
        """Persist memory data."""


class EmbeddingCacheStorage(Protocol):
    """Persistence boundary for regenerable embedding cache data."""

    async def async_load(self) -> Mapping[str, Any] | None:
        """Load cached embeddings."""

    async def async_save(self, data: dict[str, Any]) -> None:
        """Persist cached embeddings."""


class MemoryStore(Store[dict[str, Any]]):
    """Versioned Home Assistant Store backend."""

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: Any
    ) -> dict[str, Any]:
        """Migrate older memory payloads."""
        if old_major_version == 0:
            if isinstance(old_data, list):
                old_data = {"memories": old_data}
            if isinstance(old_data, dict):
                old_data = {"memories": old_data.get("memories", [])}
        if old_major_version in {0, 1} and isinstance(old_data, dict):
            return {
                "memories": [
                    _migrate_raw_record(raw)
                    for raw in old_data.get("memories", [])
                    if isinstance(raw, Mapping)
                ]
            }
        raise NotImplementedError


class HomeAssistantMemoryStorage:
    """Store adapter using Home Assistant's supported .storage API."""

    def __init__(self, hass: HomeAssistant, entry_id: str, subentry_id: str) -> None:
        """Initialize a private per-agent store."""
        key = f"{STORAGE_KEY_PREFIX}.{entry_id}.{subentry_id}"
        self._store = MemoryStore(
            hass,
            STORAGE_VERSION,
            key,
            private=True,
            atomic_writes=True,
            serialize_in_event_loop=False,
        )

    async def async_load(self) -> dict[str, Any] | None:
        """Load data."""
        return await self._store.async_load()

    async def async_save(self, data: dict[str, Any]) -> None:
        """Save data."""
        await self._store.async_save(data)


class HomeAssistantEmbeddingCacheStorage:
    """Separate private Store for regenerable embedding vectors."""

    def __init__(self, hass: HomeAssistant, entry_id: str, subentry_id: str) -> None:
        """Initialize a private per-agent embedding cache."""
        key = f"{STORAGE_KEY_PREFIX}.{entry_id}.{subentry_id}.embeddings"
        self._store = Store[dict[str, Any]](
            hass,
            EMBEDDING_CACHE_VERSION,
            key,
            private=True,
            atomic_writes=True,
            serialize_in_event_loop=False,
        )

    async def async_load(self) -> dict[str, Any] | None:
        """Load cache data."""
        return await self._store.async_load()

    async def async_save(self, data: dict[str, Any]) -> None:
        """Save cache data."""
        await self._store.async_save(data)


class PersistentMemory:
    """Concurrency-safe memory collection with bounded indexed search."""

    def __init__(
        self,
        storage: MemoryStorage,
        embedding_cache_storage: EmbeddingCacheStorage | None = None,
        embedding_task_scheduler: EmbeddingTaskScheduler | None = None,
    ) -> None:
        """Initialize memory collection."""
        self._storage = storage
        self._memories: dict[str, MemoryRecord] = {}
        self._token_index: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._key_index: dict[tuple[str, str], str] = {}
        self._embedding_provider: EmbeddingProvider | None = None
        self._embedding_model = "default"
        self._embedding_cache_storage = embedding_cache_storage
        self._embedding_cache: dict[str, EmbeddingCacheEntry] = {}
        self._embedding_cache_dirty = False
        self._embedding_task_scheduler = embedding_task_scheduler
        self._embedding_maintenance_task: asyncio.Future[Any] | None = None
        self._embedding_maintenance_requested = False
        self._lock = asyncio.Lock()
        self._initialized = False

    async def async_initialize(self) -> None:
        """Load memory data once."""
        async with self._lock:
            if self._initialized:
                return
            data = await self._storage.async_load()
            raw_memories = data.get("memories", []) if isinstance(data, Mapping) else []
            legacy_embeddings_found = False
            for raw in raw_memories:
                try:
                    legacy_embeddings_found |= (
                        isinstance(raw, Mapping) and "embedding" in raw
                    )
                    memory = MemoryRecord(**_migrate_raw_record(raw))
                except TypeError, ValueError:
                    _LOGGER.warning("Ignoring malformed persistent memory record")
                    continue
                self._memories[memory.memory_id] = memory
                self._index(memory)
            if legacy_embeddings_found:
                await self._storage.async_save(
                    {
                        "memories": [
                            _record_as_storage_dict(memory)
                            for memory in self._memories.values()
                        ]
                    }
                )
            await self._async_load_embedding_cache_locked()
            self._initialized = True

    def set_embedding_provider(
        self, provider: EmbeddingProvider | None, model: str = "default"
    ) -> None:
        """Configure the optional provider used only by hybrid retrieval."""
        self._embedding_provider = provider
        self._embedding_model = model
        if provider is not None:
            self._schedule_embedding_maintenance()

    async def async_wait_for_embedding_maintenance(self) -> None:
        """Wait for currently scheduled regenerable embedding maintenance."""
        task = self._embedding_maintenance_task
        if task is not None:
            await asyncio.shield(task)

    async def async_add(
        self,
        user_id: str,
        content: str,
        category: str,
        source: str,
        importance: str = "normal",
        subject: str | None = None,
        key: str | None = None,
        valid_from: str | None = None,
    ) -> dict[str, Any]:
        """Add a memory, or return a likely duplicate."""
        content = _clean_content(content)
        category = _clean_category(category)
        importance = _clean_importance(importance)
        subject = _clean_optional(subject, "subject", MAX_SUBJECT_LENGTH)
        key = _clean_key(key)
        valid_from = _clean_timestamp(valid_from, "valid_from")
        if source not in {"explicit", "implicit"}:
            raise ValueError("source must be explicit or implicit")
        _validate_privacy(content, source)
        async with self._lock:
            self._ensure_initialized()
            duplicate = self._find_duplicate(user_id, content)
            if duplicate:
                return {"status": "duplicate", "memory": memory_as_dict(duplicate)}
            if key and (user_id, key) in self._key_index:
                raise ValueError("canonical key already exists in this memory scope")
            if len(self._memories) >= MAX_MEMORIES_PER_AGENT:
                raise ValueError(
                    "memory limit reached; delete memories before adding more"
                )

            timestamp = dt_util.utcnow().isoformat()
            memory = MemoryRecord(
                memory_id=uuid4().hex,
                user_id=user_id,
                content=content,
                category=category,
                source=source,
                created_at=timestamp,
                updated_at=timestamp,
                importance=importance,
                subject=subject,
                key=key,
                valid_from=valid_from,
                last_confirmed_at=timestamp,
            )
            self._memories[memory.memory_id] = memory
            self._index(memory)
            await self._async_save_locked()
            self._schedule_embedding_maintenance()
            return {"status": "created", "memory": memory_as_dict(memory)}

    async def async_upsert(
        self,
        user_id: str,
        content: str,
        category: str,
        source: str,
        importance: str | _UnsetType = _UNSET,
        subject: str | _UnsetType | None = _UNSET,
        key: str | _UnsetType | None = _UNSET,
        valid_from: str | _UnsetType | None = _UNSET,
    ) -> dict[str, Any]:
        """Create, confirm, update by canonical key, or surface a likely conflict."""
        content = _clean_content(content)
        category = _clean_category(category)
        cleaned_importance = (
            _UNSET
            if isinstance(importance, _UnsetType)
            else _clean_importance(importance)
        )
        cleaned_subject = (
            _UNSET
            if isinstance(subject, _UnsetType)
            else _clean_optional(subject, "subject", MAX_SUBJECT_LENGTH)
        )
        cleaned_key = _UNSET if isinstance(key, _UnsetType) else _clean_key(key)
        cleaned_valid_from = (
            _UNSET
            if isinstance(valid_from, _UnsetType)
            else _clean_timestamp(valid_from, "valid_from")
        )
        if source not in {"explicit", "implicit"}:
            raise ValueError("source must be explicit or implicit")
        _validate_privacy(content, source)
        async with self._lock:
            self._ensure_initialized()
            timestamp = dt_util.utcnow().isoformat()
            if isinstance(cleaned_key, str) and (
                memory_id := self._key_index.get((user_id, cleaned_key))
            ):
                current = self._memories[memory_id]
                changes: dict[str, Any] = {
                    "content": content,
                    "category": category,
                    "key": cleaned_key,
                    "updated_at": timestamp,
                    "last_confirmed_at": timestamp,
                }
                if cleaned_importance is not _UNSET:
                    changes["importance"] = cleaned_importance
                if cleaned_subject is not _UNSET:
                    changes["subject"] = cleaned_subject
                if cleaned_valid_from is not _UNSET:
                    changes["valid_from"] = cleaned_valid_from
                updated = self._replace_record(
                    current,
                    **changes,
                )
                await self._async_save_locked()
                self._schedule_embedding_maintenance()
                return {"status": "updated", "memory": memory_as_dict(updated)}
            duplicate = self._find_duplicate(user_id, content)
            if duplicate:
                changes = {
                    "category": category,
                    "last_confirmed_at": timestamp,
                }
                if cleaned_importance is not _UNSET:
                    changes["importance"] = cleaned_importance
                if cleaned_subject is not _UNSET:
                    changes["subject"] = cleaned_subject
                if cleaned_key is not _UNSET:
                    changes["key"] = cleaned_key
                if cleaned_valid_from is not _UNSET:
                    changes["valid_from"] = cleaned_valid_from
                confirmed = self._replace_record(
                    duplicate,
                    **changes,
                )
                await self._async_save_locked()
                self._schedule_embedding_maintenance()
                return {"status": "confirmed", "memory": memory_as_dict(confirmed)}
            candidate = self._find_related_candidate(
                user_id,
                content,
                cleaned_subject if isinstance(cleaned_subject, str) else None,
                cleaned_key if isinstance(cleaned_key, str) else None,
            )
            if candidate is not None:
                return {
                    "status": "needs_resolution",
                    "candidate": memory_as_dict(candidate),
                }
            if len(self._memories) >= MAX_MEMORIES_PER_AGENT:
                raise ValueError(
                    "memory limit reached; delete memories before adding more"
                )
            memory = MemoryRecord(
                memory_id=uuid4().hex,
                user_id=user_id,
                content=content,
                category=category,
                source=source,
                created_at=timestamp,
                updated_at=timestamp,
                importance=(
                    cleaned_importance
                    if isinstance(cleaned_importance, str)
                    else "normal"
                ),
                subject=(cleaned_subject if isinstance(cleaned_subject, str) else None),
                key=cleaned_key if isinstance(cleaned_key, str) else None,
                valid_from=(
                    cleaned_valid_from if isinstance(cleaned_valid_from, str) else None
                ),
                last_confirmed_at=timestamp,
            )
            self._memories[memory.memory_id] = memory
            self._index(memory)
            await self._async_save_locked()
            self._schedule_embedding_maintenance()
            return {"status": "created", "memory": memory_as_dict(memory)}

    async def async_search(
        self,
        user_id: str | Sequence[str],
        query: str,
        category: str | None = None,
        limit: int = 5,
        *,
        query_embedding: list[float] | None = None,
        hybrid: bool = False,
    ) -> list[MemoryRecord]:
        """Return deterministic BM25-style lexical or hybrid results."""
        self._ensure_initialized()
        limit = max(1, min(limit, MAX_SEARCH_LIMIT))
        scope_ids = (
            (user_id,) if isinstance(user_id, str) else tuple(dict.fromkeys(user_id))
        )
        query_terms = _token_list(query)
        query_tokens = set(query_terms)
        if not query_tokens:
            return []
        category_filter = _clean_category(category) if category else None
        corpus = [
            memory
            for memory in self._memories.values()
            if memory.user_id in scope_ids
            and (category_filter is None or memory.category == category_filter)
        ]
        if not corpus:
            return []
        document_terms = {
            memory.memory_id: _record_token_list(memory) for memory in corpus
        }
        document_frequency = {
            token: sum(token in set(terms) for terms in document_terms.values())
            for token in query_tokens
        }
        average_length = max(1.0, sum(map(len, document_terms.values())) / len(corpus))
        normalized_query = _normalize(query)
        ranked: list[tuple[float, float, str, MemoryRecord]] = []
        for memory in corpus:
            terms = document_terms[memory.memory_id]
            lexical = _bm25_score(
                query_terms,
                terms,
                document_frequency,
                len(corpus),
                average_length,
            )
            lexical += _metadata_bonus(query_tokens, memory)
            normalized_content = _normalize(memory.content)
            if normalized_query and normalized_query in normalized_content:
                lexical += 0.35
            elif len(query_terms) > 1 and " ".join(query_terms) in " ".join(terms):
                lexical += 0.18
            if lexical <= 0:
                lexical = _fuzzy_relevance(query_tokens, set(terms))
            semantic = (
                _cosine_similarity(query_embedding, self._cached_embedding(memory))
                if hybrid
                else None
            )
            relevance = lexical
            if semantic is not None:
                semantic = max(0.0, semantic)
                relevance = 0.58 * lexical + 0.42 * semantic
            if not (
                lexical >= MIN_LEXICAL_RELEVANCE_SCORE
                or (semantic is not None and semantic >= MIN_SEMANTIC_SIMILARITY)
            ):
                continue
            final_score = relevance * IMPORTANCE_MULTIPLIERS[memory.importance]
            freshness = _freshness_tiebreak(memory)
            ranked.append((final_score, freshness, memory.memory_id, memory))
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return [memory for _, _, _, memory in ranked[:limit]]

    async def async_prepare_hybrid(
        self, scope_ids: Sequence[str], query: str
    ) -> list[float] | None:
        """Refresh missing embeddings and return one query embedding, or fall back."""
        if self._embedding_provider is None:
            return None
        try:
            if not await self._async_refresh_missing_embeddings(scope_ids):
                return None
            provider = self._embedding_provider
            if provider is None:
                return None
            vectors = await provider([query])
            return _clean_embedding(vectors[0]) if len(vectors) == 1 else None
        except Exception:
            _LOGGER.warning(
                "Hybrid memory embeddings unavailable; using lexical retrieval",
                exc_info=True,
            )
            return None

    async def async_get_many(
        self, references: Sequence[tuple[str, str]], readable_scope_ids: Sequence[str]
    ) -> list[MemoryRecord]:
        """Resolve a selected bundle by owner and ID without reranking."""
        self._ensure_initialized()
        allowed = set(readable_scope_ids)
        return [
            record
            for scope_id, memory_id in references
            if scope_id in allowed
            and (record := self._memories.get(memory_id)) is not None
            and record.user_id == scope_id
        ]

    async def async_list(
        self,
        user_id: str | Sequence[str],
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        """List memories for one user scope."""
        self._ensure_initialized()
        limit = max(1, min(limit, MAX_LIST_LIMIT))
        offset = max(0, offset)
        category_filter = _clean_category(category) if category else None
        scope_ids = {user_id} if isinstance(user_id, str) else set(user_id)
        memories = [
            memory
            for memory in self._memories.values()
            if memory.user_id in scope_ids
            and (category_filter is None or memory.category == category_filter)
        ]
        memories.sort(key=lambda memory: memory.updated_at, reverse=True)
        return memories[offset : offset + limit]

    async def async_update(
        self,
        user_id: str,
        memory_id: str,
        content: str | None = None,
        category: str | None = None,
        importance: str | None = None,
        subject: str | None = None,
        key: str | None = None,
        valid_from: str | None = None,
        refresh_confirmation: bool = True,
        target_user_id: str | None = None,
    ) -> MemoryRecord:
        """Update a memory owned by one user scope."""
        async with self._lock:
            self._ensure_initialized()
            current = self._owned_memory(user_id, memory_id)
            target_user_id = target_user_id or user_id
            new_content = (
                _clean_content(content) if content is not None else current.content
            )
            new_category = (
                _clean_category(category) if category is not None else current.category
            )
            _validate_privacy(new_content, current.source)
            new_key = _clean_key(key) if key is not None else current.key
            if new_key and self._key_index.get((target_user_id, new_key)) not in {
                None,
                memory_id,
            }:
                raise ValueError("canonical key already exists in this memory scope")
            timestamp = dt_util.utcnow().isoformat()
            updated = self._replace_record(
                current,
                user_id=target_user_id,
                content=new_content,
                category=new_category,
                importance=(
                    _clean_importance(importance)
                    if importance is not None
                    else current.importance
                ),
                subject=(
                    _clean_optional(subject, "subject", MAX_SUBJECT_LENGTH)
                    if subject is not None
                    else current.subject
                ),
                key=new_key,
                valid_from=(
                    _clean_timestamp(valid_from, "valid_from")
                    if valid_from is not None
                    else current.valid_from
                ),
                updated_at=timestamp,
                last_confirmed_at=(
                    timestamp if refresh_confirmation else current.last_confirmed_at
                ),
            )
            await self._async_save_locked()
            self._schedule_embedding_maintenance()
            return updated

    async def async_delete(self, user_id: str, memory_ids: list[str]) -> int:
        """Delete selected memories owned by one user scope."""
        if not memory_ids or len(memory_ids) > MAX_SEARCH_LIMIT:
            raise ValueError(f"memory_ids must contain 1 to {MAX_SEARCH_LIMIT} IDs")
        async with self._lock:
            self._ensure_initialized()
            deleted = 0
            for memory_id in set(memory_ids):
                memory = self._memories.get(memory_id)
                if memory is None or memory.user_id != user_id:
                    continue
                self._unindex(memory)
                del self._memories[memory_id]
                self._invalidate_cached_embedding(memory_id)
                deleted += 1
            if deleted:
                await self._async_save_locked()
            return deleted

    async def async_clear(self, user_id: str, category: str | None = None) -> int:
        """Clear a user's memories, optionally within one category."""
        category_filter = _clean_category(category) if category else None
        async with self._lock:
            self._ensure_initialized()
            targets = [
                memory
                for memory in self._memories.values()
                if memory.user_id == user_id
                and (category_filter is None or memory.category == category_filter)
            ]
            for memory in targets:
                self._unindex(memory)
                del self._memories[memory.memory_id]
                self._invalidate_cached_embedding(memory.memory_id)
            if targets:
                await self._async_save_locked()
            return len(targets)

    async def async_reassign(
        self, source_scope_id: str, target_scope_id: str, memory_ids: list[str]
    ) -> dict[str, int]:
        """Move selected memories between explicit scopes and report exact counts."""
        if (
            not source_scope_id
            or not target_scope_id
            or source_scope_id == target_scope_id
        ):
            raise ValueError("different source and target scopes are required")
        if not memory_ids or len(memory_ids) > MAX_LIST_LIMIT:
            raise ValueError(f"memory_ids must contain 1 to {MAX_LIST_LIMIT} IDs")
        async with self._lock:
            self._ensure_initialized()
            requested = len(set(memory_ids))
            moved = 0
            for memory_id in set(memory_ids):
                current = self._memories.get(memory_id)
                if current is None or current.user_id != source_scope_id:
                    continue
                if current.key and (target_scope_id, current.key) in self._key_index:
                    continue
                self._replace_record(
                    current,
                    user_id=target_scope_id,
                    updated_at=dt_util.utcnow().isoformat(),
                )
                moved += 1
            if moved:
                await self._async_save_locked()
            return {
                "requested": requested,
                "reassigned": moved,
                "unchanged": requested - moved,
            }

    def stats(self) -> dict[str, Any]:
        """Return non-sensitive diagnostics."""
        self._ensure_initialized()
        return {
            "backend": "home_assistant_store",
            "storage_version": STORAGE_VERSION,
            "memory_count": len(self._memories),
            "user_scope_count": len({m.user_id for m in self._memories.values()}),
        }

    def scope_counts(self) -> dict[str, int]:
        """Return memory totals grouped by their exact storage owner."""
        self._ensure_initialized()
        counts: dict[str, int] = {}
        for memory in self._memories.values():
            counts[memory.user_id] = counts.get(memory.user_id, 0) + 1
        return counts

    async def async_backup_data(self) -> dict[str, Any]:
        """Return the stable durable representation used by full backups."""
        async with self._lock:
            self._ensure_initialized()
            return {
                "memories": [
                    _record_as_storage_dict(memory)
                    for memory in self._memories.values()
                ]
            }

    @staticmethod
    def validate_backup_data(data: Any) -> list[MemoryRecord]:
        """Validate a complete replacement without changing stored memories."""
        if not isinstance(data, Mapping) or set(data) != {"memories"}:
            raise ValueError("persistent memories are incomplete or corrupted")
        raw_memories = data["memories"]
        if (
            not isinstance(raw_memories, list)
            or len(raw_memories) > MAX_MEMORIES_PER_AGENT
        ):
            raise ValueError("persistent memory count is invalid")
        records: list[MemoryRecord] = []
        seen: set[str] = set()
        for raw in raw_memories:
            if not isinstance(raw, Mapping):
                raise ValueError("persistent memory record must be an object")
            try:
                record = MemoryRecord(**_migrate_raw_record(raw))
            except TypeError as err:
                raise ValueError("persistent memory record is invalid") from err
            if not all(
                isinstance(value, str)
                for value in (
                    record.memory_id,
                    record.user_id,
                    record.content,
                    record.category,
                    record.source,
                    record.created_at,
                    record.updated_at,
                    record.importance,
                )
            ):
                raise ValueError("persistent memory fields must be strings")
            if (
                not record.memory_id
                or len(record.memory_id) > 128
                or record.memory_id in seen
                or not record.user_id
                or len(record.user_id) > 128
                or record.source not in {"explicit", "implicit"}
                or record.importance not in MEMORY_IMPORTANCES
            ):
                raise ValueError("persistent memory metadata is invalid")
            _clean_content(record.content)
            _clean_category(record.category)
            _clean_optional(record.subject, "subject", MAX_SUBJECT_LENGTH)
            normalized_key = _clean_key(record.key)
            if normalized_key != record.key:
                raise ValueError("persistent memory key is not normalized")
            if (
                dt_util.parse_datetime(record.created_at) is None
                or dt_util.parse_datetime(record.updated_at) is None
                or (
                    record.valid_from is not None
                    and dt_util.parse_datetime(record.valid_from) is None
                )
                or (
                    record.last_confirmed_at is not None
                    and dt_util.parse_datetime(record.last_confirmed_at) is None
                )
            ):
                raise ValueError("persistent memory timestamp is invalid")
            seen.add(record.memory_id)
            records.append(record)
        keys: set[tuple[str, str]] = set()
        for record in records:
            if record.key is None:
                continue
            pair = (record.user_id, record.key)
            if pair in keys:
                raise ValueError("duplicate canonical key in memory scope")
            keys.add(pair)
        return records

    async def async_replace_backup(self, records: list[MemoryRecord]) -> None:
        """Atomically replace all persistent memories with validated records."""
        async with self._lock:
            self._ensure_initialized()
            self._memories = {record.memory_id: record for record in records}
            self._embedding_cache.clear()
            self._embedding_cache_dirty = True
            self._token_index.clear()
            self._key_index.clear()
            for record in records:
                self._index(record)
            await self._async_save_locked()
            self._schedule_embedding_maintenance()

    def _find_duplicate(self, user_id: str, content: str) -> MemoryRecord | None:
        normalized = _normalize(content)
        content_tokens = _tokens(content)
        for memory in self._memories.values():
            if memory.user_id != user_id:
                continue
            if _normalize(memory.content) == normalized:
                return memory
            existing_tokens = _tokens(memory.content)
            union = content_tokens | existing_tokens
            if union and len(content_tokens & existing_tokens) / len(union) >= 0.85:
                return memory
        return None

    def _find_related_candidate(
        self, user_id: str, content: str, subject: str | None, key: str | None
    ) -> MemoryRecord | None:
        incoming = _tokens(content)
        key_root = key.rsplit(".", 1)[0] if key and "." in key else None
        best: tuple[float, str, MemoryRecord] | None = None
        for memory in self._memories.values():
            if memory.user_id != user_id:
                continue
            existing = _tokens(memory.content)
            union = incoming | existing
            similarity = len(incoming & existing) / len(union) if union else 0.0
            if (
                subject
                and memory.subject
                and _normalize(subject) == _normalize(memory.subject)
            ) or (subject and _tokens(subject) & existing):
                similarity += 0.3
            if key_root and memory.key and memory.key.startswith(f"{key_root}."):
                similarity += 0.35
            candidate = (similarity, memory.memory_id, memory)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
        return best[2] if best is not None and best[0] >= 0.62 else None

    def _owned_memory(self, user_id: str, memory_id: str) -> MemoryRecord:
        memory = self._memories.get(memory_id)
        if memory is None or memory.user_id != user_id:
            raise ValueError("memory not found")
        return memory

    def _index(self, memory: MemoryRecord) -> None:
        for token in _record_tokens(memory):
            self._token_index[(memory.user_id, token)].add(memory.memory_id)
        if memory.key:
            existing = self._key_index.get((memory.user_id, memory.key))
            if existing is not None and existing != memory.memory_id:
                raise ValueError("canonical key already exists in this memory scope")
            self._key_index[(memory.user_id, memory.key)] = memory.memory_id

    def _unindex(self, memory: MemoryRecord) -> None:
        for token in _record_tokens(memory):
            key = (memory.user_id, token)
            ids = self._token_index.get(key)
            if ids is None:
                continue
            ids.discard(memory.memory_id)
            if not ids:
                del self._token_index[key]
        if memory.key:
            self._key_index.pop((memory.user_id, memory.key), None)

    def _replace_record(self, current: MemoryRecord, **changes: Any) -> MemoryRecord:
        self._unindex(current)
        values = asdict(current)
        values.update(changes)
        updated = MemoryRecord(**values)
        self._memories[current.memory_id] = updated
        self._index(updated)
        if _embedding_fingerprint(updated) != _embedding_fingerprint(current):
            self._invalidate_cached_embedding(current.memory_id)
        return updated

    def _schedule_embedding_maintenance(self) -> None:
        if (
            self._embedding_provider is None
            or self._embedding_task_scheduler is None
            or not self._initialized
        ):
            return
        task = self._embedding_maintenance_task
        if task is not None and not task.done():
            self._embedding_maintenance_requested = True
            return
        self._embedding_maintenance_requested = False
        self._embedding_maintenance_task = self._embedding_task_scheduler(
            self._async_run_embedding_maintenance()
        )

    async def _async_run_embedding_maintenance(self) -> None:
        try:
            while True:
                self._embedding_maintenance_requested = False
                await self._async_refresh_missing_embeddings(None, background=True)
                if self._embedding_maintenance_requested:
                    continue
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.warning(
                "Background hybrid memory embedding maintenance failed",
                exc_info=True,
            )
        finally:
            self._embedding_maintenance_task = None

    async def _async_refresh_missing_embeddings(
        self,
        scope_ids: Sequence[str] | None,
        *,
        background: bool = False,
    ) -> bool:
        provider = self._embedding_provider
        if provider is None:
            return False
        model = self._embedding_model
        allowed_scopes = set(scope_ids) if scope_ids is not None else None
        missing = [
            memory
            for memory in self._memories.values()
            if (allowed_scopes is None or memory.user_id in allowed_scopes)
            and self._cached_embedding(memory) is None
        ]
        for offset in range(0, len(missing), EMBEDDING_CACHE_BATCH_SIZE):
            batch = missing[offset : offset + EMBEDDING_CACHE_BATCH_SIZE]
            vectors = await provider([_embedding_text(memory) for memory in batch])
            if len(vectors) != len(batch):
                raise ValueError(
                    "embedding provider returned the wrong number of vectors"
                )
            generated_ids: list[str] = []
            async with self._lock:
                if (
                    self._embedding_provider is not provider
                    or self._embedding_model != model
                ):
                    return False
                for memory, vector in zip(batch, vectors, strict=True):
                    current = self._memories.get(memory.memory_id)
                    if (
                        current is None
                        or _embedding_fingerprint(current)
                        != _embedding_fingerprint(memory)
                        or self._cached_embedding(current) is not None
                    ):
                        continue
                    self._embedding_cache[memory.memory_id] = EmbeddingCacheEntry(
                        model=model,
                        fingerprint=_embedding_fingerprint(memory),
                        vector=_clean_embedding(vector),
                    )
                    generated_ids.append(memory.memory_id)
                if not generated_ids:
                    continue
                self._embedding_cache_dirty = True
                if not await self._async_save_embedding_cache_locked():
                    if background:
                        for memory_id in generated_ids:
                            self._embedding_cache.pop(memory_id, None)
                    return False
        return True

    async def _async_save_locked(self) -> None:
        await self._storage.async_save(
            {
                "memories": [
                    _record_as_storage_dict(memory)
                    for memory in self._memories.values()
                ]
            }
        )
        if self._embedding_cache_dirty:
            await self._async_save_embedding_cache_locked()

    def _cached_embedding(self, memory: MemoryRecord) -> list[float] | None:
        entry = self._embedding_cache.get(memory.memory_id)
        if (
            entry is None
            or entry.model != self._embedding_model
            or entry.fingerprint != _embedding_fingerprint(memory)
        ):
            return None
        return entry.vector

    def _invalidate_cached_embedding(self, memory_id: str) -> None:
        if self._embedding_cache.pop(memory_id, None) is not None:
            self._embedding_cache_dirty = True

    async def _async_load_embedding_cache_locked(self) -> None:
        if self._embedding_cache_storage is None:
            return
        try:
            data = await self._embedding_cache_storage.async_load()
            raw_entries = (
                data.get("embeddings", {}) if isinstance(data, Mapping) else {}
            )
            if not isinstance(raw_entries, Mapping):
                raise ValueError("embedding cache entries must be an object")
            for memory_id, raw in raw_entries.items():
                if not isinstance(memory_id, str) or not isinstance(raw, Mapping):
                    raise ValueError("embedding cache entry is malformed")
                model = raw.get("model")
                fingerprint = raw.get("fingerprint")
                if not isinstance(model, str) or not isinstance(fingerprint, str):
                    raise ValueError("embedding cache metadata is malformed")
                self._embedding_cache[memory_id] = EmbeddingCacheEntry(
                    model=model,
                    fingerprint=fingerprint,
                    vector=_clean_embedding(raw.get("vector")),
                )
        except Exception:
            self._embedding_cache.clear()
            _LOGGER.warning(
                "Persistent memory embedding cache is unavailable; it will regenerate",
                exc_info=True,
            )

    async def _async_save_embedding_cache_locked(self) -> bool:
        if not self._embedding_cache_dirty:
            return True
        if self._embedding_cache_storage is None:
            self._embedding_cache_dirty = False
            return True
        try:
            await self._embedding_cache_storage.async_save(
                {
                    "embeddings": {
                        memory_id: asdict(entry)
                        for memory_id, entry in self._embedding_cache.items()
                        if memory_id in self._memories
                    }
                }
            )
            self._embedding_cache_dirty = False
            return True
        except Exception:
            _LOGGER.warning(
                "Persistent memory embedding cache could not be saved",
                exc_info=True,
            )
            return False

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("persistent memory has not been initialized")


_MEMORY_MANAGERS = f"{DOMAIN}.memory_managers"


def get_memory_mode(options: dict[str, Any] | Any) -> str:
    """Return the memory mode, interpreting legacy settings when necessary."""
    configured = options.get(CONF_MEMORY_MODE)
    if configured in MEMORY_MODES:
        return str(configured)
    if not options.get(CONF_MEMORY_ENABLED, DEFAULT_MEMORY_ENABLED):
        return MEMORY_MODE_OFF
    if options.get(CONF_MEMORY_AUTO_CREATE, DEFAULT_MEMORY_AUTO_CREATE):
        return MEMORY_MODE_AUTOMATIC
    return MEMORY_MODE_MANUAL


def memory_enabled(options: dict[str, Any] | Any) -> bool:
    """Return whether persistent memory is enabled for an agent."""
    return get_memory_mode(options) != MEMORY_MODE_OFF


def automatic_memory_enabled(options: dict[str, Any] | Any) -> bool:
    """Return whether the model may create memories proactively."""
    return get_memory_mode(options) == MEMORY_MODE_AUTOMATIC


async def async_get_memory(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> PersistentMemory:
    """Get the shared in-process manager for a conversation agent."""
    managers: dict[tuple[str, str], PersistentMemory] = hass.data.setdefault(
        _MEMORY_MANAGERS, {}
    )
    key = (entry_id, subentry_id)
    if key not in managers:
        managers[key] = PersistentMemory(
            HomeAssistantMemoryStorage(hass, entry_id, subentry_id),
            HomeAssistantEmbeddingCacheStorage(hass, entry_id, subentry_id),
            hass.async_create_task,
        )
    manager = managers[key]
    await manager.async_initialize()
    return manager


def memory_user_id(context: Any) -> str:
    """Resolve Home Assistant's authenticated user scope."""
    ha_context = getattr(context, "context", None)
    return getattr(ha_context, "user_id", None) or ANONYMOUS_USER_ID


def memory_as_dict(
    memory: MemoryRecord,
    *,
    include_scope: bool = False,
    personal_scope_id: str | None = None,
) -> dict[str, Any]:
    """Serialize a memory, exposing its owner only to explicit admin callers."""
    result = {
        "memory_id": memory.memory_id,
        "content": memory.content,
        "category": memory.category,
        "source": memory.source,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
        "importance": getattr(memory, "importance", "normal"),
        "subject": getattr(memory, "subject", None),
        "key": getattr(memory, "key", None),
        "valid_from": getattr(memory, "valid_from", None),
        "last_confirmed_at": getattr(memory, "last_confirmed_at", None),
    }
    if include_scope:
        owner = getattr(memory, "user_id", personal_scope_id)
        result["scope_id"] = owner
        result["scope"] = (
            "Personal"
            if personal_scope_id == owner
            else "Shared household"
            if owner == "shared:household"
            else "Personal"
        )
    return result


def validate_memory_privacy(content: str, *, automatic: bool) -> None:
    """Apply the durable-memory secret and sensitivity safeguards to other stores."""
    _validate_privacy(content, "implicit" if automatic else "explicit")


def memory_tools() -> list[dict[str, Any]]:
    """Return internal function definitions for persistent memory."""
    return [
        {
            "spec": {
                "name": "memory_add",
                "description": "Store one concise, durable fact for the current user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Concise self-contained fact, not transcript text.",
                        },
                        "category": {
                            "type": "string",
                            "description": "Short flexible category such as preferences or devices.",
                        },
                        "source": {
                            "type": "string",
                            "enum": ["explicit", "implicit"],
                            "description": "Whether the user explicitly asked or this is proactively useful.",
                        },
                        **_memory_metadata_schema(include_scope=True),
                    },
                    "required": ["content", "category", "source"],
                    "additionalProperties": False,
                },
            },
            "function": {"type": "memory", "operation": "add"},
        },
        {
            "spec": {
                "name": "memory_upsert",
                "description": "Create, confirm, or replace a durable fact using a stable canonical key when available.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "category": {"type": "string"},
                        "source": {"type": "string", "enum": ["explicit", "implicit"]},
                        **_memory_metadata_schema(include_scope=True),
                    },
                    "required": ["content", "category", "source"],
                    "additionalProperties": False,
                },
            },
            "function": {"type": "memory", "operation": "upsert"},
        },
        {
            "spec": {
                "name": "memory_search",
                "description": "Search relevant durable facts for the current user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "category": {"type": "string"},
                        "scope": {"type": "string", "enum": ["personal", "household"]},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            "function": {"type": "memory", "operation": "search"},
        },
        {
            "spec": {
                "name": "memory_list",
                "description": "List the current user's memories, optionally by category.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "scope": {"type": "string", "enum": ["personal", "household"]},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        "offset": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
            },
            "function": {"type": "memory", "operation": "list"},
        },
        {
            "spec": {
                "name": "memory_update",
                "description": "Correct or replace an existing memory for the current user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string"},
                        "content": {"type": "string"},
                        "category": {"type": "string"},
                        **_memory_metadata_schema(include_scope=True),
                    },
                    "required": ["memory_id"],
                    "additionalProperties": False,
                },
            },
            "function": {"type": "memory", "operation": "update"},
        },
        {
            "spec": {
                "name": "memory_delete",
                "description": "Permanently delete selected memories after identifying their IDs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": 50,
                        },
                        "scope": {"type": "string", "enum": ["personal", "household"]},
                    },
                    "required": ["memory_ids"],
                    "additionalProperties": False,
                },
            },
            "function": {"type": "memory", "operation": "delete"},
        },
    ]


def _memory_metadata_schema(*, include_scope: bool) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "importance": {"type": "string", "enum": ["low", "normal", "high"]},
        "subject": {"type": "string"},
        "key": {"type": "string"},
        "valid_from": {
            "type": "string",
            "description": "ISO 8601 time when the fact became true, if known.",
        },
    }
    if include_scope:
        schema["scope"] = {"type": "string", "enum": ["personal", "household"]}
    return schema


def _token_list(value: str) -> list[str]:
    return [
        _stem(token)
        for token in _TOKEN_PATTERN.findall(value.casefold())
        if len(token) > 1 and token not in _STOP_WORDS
    ]


def _tokens(value: str) -> set[str]:
    return set(_token_list(value))


def _stem(token: str) -> str:
    """Apply conservative deterministic English suffix normalization."""
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 5 and token.endswith("ing"):
        root = token[:-3]
        if len(root) > 2 and root[-1] == root[-2]:
            root = root[:-1]
        return root
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 5 and token.endswith(("ches", "shes", "xes", "zes", "oes")):
        return token[:-2]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _record_token_list(memory: MemoryRecord) -> list[str]:
    return _token_list(
        " ".join(
            filter(None, (memory.content, memory.category, memory.subject, memory.key))
        )
    )


def _record_tokens(memory: MemoryRecord) -> set[str]:
    return set(_record_token_list(memory))


def _bm25_score(
    query_terms: list[str],
    document_terms: list[str],
    document_frequency: Mapping[str, int],
    document_count: int,
    average_length: float,
) -> float:
    frequencies: dict[str, int] = defaultdict(int)
    for term in document_terms:
        frequencies[term] += 1
    k1, b = 1.2, 0.75
    score = 0.0
    max_score = 0.0
    for term in dict.fromkeys(query_terms):
        df = document_frequency.get(term, 0)
        idf = math.log(1 + (document_count - df + 0.5) / (df + 0.5))
        tf = frequencies.get(term, 0)
        denominator = tf + k1 * (1 - b + b * len(document_terms) / average_length)
        if tf:
            score += idf * (tf * (k1 + 1) / denominator)
        max_score += idf * (k1 + 1) / (1 + k1 * (1 - b))
    return score / max_score if max_score else 0.0


def _metadata_bonus(query_tokens: set[str], memory: MemoryRecord) -> float:
    category = _tokens(memory.category)
    subject = _tokens(memory.subject or "")
    key = _tokens((memory.key or "").replace(".", " "))
    return (
        0.10 * len(query_tokens & category)
        + 0.16 * len(query_tokens & subject)
        + 0.18 * len(query_tokens & key)
    )


def _edit_distance_one(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1 or min(len(left), len(right)) < 4:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right, strict=True)) <= 1
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    index = 0
    while index < len(shorter) and shorter[index] == longer[index]:
        index += 1
    return shorter[index:] == longer[index + 1 :]


def _fuzzy_relevance(query_tokens: set[str], document_tokens: set[str]) -> float:
    matches = 0
    for query in query_tokens:
        if any(
            (
                min(len(query), len(token)) >= 5
                and (query.startswith(token) or token.startswith(query))
            )
            or _edit_distance_one(query, token)
            for token in document_tokens
        ):
            matches += 1
    return 0.16 * matches / max(1, len(query_tokens))


def _cosine_similarity(
    left: list[float] | None, right: list[float] | None
) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return None
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )


def _freshness_tiebreak(memory: MemoryRecord) -> float:
    parsed = dt_util.parse_datetime(memory.last_confirmed_at or memory.updated_at)
    return parsed.timestamp() if parsed else 0.0


def _embedding_text(memory: MemoryRecord) -> str:
    return " | ".join(
        filter(None, (memory.subject, memory.key, memory.category, memory.content))
    )


def _embedding_fingerprint(memory: MemoryRecord) -> str:
    payload = json.dumps(
        [memory.content, memory.category, memory.subject, memory.key],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _clean_embedding(value: Any) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("embedding must be a numeric sequence")
    result = [float(item) for item in value]
    if (
        not result
        or len(result) > 16_384
        or not all(math.isfinite(item) for item in result)
    ):
        raise ValueError("embedding is invalid")
    return result


def _record_as_storage_dict(memory: MemoryRecord) -> dict[str, Any]:
    return asdict(memory)


def _migrate_raw_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(raw)
    result.setdefault("importance", "normal")
    result.setdefault("subject", None)
    result.setdefault("key", None)
    result.setdefault("valid_from", None)
    result.setdefault(
        "last_confirmed_at", result.get("updated_at") or result.get("created_at")
    )
    result.pop("embedding", None)
    return result


def _normalize(value: str) -> str:
    return " ".join(_TOKEN_PATTERN.findall(value.casefold()))


def _clean_content(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("content must be a string")
    value = _SPACE_PATTERN.sub(" ", value).strip()
    if not value or len(value) > MAX_CONTENT_LENGTH:
        raise ValueError(f"content must be 1 to {MAX_CONTENT_LENGTH} characters")
    return value


def _clean_category(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("category must be a string")
    value = _SPACE_PATTERN.sub(" ", value).strip().casefold()
    if not value or len(value) > MAX_CATEGORY_LENGTH:
        raise ValueError(f"category must be 1 to {MAX_CATEGORY_LENGTH} characters")
    return value


def _clean_importance(value: str) -> str:
    if not isinstance(value, str) or value not in MEMORY_IMPORTANCES:
        raise ValueError("importance must be low, normal, or high")
    return value


def _clean_optional(value: str | None, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    cleaned = _SPACE_PATTERN.sub(" ", value).strip()
    if not cleaned:
        return None
    if len(cleaned) > maximum:
        raise ValueError(f"{field} must be at most {maximum} characters")
    return cleaned


def _clean_key(value: str | None) -> str | None:
    cleaned = _clean_optional(value, "key", MAX_KEY_LENGTH)
    if cleaned is None:
        return None
    normalized = re.sub(r"[^a-z0-9._-]+", ".", cleaned.casefold()).strip(".")
    normalized = re.sub(r"\.{2,}", ".", normalized)
    if not normalized:
        raise ValueError("key must contain letters or numbers")
    return normalized


def _clean_timestamp(value: str | None, field: str) -> str | None:
    cleaned = _clean_optional(value, field, 64)
    if cleaned is not None and dt_util.parse_datetime(cleaned) is None:
        raise ValueError(f"{field} must be an ISO 8601 timestamp")
    return cleaned


def _validate_privacy(content: str, source: str) -> None:
    if _SECRET_PATTERN.search(content):
        raise ValueError("memory rejected because it appears to contain a secret")
    if _contains_financial_credential(content):
        raise ValueError(
            "memory rejected because it appears to contain a financial credential"
        )
    if source == "implicit" and _SENSITIVE_IMPLICIT_PATTERN.search(content):
        raise ValueError("sensitive memories require an explicit user request")


def _contains_financial_credential(content: str) -> bool:
    """Conservatively detect usable payment or bank credentials."""
    if _FINANCIAL_CREDENTIAL_PATTERN.search(content):
        return True

    has_card_context = _PAYMENT_CARD_CONTEXT_PATTERN.search(content) is not None
    for match in _PAYMENT_CARD_CANDIDATE_PATTERN.finditer(content):
        digits = re.sub(r"\D", "", match.group())
        if has_card_context or _passes_luhn_checksum(digits):
            return True

    return any(
        _is_valid_iban(re.sub(r"[ -]", "", match.group()))
        for match in _IBAN_CANDIDATE_PATTERN.finditer(content)
    )


def _passes_luhn_checksum(value: str) -> bool:
    """Return whether a payment-card candidate has a valid Luhn checksum."""
    if not 13 <= len(value) <= 19 or not value.isdigit():
        return False
    total = 0
    for index, character in enumerate(reversed(value)):
        digit = int(character)
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _is_valid_iban(value: str) -> bool:
    """Return whether a compact IBAN candidate has a valid checksum."""
    value = value.upper()
    if not 15 <= len(value) <= 34 or not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", value):
        return False
    rearranged = value[4:] + value[:4]
    numeric = "".join(
        character if character.isdigit() else str(ord(character) - 55)
        for character in rearranged
    )
    return int(numeric) % 97 == 1
