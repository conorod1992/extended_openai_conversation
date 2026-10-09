"""Local persistent memory for conversation agents."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
import hashlib
import json
import logging
import math
import re
from types import MappingProxyType
from typing import Any, Protocol
import unicodedata
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
from .operational_errors import log_handled_failure
from .persistence_hardening import _async_settle_transactional_save
from .provider_errors import provider_failure_category, provider_log_remediation
from .scope import LEGACY_ANONYMOUS_SCOPE_ID
from .strict_store import PropagatingWriteStore, async_storage_lock

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
MIN_LEXICAL_RELEVANCE_SCORE = 0.08
MIN_SEMANTIC_SIMILARITY = 0.55
EmbeddingProvider = Callable[[list[str]], Awaitable[list[list[float]]]]


class _UnsetType:
    """Marker for an omitted optional tool argument."""


_UNSET = _UnsetType()

_TOKEN_PATTERN = re.compile(r"[\w'-]+", re.UNICODE)
_SPACE_PATTERN = re.compile(r"\s+")
_MEMORY_KEY_PATTERN = re.compile(r"^[a-z0-9_-]+(?:\.[a-z0-9_-]+)*$")
_SECRET_PATTERN = re.compile(
    # Accept separators and camelCase label starts; keep case transitions
    # case-sensitive even though credential names themselves ignore case.
    r"(?:(?<![^\W_])|(?<=(?-i:[a-z]))(?=(?-i:[A-Z])))"
    r"(?:password|passcode|api[_ -]?key|access[_ -]?token|auth[_ -]?token|"
    r"security[_ -]?code|secret|pin)\b\s*(?:is|:|=)\s*\S+|"
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
    subject: str | None = None
    key: str | None = None
    valid_from: str | None = None


@dataclass(slots=True, frozen=True)
class EmbeddingCacheEntry:
    """Regenerable embedding data kept outside the durable memory store."""

    model: str
    fingerprint: str
    vector: list[float]
    space_id: str = ""


@dataclass(slots=True)
class _MemoryMutationSnapshot:
    """Last successfully committed live state for durable-write rollback."""

    memories: dict[str, MemoryRecord]
    key_index: dict[tuple[str, str], str]
    embedding_cache: dict[str, EmbeddingCacheEntry]
    embedding_cache_dirty: bool


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
        """Save cached embeddings."""


class MemoryStore(PropagatingWriteStore):
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
        ).bind_agent(entry_id, subentry_id)

    async def async_load(self) -> dict[str, Any] | None:
        """Load data."""
        return await self._store.async_load()

    async def async_save(self, data: dict[str, Any]) -> None:
        """Persist data."""
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
        """Save cached embeddings."""
        await self._store.async_save(data)


class PersistentMemory:
    """Concurrency-safe memory collection with bounded indexed search."""

    def __init__(
        self,
        storage: MemoryStorage,
        embedding_cache_storage: EmbeddingCacheStorage | None = None,
    ) -> None:
        """Initialize memory collection."""
        self._storage = storage
        self._memories: dict[str, MemoryRecord] = {}
        self._key_index: dict[tuple[str, str], str] = {}
        self._embedding_provider: EmbeddingProvider | None = None
        self._embedding_model = "default"
        self._embedding_space_id = ""
        self._embedding_cache_storage = embedding_cache_storage
        self._embedding_cache: dict[str, EmbeddingCacheEntry] = {}
        self._embedding_cache_dirty = False
        self._embedding_cache_write_failed = False
        self._hybrid_status: dict[str, Any] = {
            "configured": False,
            "status": "lexical_fallback",
            "model": "default",
            "reason": "provider_not_configured",
        }
        self._lock = asyncio.Lock()
        self._initialized = False
        self._committed_state: _MemoryMutationSnapshot | None = None

    @property
    def initialized(self) -> bool:
        """Return whether authoritative stored facts are ready for use."""
        return self._initialized

    async def async_initialize(self) -> None:
        """Load, validate, and self-heal memory data once."""
        async with async_storage_lock(self._storage, self._lock):
            if self._initialized:
                return
            try:
                data = await self._storage.async_load()
                needs_save = False
                if data is None:
                    raw_memories: list[Any] = []
                elif not isinstance(data, Mapping) or "memories" not in data:
                    raw_memories = []
                    needs_save = True
                else:
                    candidate = data.get("memories")
                    if not isinstance(candidate, list):
                        raw_memories = []
                        needs_save = True
                    else:
                        raw_memories = candidate
                        if len(raw_memories) > MAX_MEMORIES_PER_AGENT:
                            raw_memories = raw_memories[:MAX_MEMORIES_PER_AGENT]
                            needs_save = True

                legacy_embeddings_found = False
                seen_ids: set[str] = set()
                seen_keys: set[tuple[str, str]] = set()
                for raw in raw_memories:
                    needs_save |= isinstance(raw, Mapping) and bool(
                        {"importance", "last_confirmed_at"} & raw.keys()
                    )
                    legacy_embeddings_found |= (
                        isinstance(raw, Mapping) and "embedding" in raw
                    )
                    try:
                        memory = _validate_persistent_memory_record(raw)
                        if memory.memory_id in seen_ids:
                            raise ValueError("duplicate persistent memory ID")
                        if memory.key is not None:
                            key_pair = (memory.user_id, memory.key)
                            if key_pair in seen_keys:
                                raise ValueError(
                                    "duplicate canonical key in memory scope"
                                )
                            seen_keys.add(key_pair)
                    except TypeError, ValueError:
                        needs_save = True
                        _LOGGER.warning("Ignoring malformed persistent memory record")
                        continue
                    seen_ids.add(memory.memory_id)
                    self._assert_key_available(memory)
                    self._memories[memory.memory_id] = memory
                    self._index(memory)
                if legacy_embeddings_found or needs_save:
                    await self._storage.async_save(
                        {
                            "memories": [
                                _record_as_storage_dict(memory)
                                for memory in self._memories.values()
                            ]
                        }
                    )
                await self._async_load_embedding_cache_locked()
                self._remember_committed_state()
                self._initialized = True
            except BaseException:
                self._memories.clear()
                self._key_index.clear()
                self._embedding_cache.clear()
                self._embedding_cache_dirty = False
                self._initialized = False
                self._committed_state = None
                raise

    def set_embedding_provider(
        self,
        provider: EmbeddingProvider | None,
        model: str = "default",
        *,
        space_id: str = "",
    ) -> None:
        """Configure the optional provider used only by hybrid retrieval."""
        # Unchanged live configuration must preserve diagnostics and avoid prewarming.
        if (
            self._embedding_provider == provider
            and self._embedding_model == model
            and self._embedding_space_id == space_id
        ):
            return
        model = str(model).strip() or "default"
        self._embedding_provider = provider
        self._embedding_model = model
        self._embedding_space_id = space_id
        self._set_hybrid_status(
            "ready" if provider is not None else "lexical_fallback",
            None if provider is not None else "provider_not_configured",
        )

    def hybrid_status(self) -> dict[str, Any]:
        """Return non-sensitive hybrid-retrieval availability diagnostics."""
        return dict(self._hybrid_status)

    async def async_add(
        self,
        user_id: str,
        content: str,
        category: str,
        source: str,
        subject: str | None = None,
        key: str | None = None,
        valid_from: str | None = None,
    ) -> dict[str, Any]:
        """Add a memory, or return a likely duplicate for an unkeyed fact."""
        content = _clean_content(content)
        category = _clean_category(category)
        subject = _clean_optional(subject, "subject", MAX_SUBJECT_LENGTH)
        key = _clean_key(key)
        valid_from = _clean_timestamp(valid_from, "valid_from")
        if source not in {"explicit", "implicit"}:
            raise ValueError("source must be explicit or implicit")
        _validate_privacy_fields(source, content, category, subject, key)
        async with async_storage_lock(self._storage, self._lock):
            self._ensure_initialized()
            if key and (user_id, key) in self._key_index:
                raise ValueError("canonical key already exists in this memory scope")
            if key is None:
                duplicate = self._find_duplicate(user_id, content)
                if duplicate:
                    if duplicate.source == "implicit" and source == "explicit":
                        timestamp = dt_util.utcnow().isoformat()
                        changes = {"source": "explicit"}
                        _set_updated_at_if_substantive(duplicate, changes, timestamp)
                        duplicate = self._replace_record(duplicate, **changes)
                        await self._async_save_locked()
                        return {
                            "status": "updated",
                            "memory": memory_as_dict(duplicate),
                        }
                    return {"status": "duplicate", "memory": memory_as_dict(duplicate)}
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
                subject=subject,
                key=key,
                valid_from=valid_from,
            )
            self._assert_key_available(memory)
            self._memories[memory.memory_id] = memory
            self._index(memory)
            await self._async_save_locked()
            return {"status": "created", "memory": memory_as_dict(memory)}

    async def async_upsert(
        self,
        user_id: str,
        content: str,
        category: str,
        source: str,
        subject: str | _UnsetType | None = _UNSET,
        key: str | _UnsetType | None = _UNSET,
        valid_from: str | _UnsetType | None = _UNSET,
    ) -> dict[str, Any]:
        """Create or update by canonical key, or surface a likely conflict."""
        content = _clean_content(content)
        category = _clean_category(category)
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
        _validate_privacy_fields(
            source, content, category, cleaned_subject, cleaned_key
        )
        async with async_storage_lock(self._storage, self._lock):
            self._ensure_initialized()
            timestamp = dt_util.utcnow().isoformat()
            keyed_identity = isinstance(cleaned_key, str)
            if isinstance(cleaned_key, str) and (
                memory_id := self._key_index.get((user_id, cleaned_key))
            ):
                current = self._memories[memory_id]
                if current.source == "explicit" and source == "implicit":
                    if current.content != content:
                        return {
                            "status": "needs_resolution",
                            "candidate": memory_as_dict(current),
                        }
                    return {"status": "unchanged", "memory": memory_as_dict(current)}
                changes: dict[str, Any] = {
                    "content": content,
                    "category": category,
                    "key": cleaned_key,
                    "source": "explicit" if current.source == "explicit" else source,
                }
                if cleaned_subject is not _UNSET:
                    changes["subject"] = cleaned_subject
                if cleaned_valid_from is not _UNSET:
                    changes["valid_from"] = cleaned_valid_from
                _set_updated_at_if_substantive(current, changes, timestamp)
                updated = self._replace_record(current, **changes)
                await self._async_save_locked()
                return {
                    "status": "unchanged" if updated == current else "updated",
                    "memory": memory_as_dict(updated),
                }

            if not keyed_identity:
                duplicate = self._find_duplicate(user_id, content)
                if duplicate:
                    if duplicate.source == "explicit" and source == "implicit":
                        return {
                            "status": "unchanged",
                            "memory": memory_as_dict(duplicate),
                        }
                    changes = {
                        "category": category,
                        "source": "explicit"
                        if duplicate.source == "explicit"
                        else source,
                    }
                    if cleaned_subject is not _UNSET:
                        changes["subject"] = cleaned_subject
                    if cleaned_key is not _UNSET:
                        changes["key"] = cleaned_key
                    if cleaned_valid_from is not _UNSET:
                        changes["valid_from"] = cleaned_valid_from
                    _set_updated_at_if_substantive(duplicate, changes, timestamp)
                    updated = self._replace_record(duplicate, **changes)
                    await self._async_save_locked()
                    return {
                        "status": "unchanged" if updated == duplicate else "updated",
                        "memory": memory_as_dict(updated),
                    }
                candidate = self._find_related_candidate(
                    user_id,
                    content,
                    cleaned_subject if isinstance(cleaned_subject, str) else None,
                    None,
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
                subject=(cleaned_subject if isinstance(cleaned_subject, str) else None),
                key=cleaned_key if isinstance(cleaned_key, str) else None,
                valid_from=(
                    cleaned_valid_from if isinstance(cleaned_valid_from, str) else None
                ),
            )
            self._assert_key_available(memory)
            self._memories[memory.memory_id] = memory
            self._index(memory)
            await self._async_save_locked()
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
        offset: int = 0,
    ) -> list[MemoryRecord]:
        """Return deterministic BM25-style lexical or hybrid results."""
        async with async_storage_lock(self._storage, self._lock):
            self._ensure_initialized()
            limit = max(1, min(limit, MAX_SEARCH_LIMIT))
            offset = max(0, offset)
            scope_ids = (
                (user_id,)
                if isinstance(user_id, str)
                else tuple(dict.fromkeys(user_id))
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
            document_data: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {}
            document_frequency = {token: 0 for token in query_tokens}
            total_length = 0
            for memory in corpus:
                terms = _cached_memory_record_terms(memory)
                token_set = _cached_memory_record_token_set(memory)
                document_data[memory.memory_id] = (terms, token_set)
                total_length += len(terms)
                for token in query_tokens:
                    if token in token_set:
                        document_frequency[token] += 1
            average_length = max(1.0, total_length / len(corpus))
            normalized_query = _normalize(query)
            ranked: list[tuple[float, str, MemoryRecord]] = []
            for memory in corpus:
                terms, token_set = document_data[memory.memory_id]
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
                    lexical = _fuzzy_relevance(query_tokens, token_set)
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
                ranked.append((relevance, memory.memory_id, memory))
            ranked.sort(key=lambda item: (-item[0], item[1]))
            return [memory for _, _, memory in ranked[offset : offset + limit]]

    async def async_prepare_hybrid(
        self, scope_ids: Sequence[str], query: str
    ) -> list[float] | None:
        """Prepare embeddings only for searched scopes and return the query vector."""
        if self._embedding_provider is None:
            self._set_hybrid_status("lexical_fallback", "provider_not_configured")
            return None
        try:
            if not await self._async_refresh_missing_embeddings(scope_ids):
                self._set_hybrid_status(
                    "lexical_fallback", "embedding_preparation_failed"
                )
                return None
            provider = self._embedding_provider
            model = self._embedding_model
            if provider is None:
                self._set_hybrid_status("lexical_fallback", "provider_changed")
                return None
            vectors = await provider([query])
            if (
                self._embedding_provider is not provider
                or self._embedding_model != model
            ):
                self._set_hybrid_status("lexical_fallback", "provider_changed")
                return None
            if len(vectors) != 1:
                raise ValueError(
                    "embedding provider returned the wrong number of vectors"
                )
            vector = _clean_embedding(vectors[0])
            self._set_hybrid_status("active")
            return vector
        except Exception as err:
            reason = provider_failure_category(err)
            changed = (
                self._hybrid_status.get("status") != "lexical_fallback"
                or self._hybrid_status.get("reason") != reason
                or self._hybrid_status.get("error_type") != type(err).__name__
            )
            self._set_hybrid_status(
                "lexical_fallback", reason, error_type=type(err).__name__
            )
            if changed:
                log_handled_failure(
                    _LOGGER,
                    f"Hybrid memory embeddings unavailable model={self._embedding_model} "
                    f"reason={reason}; using lexical retrieval. Memories remain available "
                    "through word matching, but semantic matches may be missed. "
                    + provider_log_remediation(err),
                    err,
                )
            return None

    async def async_get_many(
        self, references: Sequence[tuple[str, str]], readable_scope_ids: Sequence[str]
    ) -> list[MemoryRecord]:
        """Resolve a selected bundle by owner and ID without reranking."""
        async with async_storage_lock(self._storage, self._lock):
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
        async with async_storage_lock(self._storage, self._lock):
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

    async def async_list_page(
        self,
        user_id: str | Sequence[str],
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[MemoryRecord], bool]:
        """List one page and continuation state with a single scan and sort."""
        async with async_storage_lock(self._storage, self._lock):
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
            end = offset + limit
            return memories[offset:end], len(memories) > end

    @property
    def memory_count(self) -> int:
        """Return the number of retained persistent memories in O(1)."""
        self._ensure_initialized()
        return len(self._published_memories())

    async def async_browse(
        self,
        user_id: str,
        query: str,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[MemoryRecord], int]:
        """Browse one owner's complete management projection with bounded output."""
        async with async_storage_lock(self._storage, self._lock):
            self._ensure_initialized()
            limit = max(1, min(limit, MAX_LIST_LIMIT))
            offset = max(0, offset)
            folded_query = unicodedata.normalize("NFC", str(query)).casefold()
            memories = [
                memory
                for memory in self._memories.values()
                if memory.user_id == user_id
                and folded_query
                in unicodedata.normalize(
                    "NFC",
                    " ".join(
                        str(value or "")
                        for value in (memory.content, memory.category, memory.source)
                    ),
                ).casefold()
            ]
            memories.sort(key=lambda memory: memory.updated_at, reverse=True)
            total = len(memories)
            return memories[offset : offset + limit], total

    async def async_update(
        self,
        user_id: str,
        memory_id: str,
        content: str | None = None,
        category: str | None = None,
        subject: str | None = None,
        key: str | None = None,
        valid_from: str | None = None,
        target_user_id: str | None = None,
        clear_fields: Sequence[str] | None = None,
        expected_revision: str | None = None,
        *,
        source: str | None = None,
    ) -> MemoryRecord:
        """Update a memory owned by one user scope."""
        if source is not None and source not in {"explicit", "implicit"}:
            raise ValueError("source must be explicit or implicit")
        if expected_revision is not None and (
            not isinstance(expected_revision, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_revision)
        ):
            raise ValueError("expected_revision is invalid")
        if clear_fields is None:
            clear: set[str] = set()
        elif isinstance(clear_fields, (str, bytes)) or not isinstance(
            clear_fields, Sequence
        ):
            raise ValueError("clear_fields must be a list of metadata field names")
        else:
            if not all(isinstance(field, str) for field in clear_fields):
                raise ValueError("clear_fields must be a list of metadata field names")
            clear = set(clear_fields)
        allowed_clear = {"subject", "key", "valid_from"}
        if not clear <= allowed_clear:
            raise ValueError("clear_fields may contain subject, key, or valid_from")
        supplied_metadata = {
            "subject": subject,
            "key": key,
            "valid_from": valid_from,
        }
        if any(
            field in clear and supplied_metadata[field] is not None for field in clear
        ):
            raise ValueError("a metadata field cannot be updated and cleared together")

        async with async_storage_lock(self._storage, self._lock):
            self._ensure_initialized()
            current = self._owned_memory(user_id, memory_id)
            if (
                expected_revision is not None
                and memory_revision(current) != expected_revision
            ):
                raise ValueError(
                    "memory changed since it was loaded; reopen it before saving"
                )
            target_user_id = target_user_id or user_id
            new_content = (
                _clean_content(content) if content is not None else current.content
            )
            new_category = (
                _clean_category(category) if category is not None else current.category
            )
            effective_source = source or current.source
            new_key = (
                None
                if "key" in clear
                else _clean_key(key)
                if key is not None
                else current.key
            )
            timestamp = dt_util.utcnow().isoformat()
            changes: dict[str, Any] = {
                "user_id": target_user_id,
                "content": new_content,
                "category": new_category,
                "source": effective_source,
                "subject": (
                    None
                    if "subject" in clear
                    else _clean_optional(subject, "subject", MAX_SUBJECT_LENGTH)
                    if subject is not None
                    else current.subject
                ),
                "key": new_key,
                "valid_from": (
                    None
                    if "valid_from" in clear
                    else _clean_timestamp(valid_from, "valid_from")
                    if valid_from is not None
                    else current.valid_from
                ),
            }
            _validate_privacy_fields(
                effective_source, new_content, new_category, changes["subject"], new_key
            )
            if current.source == "explicit" and source == "implicit":
                if any(
                    value != getattr(current, field)
                    for field, value in changes.items()
                    if field != "source"
                ):
                    raise ValueError(
                        "implicit updates cannot replace an explicitly confirmed memory"
                    )
                return current
            _set_updated_at_if_substantive(current, changes, timestamp)
            updated = self._replace_record(current, **changes)
            await self._async_save_locked()
            return updated

    async def async_delete(self, user_id: str, memory_ids: list[str]) -> int:
        """Delete selected memories owned by one user scope."""
        if not memory_ids or len(memory_ids) > MAX_SEARCH_LIMIT:
            raise ValueError(f"memory_ids must contain 1 to {MAX_SEARCH_LIMIT} IDs")
        async with async_storage_lock(self._storage, self._lock):
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
        async with async_storage_lock(self._storage, self._lock):
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
        async with async_storage_lock(self._storage, self._lock):
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
        memories = self._published_memories()
        return {
            "backend": "home_assistant_store",
            "storage_version": STORAGE_VERSION,
            "memory_count": len(memories),
            "user_scope_count": len({m.user_id for m in memories.values()}),
            "hybrid_retrieval": self.hybrid_status(),
        }

    def scope_counts(self) -> dict[str, int]:
        """Return memory totals grouped by their exact storage owner."""
        self._ensure_initialized()
        counts: dict[str, int] = {}
        for memory in self._published_memories().values():
            counts[memory.user_id] = counts.get(memory.user_id, 0) + 1
        return counts

    def _published_memories(self) -> dict[str, MemoryRecord]:
        """Use committed facts for synchronous availability/owner diagnostics."""
        self._ensure_initialized()
        assert self._committed_state is not None
        return self._committed_state.memories

    async def async_backup_data(self) -> dict[str, Any]:
        """Return the stable durable representation used by full backups."""
        async with async_storage_lock(self._storage, self._lock):
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
        keys: set[tuple[str, str]] = set()
        for raw in raw_memories:
            record = _validate_persistent_memory_record(raw)
            if record.memory_id in seen:
                raise ValueError("persistent memory metadata is invalid")
            seen.add(record.memory_id)
            if record.key is not None:
                pair = (record.user_id, record.key)
                if pair in keys:
                    raise ValueError("duplicate canonical key in memory scope")
                keys.add(pair)
            records.append(record)
        return records

    async def async_replace_backup(self, records: list[MemoryRecord]) -> None:
        """Atomically replace all persistent memories with validated records."""
        seen_ids: set[str] = set()
        seen_keys: set[tuple[str, str]] = set()
        for record in records:
            if record.memory_id in seen_ids:
                raise ValueError("persistent memory metadata is invalid")
            seen_ids.add(record.memory_id)
            if record.key is not None:
                pair = (record.user_id, record.key)
                if pair in seen_keys:
                    raise ValueError("duplicate canonical key in memory scope")
                seen_keys.add(pair)
        async with async_storage_lock(self._storage, self._lock):
            self._ensure_initialized()
            self._memories = {record.memory_id: record for record in records}
            self._embedding_cache.clear()
            self._embedding_cache_dirty = True
            self._key_index.clear()
            for record in records:
                self._index(record)
            await self._async_save_locked()

    def _find_duplicate(self, user_id: str, content: str) -> MemoryRecord | None:
        """Deduplicate only order-preserving normalized equality.

        Word overlap identifies related facts in _find_related_candidate; it
        cannot establish equivalence (preferences, negation and numbers matter).
        """
        normalized = _fact_equality(content)
        for memory in self._memories.values():
            if memory.user_id != user_id:
                continue
            if _fact_equality(memory.content) == normalized:
                return memory
        return None

    def _find_related_candidate(
        self, user_id: str, content: str, subject: str | None, key: str | None
    ) -> MemoryRecord | None:
        incoming = _cached_memory_tokens(content)
        key_root = key.rsplit(".", 1)[0] if key and "." in key else None
        best: tuple[float, str, MemoryRecord] | None = None
        for memory in self._memories.values():
            if memory.user_id != user_id:
                continue
            existing = _cached_memory_tokens(memory.content)
            union = incoming | existing
            similarity = len(incoming & existing) / len(union) if union else 0.0
            if (
                subject
                and memory.subject
                and _normalize(subject) == _normalize(memory.subject)
            ) or (subject and _cached_memory_tokens(subject) & existing):
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

    def _assert_key_available(self, memory: MemoryRecord) -> None:
        """Reject a key collision before any derived index is mutated."""
        if memory.key is None:
            return
        existing = self._key_index.get((memory.user_id, memory.key))
        if existing is not None and existing != memory.memory_id:
            raise ValueError("canonical key already exists in this memory scope")

    def _index(self, memory: MemoryRecord) -> None:
        self._assert_key_available(memory)
        if memory.key:
            self._key_index[(memory.user_id, memory.key)] = memory.memory_id

    def _unindex(self, memory: MemoryRecord) -> None:
        if memory.key:
            pair = (memory.user_id, memory.key)
            if self._key_index.get(pair) == memory.memory_id:
                self._key_index.pop(pair, None)

    def _replace_record(self, current: MemoryRecord, **changes: Any) -> MemoryRecord:
        updated = replace(current, **changes)
        self._assert_key_available(updated)
        embedding_changed = _embedding_fingerprint(updated) != _embedding_fingerprint(
            current
        )
        if updated == current:
            return current
        self._unindex(current)
        self._memories[current.memory_id] = updated
        self._index(updated)
        if embedding_changed:
            self._invalidate_cached_embedding(current.memory_id)
        return updated

    def _snapshot_mutation_state(self) -> _MemoryMutationSnapshot:
        """Capture live structures so a rejected durable write can be rolled back."""
        return _MemoryMutationSnapshot(
            memories=dict(self._memories),
            key_index=dict(self._key_index),
            embedding_cache={
                memory_id: EmbeddingCacheEntry(
                    model=entry.model,
                    fingerprint=entry.fingerprint,
                    vector=list(entry.vector),
                    space_id=entry.space_id,
                )
                for memory_id, entry in self._embedding_cache.items()
            },
            embedding_cache_dirty=self._embedding_cache_dirty,
        )

    def _restore_mutation_state(self, snapshot: _MemoryMutationSnapshot) -> None:
        """Restore the last successfully committed live structures."""
        self._memories = dict(snapshot.memories)
        self._key_index = dict(snapshot.key_index)
        self._embedding_cache = {
            memory_id: EmbeddingCacheEntry(
                model=entry.model,
                fingerprint=entry.fingerprint,
                vector=list(entry.vector),
                space_id=entry.space_id,
            )
            for memory_id, entry in snapshot.embedding_cache.items()
        }
        self._embedding_cache_dirty = snapshot.embedding_cache_dirty

    async def _async_refresh_missing_embeddings(self, scope_ids: Sequence[str]) -> bool:
        provider = self._embedding_provider
        if provider is None:
            return False
        model = self._embedding_model
        space_id = self._embedding_space_id
        allowed_scopes = set(scope_ids)
        async with async_storage_lock(self._storage, self._lock):
            self._ensure_initialized()
            missing = [
                memory
                for memory in self._memories.values()
                if memory.user_id in allowed_scopes
                and self._cached_embedding(memory) is None
            ]
        for offset in range(0, len(missing), EMBEDDING_CACHE_BATCH_SIZE):
            batch = missing[offset : offset + EMBEDDING_CACHE_BATCH_SIZE]
            vectors = await provider([_embedding_text(memory) for memory in batch])
            if len(vectors) != len(batch):
                raise ValueError(
                    "embedding provider returned the wrong number of vectors"
                )
            batch_generated = False
            async with async_storage_lock(self._storage, self._lock):
                if (
                    self._embedding_provider is not provider
                    or self._embedding_model != model
                    or self._embedding_space_id != space_id
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
                        space_id=space_id,
                        fingerprint=_embedding_fingerprint(memory),
                        vector=_clean_embedding(vector),
                    )
                    batch_generated = True
                if not batch_generated:
                    continue
                self._embedding_cache_dirty = True
                if not await self._async_save_embedding_cache_locked():
                    return False
        return True

    async def _async_save_locked(self) -> None:
        """Settle writes and rebuild memory indexes from authoritative storage."""
        await _async_settle_transactional_save(
            self._async_persist_locked(),
            self._restore_committed_state,
            self._remember_committed_state,
            self._async_reconcile_failed_save,
            self._invalidate_after_unreadable_store,
        )

    async def _async_reconcile_failed_save(self) -> None:
        """Reload validated facts and rebuild indexes after an ambiguous write."""
        disk_state = PersistentMemory(self._storage, self._embedding_cache_storage)
        await disk_state.async_initialize()
        self._restore_mutation_state(disk_state._snapshot_mutation_state())
        self._initialized = True
        self._committed_state = self._snapshot_mutation_state()

    def _invalidate_after_unreadable_store(self) -> None:
        """Block memory operations when persisted facts cannot be validated."""
        self._memories.clear()
        self._key_index.clear()
        self._embedding_cache.clear()
        self._embedding_cache_dirty = False
        self._initialized = False
        self._committed_state = None

    async def _async_persist_locked(self) -> None:
        """Write durable facts and then the regenerable embedding cache."""
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

    def _remember_committed_state(self) -> None:
        self._committed_state = self._snapshot_mutation_state()

    def _restore_committed_state(self) -> None:
        if self._committed_state is not None:
            self._restore_mutation_state(self._committed_state)
            # Embeddings are regenerable; a failed durable mutation invalidates them.
            self._embedding_cache.clear()
            self._embedding_cache_dirty = True

    def _cached_embedding(self, memory: MemoryRecord) -> list[float] | None:
        entry = self._embedding_cache.get(memory.memory_id)
        if (
            entry is None
            or entry.model != self._embedding_model
            or entry.space_id != self._embedding_space_id
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
                    space_id=raw.get("space_id", "")
                    if isinstance(raw.get("space_id", ""), str)
                    else "",
                    vector=_clean_embedding(raw.get("vector")),
                )
        except Exception as err:
            self._embedding_cache.clear()
            log_handled_failure(
                _LOGGER,
                "Persistent memory embedding cache is unavailable; it will regenerate",
                err,
            )

    async def _async_save_embedding_cache_locked(self) -> bool:
        if not self._embedding_cache_dirty:
            return True
        if self._embedding_cache_storage is None:
            self._embedding_cache_dirty = False
            if self._initialized:
                self._committed_state = self._snapshot_mutation_state()
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
            if self._embedding_cache_write_failed:
                _LOGGER.info("Persistent memory embedding cache persistence recovered")
            self._embedding_cache_write_failed = False
            if self._initialized:
                self._committed_state = self._snapshot_mutation_state()
            return True
        except Exception as err:
            if not self._embedding_cache_write_failed:
                log_handled_failure(
                    _LOGGER,
                    "Persistent memory embedding cache could not be saved; using lexical retrieval. "
                    "Memories remain available through word matching, but semantic matches may be missed",
                    err,
                )
            self._embedding_cache_write_failed = True
            return False

    def _set_hybrid_status(
        self,
        status: str,
        reason: str | None = None,
        *,
        error_type: str | None = None,
    ) -> None:
        if (
            status == "active"
            and self._hybrid_status.get("status") == "lexical_fallback"
        ):
            _LOGGER.info(
                "Hybrid memory semantic retrieval recovered model=%s",
                self._embedding_model,
            )
        self._hybrid_status = {
            "configured": self._embedding_provider is not None,
            "status": status,
            "model": self._embedding_model,
            **({"reason": reason} if reason is not None else {}),
            **({"error_type": error_type} if error_type is not None else {}),
        }

    def _ensure_initialized(self) -> None:
        store = getattr(self._storage, "_store", None)
        if isinstance(store, PropagatingWriteStore):
            store.require_available()
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
        "subject": getattr(memory, "subject", None),
        "key": getattr(memory, "key", None),
        "valid_from": getattr(memory, "valid_from", None),
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


def memory_revision(memory: MemoryRecord) -> str:
    """Return a stable optimistic-concurrency token for substantive memory state."""
    payload = json.dumps(
        [
            memory.memory_id,
            memory.user_id,
            memory.content,
            memory.category,
            memory.source,
            memory.subject,
            memory.key,
            memory.valid_from,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


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
                "description": "Create or replace a durable fact using a stable canonical key when available.",
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
                        "query": {"type": "string", "minLength": 1},
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
                "description": "Correct or replace an existing memory. Use explicit only for a user-requested update; use implicit for proactive changes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string"},
                        "source": {"type": "string", "enum": ["explicit", "implicit"]},
                        "content": {"type": "string"},
                        "category": {"type": "string"},
                        **_memory_metadata_schema(include_scope=True),
                        "clear_fields": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["subject", "key", "valid_from"],
                            },
                            "minItems": 1,
                            "maxItems": 3,
                            "description": "Optional metadata fields to clear explicitly.",
                        },
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
        "subject": {"type": "string"},
        "key": {
            "type": "string",
            "maxLength": MAX_KEY_LENGTH,
            "pattern": r"^[a-z0-9_-]+(?:\.[a-z0-9_-]+)*$",
            "description": (
                "Optional stable lowercase identifier such as pet.oscar.breed. "
                "Use only a-z, 0-9, underscore, hyphen, and dot-separated components."
            ),
        },
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
        for token in _TOKEN_PATTERN.findall(
            unicodedata.normalize("NFC", value).casefold()
        )
        if len(token) > 1 and token not in _STOP_WORDS
    ]


def _tokens(value: str) -> set[str]:
    """Return fresh mutable tokens from the immutable lexical cache."""
    return set(_cached_memory_tokens(value))


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


@lru_cache(maxsize=20_000)
def _cached_memory_record_terms(memory: MemoryRecord) -> tuple[str, ...]:
    return tuple(
        _token_list(
            " ".join(
                filter(
                    None, (memory.content, memory.category, memory.subject, memory.key)
                )
            )
        )
    )


@lru_cache(maxsize=20_000)
def _cached_memory_record_token_set(memory: MemoryRecord) -> frozenset[str]:
    """Return the immutable token membership set for one memory revision."""
    return frozenset(_cached_memory_record_terms(memory))


def _bm25_score(
    query_terms: list[str],
    document_terms: Sequence[str],
    document_frequency: Mapping[str, int],
    document_count: int,
    average_length: float,
) -> float:
    """Calculate the existing BM25 score without rebuilding term frequencies."""
    cached_terms = (
        document_terms if isinstance(document_terms, tuple) else tuple(document_terms)
    )
    frequencies = _cached_memory_term_frequencies(cached_terms)
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
    category = _cached_memory_tokens(memory.category)
    subject = _cached_memory_tokens(memory.subject or "")
    key = _cached_memory_tokens((memory.key or "").replace(".", " "))
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


def _fuzzy_relevance(
    query_tokens: set[str], document_tokens: set[str] | frozenset[str]
) -> float:
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


def _validate_persistent_memory_record(raw: Any) -> MemoryRecord:
    """Validate one stored record using the same contract as backup restore."""
    if not isinstance(raw, Mapping):
        raise ValueError("persistent memory record must be an object")
    try:
        record = MemoryRecord(**_migrate_raw_record(raw))
    except (TypeError, ValueError) as err:
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
        )
    ):
        raise ValueError("persistent memory fields must be strings")
    if (
        not record.memory_id
        or len(record.memory_id) > 128
        or not record.user_id
        or len(record.user_id) > 128
        or record.source not in {"explicit", "implicit"}
    ):
        raise ValueError("persistent memory metadata is invalid")
    _clean_content(record.content)
    _clean_category(record.category)
    _clean_optional(record.subject, "subject", MAX_SUBJECT_LENGTH)
    _clean_key(record.key)
    _validate_privacy_fields(
        record.source, record.content, record.category, record.subject, record.key
    )
    if (
        dt_util.parse_datetime(record.created_at) is None
        or dt_util.parse_datetime(record.updated_at) is None
    ):
        raise ValueError("persistent memory timestamp is invalid")
    for value in (record.valid_from,):
        if value is not None and (
            not isinstance(value, str) or dt_util.parse_datetime(value) is None
        ):
            raise ValueError("persistent memory timestamp is invalid")
    return record


def _migrate_raw_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(raw)
    result.pop("importance", None)
    result.pop("last_confirmed_at", None)
    result.setdefault("subject", None)
    result.setdefault("key", None)
    result.setdefault("valid_from", None)
    result.pop("embedding", None)
    return result


def _fact_equality(value: str) -> str:
    """Fold case and whitespace without erasing meaning-bearing punctuation."""
    return " ".join(unicodedata.normalize("NFC", value).casefold().split()).rstrip(
        ".!?"
    )


@lru_cache(maxsize=20_000)
def _normalize(value: str) -> str:
    return " ".join(
        _TOKEN_PATTERN.findall(unicodedata.normalize("NFC", value).casefold())
    )


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
    """Validate a stable memory identifier without lossy normalization."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("key must be a string")
    if len(value) > MAX_KEY_LENGTH:
        raise ValueError(f"key must be at most {MAX_KEY_LENGTH} characters")
    if not _MEMORY_KEY_PATTERN.fullmatch(value):
        raise ValueError(
            "key must be a lowercase dot-separated identifier using only a-z, "
            "0-9, underscore, and hyphen (for example pet.oscar.breed)"
        )
    return value


def _clean_timestamp(value: str | None, field: str) -> str | None:
    cleaned = _clean_optional(value, field, 64)
    if cleaned is not None and dt_util.parse_datetime(cleaned) is None:
        raise ValueError(f"{field} must be an ISO 8601 timestamp")
    return cleaned


def _set_updated_at_if_substantive(
    current: MemoryRecord, changes: dict[str, Any], timestamp: str
) -> None:
    substantive = {
        "user_id",
        "content",
        "category",
        "source",
        "subject",
        "key",
        "valid_from",
    }
    if any(
        field in changes and changes[field] != getattr(current, field)
        for field in substantive
    ):
        changes["updated_at"] = timestamp


def _validate_privacy_fields(source: str, *values: object) -> None:
    """Treat retained metadata as untrusted text under the same write policy."""
    for value in values:
        if isinstance(value, str):
            _validate_privacy(value, source)


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


@lru_cache(maxsize=20_000)
def _cached_memory_tokens(value: str) -> frozenset[str]:
    return frozenset(_token_list(value))


@lru_cache(maxsize=20_000)
def _cached_memory_term_frequencies(
    document_terms: tuple[str, ...],
) -> Mapping[str, int]:
    """Build one immutable frequency map per distinct tokenized memory document."""
    frequencies: dict[str, int] = {}
    for term in document_terms:
        frequencies[term] = frequencies.get(term, 0) + 1
    return MappingProxyType(frequencies)
