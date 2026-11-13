"""Local persistent memory for conversation agents."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from functools import lru_cache
import hashlib
import json
import logging
import math
import re
from time import monotonic
from types import MappingProxyType
from typing import Any, Protocol
import unicodedata
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .agent_maintenance import get_agent_maintenance_gate
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
from .operation_settlement import async_settle_owned_operation
from .operational_errors import log_handled_failure
from .persistence_hardening import (
    StorageRepairRequired,
    _async_settle_transactional_save,
)
from .provider_errors import provider_failure_category, provider_log_remediation
from .retrieval_text import _stem, bounded_phrase, ordered_term_phrase
from .scope import LEGACY_ANONYMOUS_SCOPE_ID
from .storage_namespaces import MEMORY_PREFIX as STORAGE_KEY_PREFIX
from .strict_store import PropagatingWriteStore, async_storage_lock

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 2

EMBEDDING_CACHE_VERSION = 1
EMBEDDING_CACHE_BATCH_SIZE = 64
HYBRID_RETRY_INITIAL_SECONDS = 30.0
HYBRID_RETRY_MAX_SECONDS = 300.0
EMBEDDING_CACHE_RETRY_SECONDS = 60
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

# Query-only, automatic-recall hint. Do not change stored tokens, explicit
# memory searches or the semantic threshold. These words supply conversational
# framing rather than distinguishing a fact; retain modal/control/negation
# words, numbers, names and identifiers at their original weight.
_AUTOMATIC_MEMORY_FILLER_TERMS = frozenset(
    _stem(word)
    for word in (
        "hey",
        "hi",
        "please",
        "kindly",
        "tell",
        "me",
        "you",
        "which",
        "perhaps",
        "maybe",
        "just",
        "curious",
        "wondering",
        "know",
        "recall",
        "remind",
        "remember",
        "about",
        "thanks",
    )
)


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
    vector: Sequence[float]
    space_id: str = ""

    def __post_init__(self) -> None:
        # Frozen entries own immutable vectors; snapshots can safely share them.
        object.__setattr__(self, "vector", tuple(self.vector))


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
        if old_major_version == 0 and isinstance(old_data, list):
            old_data = {"memories": old_data}
        if old_major_version in {0, 1} and isinstance(old_data, dict):
            # The shared record decoder already accepts legacy fields. Defer
            # lossy cleanup until the manager has validated the whole generation.
            return dict(old_data)
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
        # Register cleanup ownership without changing best-effort cache I/O.
        get_agent_maintenance_gate(hass, entry_id, subentry_id).register_store(
            self._store
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
        self._embedding_retry_identity: object = None
        self._hybrid_retry_at = 0.0
        self._hybrid_retry_delay = 0.0
        self._embedding_cache_retry_at = 0.0
        self._hybrid_status: dict[str, Any] = {
            "configured": False,
            "status": "lexical_fallback",
            "model": "default",
            "reason": "provider_not_configured",
        }
        self._lock = asyncio.Lock()
        self._initialized = False
        self._repair_required = False
        self._committed_state: _MemoryMutationSnapshot | None = None

    @property
    def initialized(self) -> bool:
        """Return whether authoritative stored facts are ready for use."""
        return self._initialized

    async def async_initialize(self) -> None:
        """Load usable facts; normalize legacy data without discarding damage."""
        async with async_storage_lock(self._storage, self._lock):
            if self._initialized:
                return
            try:
                data = await self._storage.async_load()
                memories, key_index, payload, repair_required = await asyncio.to_thread(
                    _prepare_stored_memories, data
                )
                self._repair_required = repair_required
                if payload is not None:
                    await self._storage.async_save(payload)
                self._memories = memories
                self._key_index = key_index
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
        retry_identity: object = None,
    ) -> None:
        """Configure the optional provider used only by hybrid retrieval."""
        # This runtime-only identity must never enter vectors or diagnostics.
        model = str(model).strip() or "default"
        identity = (
            model,
            space_id,
            retry_identity if retry_identity is not None else provider,
        )
        if identity == self._embedding_retry_identity:
            # Equality identifies equivalent wrappers. Retain their object identity
            # so a repeated sync cannot retire a valid in-flight retrieval. A new
            # entity/callback must still bind even when its retry identity matches.
            if self._embedding_provider != provider:
                self._embedding_provider = provider
            return
        self._embedding_retry_identity = identity
        self._hybrid_retry_at = self._hybrid_retry_delay = 0.0
        self._embedding_provider = provider
        self._embedding_model = model
        self._embedding_space_id = space_id
        self._set_hybrid_status(
            "ready" if provider is not None else "lexical_fallback",
            None if provider is not None else "provider_not_configured",
        )

    def hybrid_status(self) -> dict[str, Any]:
        """Return non-sensitive hybrid-retrieval availability diagnostics."""
        return {
            **self._hybrid_status,
            "retry_after_seconds": max(
                0, math.ceil(self._hybrid_retry_at - monotonic())
            ),
        }

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
        status, record = await self.async_add_record(
            user_id, content, category, source, subject, key, valid_from
        )
        return {"status": status, "memory": memory_as_dict(record)}

    async def async_add_record(
        self,
        user_id: str,
        content: str,
        category: str,
        source: str,
        subject: str | None = None,
        key: str | None = None,
        valid_from: str | None = None,
    ) -> tuple[str, MemoryRecord]:
        """Return the committed immutable record without requiring a second read."""
        content = _clean_content(content)
        category = _clean_category(category)
        subject = _clean_optional(subject, "subject", MAX_SUBJECT_LENGTH)
        key = _clean_key(key)
        valid_from = _clean_timestamp(valid_from, "valid_from")
        if source not in {"explicit", "implicit"}:
            raise ValueError("source must be explicit or implicit")
        async with async_storage_lock(self._storage, self._lock):
            self._require_writable()
            if key and (user_id, key) in self._key_index:
                raise ValueError("canonical key already exists in this memory scope")
            if key is not None:
                # A key may identify one existing legacy fact, but must never
                # reassign the identity of an already keyed record.
                duplicate = self._find_unique_unkeyed_duplicate(
                    user_id, content, subject, valid_from
                )
                if duplicate is not None:
                    if duplicate.category != category:
                        raise ValueError(
                            "Existing memory conflicts with requested category; "
                            "no changes made. Open the existing memory and use Update "
                            "to intentionally edit its metadata before assigning a key."
                        )
                    if duplicate.source == "explicit" and source == "implicit":
                        return "duplicate", duplicate
                    promotion_changes: dict[str, Any] = {"key": key}
                    if duplicate.source == "implicit" and source == "explicit":
                        promotion_changes["source"] = "explicit"
                    if subject and not duplicate.subject:
                        promotion_changes["subject"] = subject
                    if valid_from is not None and duplicate.valid_from is None:
                        promotion_changes["valid_from"] = valid_from
                    timestamp = dt_util.utcnow().isoformat()
                    _set_updated_at_if_substantive(
                        duplicate, promotion_changes, timestamp
                    )
                    updated = self._replace_record(duplicate, **promotion_changes)
                    await self._async_save_locked()
                    return "updated", updated
            if key is None:
                duplicate = self._find_duplicate(user_id, content, subject)
                if duplicate:
                    conflict = None
                    if duplicate.key is not None:
                        conflict = "canonical keyed identity"
                    elif duplicate.category != category:
                        conflict = "category"
                    elif (
                        valid_from is not None
                        and duplicate.valid_from is not None
                        and duplicate.valid_from != valid_from
                    ):
                        conflict = "valid_from"
                    elif (
                        duplicate.source == "explicit"
                        and source == "implicit"
                        and (
                            (subject and not duplicate.subject)
                            or (valid_from is not None and duplicate.valid_from is None)
                        )
                    ):
                        conflict = "explicit-memory metadata protection"
                    if conflict:
                        raise ValueError(
                            f"Existing memory conflicts with requested {conflict}; "
                            "no changes made. Open the existing memory and use Update "
                            "to intentionally edit its metadata."
                        )
                    changes: dict[str, Any] = {}
                    if duplicate.source == "implicit" and source == "explicit":
                        changes["source"] = "explicit"
                    if (
                        subject
                        and not duplicate.subject
                        and (source == "explicit" or duplicate.source == "implicit")
                    ):
                        changes["subject"] = subject
                    if valid_from is not None and duplicate.valid_from is None:
                        changes["valid_from"] = valid_from
                    if changes:
                        timestamp = dt_util.utcnow().isoformat()
                        _set_updated_at_if_substantive(duplicate, changes, timestamp)
                        duplicate = self._replace_record(duplicate, **changes)
                        await self._async_save_locked()
                        return "updated", duplicate
                    return "duplicate", duplicate
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
            return "created", memory

    async def async_propose(
        self,
        user_id: str,
        content: str,
        category: str,
        source: str,
        subject: str | _UnsetType | None = _UNSET,
        key: str | _UnsetType | None = _UNSET,
        valid_from: str | _UnsetType | None = _UNSET,
    ) -> dict[str, Any]:
        """Model-origin create/no-op/conflict path; never edit an existing record.

        The model's requested source is descriptive provenance only. It cannot
        establish permission to mutate existing state or promote its provenance.
        """
        content = _clean_content(content)
        category = _clean_category(category)
        if source not in {"explicit", "implicit"}:
            raise ValueError("source must be explicit or implicit")
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

        async with async_storage_lock(self._storage, self._lock):
            self._require_writable()
            candidate: MemoryRecord | None = None
            reason = "exact_duplicate"
            ambiguous = False
            if isinstance(cleaned_key, str):
                memory_id = self._key_index.get((user_id, cleaned_key))
                if memory_id is not None:
                    # A canonical key identifies an exact record, but cannot
                    # authorize replacing it, regardless of text similarity.
                    candidate = self._memories[memory_id]
                    reason = "key_collision"

            if candidate is None:
                # Literal equality is a deterministic duplicate check, not
                # semantic classification. Keep subjects and temporal identity
                # distinct; leave paraphrases and contradictions to the model.
                matches = self._find_exact_proposal_candidates(
                    user_id,
                    content,
                    cleaned_subject if isinstance(cleaned_subject, str) else None,
                    cleaned_valid_from if isinstance(cleaned_valid_from, str) else None,
                )
                if matches:
                    candidate = matches[0]
                    ambiguous = len(matches) != 1
                    if ambiguous:
                        reason = "ambiguous_exact_duplicate"
                    elif isinstance(cleaned_key, str) and candidate.key is None:
                        reason = "unkeyed_key_promotion"
                    elif cleaned_key is _UNSET:
                        reason = "duplicate_metadata"

            if candidate is not None:
                same = (
                    _fact_equality(candidate.content) == _fact_equality(content)
                    and candidate.category == category
                    # Source describes the proposal, not permission to change
                    # stored provenance. A source-only difference is a no-op.
                    and (
                        cleaned_subject is _UNSET
                        or candidate.subject == cleaned_subject
                    )
                    and (
                        cleaned_valid_from is _UNSET
                        or candidate.valid_from == cleaned_valid_from
                    )
                    and (cleaned_key is _UNSET or candidate.key == cleaned_key)
                )
                # An unkeyed proposal must not silently take the identity of a
                # differently keyed record; an identical text alone is not proof
                # that two canonical identities are interchangeable.
                if (
                    not ambiguous
                    and same
                    and (cleaned_key is not _UNSET or candidate.key is None)
                ):
                    return {
                        "status": "unchanged",
                        "memory": memory_as_dict(candidate, include_revision=True),
                    }
                return {
                    "status": "needs_resolution",
                    "reason": reason,
                    "applied": False,
                    "candidate": memory_as_dict(candidate, include_revision=True),
                }

            if len(self._memories) >= MAX_MEMORIES_PER_AGENT:
                raise ValueError(
                    "memory limit reached; delete memories before adding more"
                )
            timestamp = dt_util.utcnow().isoformat()
            new_record = MemoryRecord(
                memory_id=uuid4().hex,
                user_id=user_id,
                content=content,
                category=category,
                source=source,
                created_at=timestamp,
                updated_at=timestamp,
                subject=cleaned_subject if isinstance(cleaned_subject, str) else None,
                key=cleaned_key if isinstance(cleaned_key, str) else None,
                valid_from=(
                    cleaned_valid_from if isinstance(cleaned_valid_from, str) else None
                ),
            )
            self._assert_key_available(new_record)
            self._memories[new_record.memory_id] = new_record
            self._index(new_record)
            await self._async_save_locked()
            return {
                "status": "created",
                "memory": memory_as_dict(new_record, include_revision=True),
            }

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
        allow_fuzzy_only: bool = True,
        automatic_recall: bool = False,
    ) -> list[MemoryRecord]:
        """Return deterministic BM25-style lexical or hybrid results."""
        limit = max(1, min(limit, MAX_LIST_LIMIT))
        offset = max(0, offset)
        category_filter = _clean_category(category) if category else None
        scope_ids = {user_id} if isinstance(user_id, str) else set(user_id)
        async with async_storage_lock(self._storage, self._lock):
            self._ensure_initialized()
            corpus = tuple(
                memory
                for memory in self._memories.values()
                if memory.user_id in scope_ids
                and (category_filter is None or memory.category == category_filter)
            )
            cache = dict(self._embedding_cache) if hybrid else {}
            model, space = self._embedding_model, self._embedding_space_id
        ranked = await asyncio.to_thread(
            _rank_memory_snapshot,
            corpus,
            query,
            limit,
            offset,
            tuple(query_embedding) if query_embedding is not None else None,
            hybrid,
            cache,
            model,
            space,
            allow_fuzzy_only,
            automatic_recall,
        )
        async with async_storage_lock(self._storage, self._lock):
            self._ensure_initialized()
            # A concurrent delete, reassignment, or edit must not publish stale facts.
            return [
                memory
                for memory in ranked
                if self._memories.get(memory.memory_id) == memory
            ]

    async def async_prepare_hybrid(
        self, scope_ids: Sequence[str], query: str
    ) -> list[float] | None:
        """Prepare embeddings only for searched scopes and return the query vector."""
        if self._embedding_provider is None:
            self._set_hybrid_status("lexical_fallback", "provider_not_configured")
            return None
        if monotonic() < self._hybrid_retry_at:
            return None
        attempt_identity = self._embedding_retry_identity
        attempt_provider = self._embedding_provider
        try:
            if not await self._async_refresh_missing_embeddings(scope_ids):
                self._set_hybrid_status(
                    "lexical_fallback",
                    "embedding_preparation_failed",
                )
                return None
            provider = self._embedding_provider
            model = self._embedding_model
            space_id = self._embedding_space_id
            if provider is None:
                self._set_hybrid_status("lexical_fallback", "provider_changed")
                return None
            vectors = await provider([query])
            if (
                self._embedding_provider is not provider
                or self._embedding_model != model
                or self._embedding_space_id != space_id
                or self._embedding_retry_identity != attempt_identity
            ):
                self._set_hybrid_status("lexical_fallback", "provider_changed")
                return None
            if len(vectors) != 1:
                raise ValueError(
                    "embedding provider returned the wrong number of vectors"
                )
            vector = _clean_embedding(vectors[0])
            self._hybrid_retry_at = self._hybrid_retry_delay = 0.0
            self._set_hybrid_status(
                "active",
                "embedding_cache_not_persisted"
                if self._embedding_cache_write_failed and self._embedding_cache_dirty
                else None,
            )
            return vector
        except Exception as err:
            if (
                attempt_identity != self._embedding_retry_identity
                or attempt_provider is not self._embedding_provider
            ):
                return None
            self._hybrid_retry_delay = min(
                HYBRID_RETRY_MAX_SECONDS,
                max(HYBRID_RETRY_INITIAL_SECONDS, self._hybrid_retry_delay * 2),
            )
            self._hybrid_retry_at = monotonic() + self._hybrid_retry_delay
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
            memories.sort(
                key=lambda memory: _updated_at_instant(memory.updated_at), reverse=True
            )
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
            memories.sort(
                key=lambda memory: _updated_at_instant(memory.updated_at), reverse=True
            )
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
                        for value in (
                            memory.content,
                            memory.category,
                            memory.source,
                            memory.subject,
                            memory.key,
                        )
                    ),
                ).casefold()
            ]
            memories.sort(
                key=lambda memory: _updated_at_instant(memory.updated_at), reverse=True
            )
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
        allow_explicit_edit: bool = True,
        model_origin: bool = False,
    ) -> MemoryRecord:
        """Revision-checked update; model writes preserve ownership and provenance."""
        if model_origin and expected_revision is None:
            raise ValueError("model-origin update requires expected_revision")
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
            self._require_writable()
            current = self._owned_memory(user_id, memory_id)
            if not allow_explicit_edit and current.source == "explicit":
                raise ValueError(
                    "explicit memories require a direct user request or authenticated management editing"
                )
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
            # Model-supplied origin cannot promote or demote existing records.
            effective_source = (
                current.source if model_origin else (source or current.source)
            )
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
            if (
                not model_origin
                and current.source == "explicit"
                and source == "implicit"
            ):
                if any(
                    value != getattr(current, field)
                    for field, value in changes.items()
                    if field != "source"
                ):
                    raise ValueError(
                        "implicit updates cannot replace an explicitly confirmed memory"
                    )
                changes["source"] = current.source
            if all(
                value == getattr(current, field) for field, value in changes.items()
            ):
                # Validation and recovery barriers still apply to no-ops. Only
                # the regenerable cache may need an independently settled retry.
                await async_settle_owned_operation(
                    self._async_save_embedding_cache_locked()
                )
                return current
            _set_updated_at_if_substantive(current, changes, timestamp)
            updated = self._replace_record(current, **changes)
            await self._async_save_locked()
            return updated

    async def async_delete(
        self,
        user_id: str,
        memory_ids: list[str],
        *,
        expected_revisions: Mapping[str, str] | None = None,
        allow_explicit_delete: bool = True,
    ) -> int:
        """Delete scoped records, optionally validating an atomic guarded batch.

        Legacy authenticated Management calls retain their established
        best-effort missing-ID behaviour. Model-origin calls must supply a
        complete revision map; every requested target is validated under the
        lock before any record/index/cache mutation occurs.
        """
        if (
            not isinstance(memory_ids, list)
            or not memory_ids
            or len(memory_ids) > MAX_SEARCH_LIMIT
        ):
            raise ValueError(f"memory_ids must contain 1 to {MAX_SEARCH_LIMIT} IDs")
        if not all(isinstance(identity, str) and identity for identity in memory_ids):
            raise ValueError("memory_ids must contain non-empty strings")
        if expected_revisions is not None:
            if (
                not isinstance(expected_revisions, Mapping)
                or len(set(memory_ids)) != len(memory_ids)
                or set(expected_revisions) != set(memory_ids)
                or not all(
                    isinstance(revision, str)
                    and re.fullmatch(r"[0-9a-f]{64}", revision)
                    for revision in expected_revisions.values()
                )
            ):
                raise ValueError("expected_revisions must cover unique memory IDs")
        elif not allow_explicit_delete:
            raise ValueError("model-origin deletion requires expected_revisions")

        async with async_storage_lock(self._storage, self._lock):
            self._require_writable()
            if expected_revisions is not None:
                for memory_id in memory_ids:
                    current = self._memories.get(memory_id)
                    if current is None or current.user_id != user_id:
                        raise ValueError("memory not found or changed; search again")
                    if not allow_explicit_delete and current.source == "explicit":
                        raise ValueError(
                            "explicit memories require a direct user request or authenticated management deletion"
                        )
                    if memory_revision(current) != expected_revisions[memory_id]:
                        raise ValueError(
                            "memory changed since it was loaded; search again"
                        )
            deleted = 0
            for memory_id in set(memory_ids):
                current = self._memories.get(memory_id)
                if current is None or current.user_id != user_id:
                    continue
                self._unindex(current)
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
            self._require_writable()
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
            self._require_writable()
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
            "memory_storage_repair_required": self._repair_required,
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
            self._require_writable()
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
            # Retain only exact local facts with valid fingerprints. Provider/model
            # space is checked at use time, including after cold recovery binds it.
            retained_cache = {
                record.memory_id: entry
                for record in records
                if self._memories.get(record.memory_id) == record
                and (entry := self._embedding_cache.get(record.memory_id)) is not None
                and entry.fingerprint == _embedding_fingerprint(record)
            }
            self._memories = {record.memory_id: record for record in records}
            self._embedding_cache = retained_cache
            self._embedding_cache_dirty = True
            self._key_index.clear()
            for record in records:
                self._index(record)
            await self._async_save_locked(replacing=True)

    def _find_duplicate(
        self, user_id: str, content: str, subject: str | None = None
    ) -> MemoryRecord | None:
        """Deduplicate only order-preserving normalized equality.

        Word overlap cannot establish equivalence: preferences, negation and
        numbers matter.
        """
        normalized = _fact_equality(content)
        matches = [
            memory
            for memory in self._memories.values()
            if memory.user_id == user_id
            and _fact_equality(memory.content) == normalized
            and (
                not subject
                or not memory.subject
                or _subject_identity(memory.subject) == _subject_identity(subject)
            )
        ]
        # Missing subject metadata is compatible, not evidence that a candidate
        # can be excluded. Never let dictionary order choose an existing identity.
        if len(matches) > 1:
            raise ValueError(
                "Multiple existing memories could match this addition; no changes made. "
                "Open the intended memory and use Update to edit its metadata."
            )
        return matches[0] if matches else None

    def _find_unique_unkeyed_duplicate(
        self,
        user_id: str,
        content: str,
        subject: str | None = None,
        valid_from: str | None = None,
    ) -> MemoryRecord | None:
        """Find one unkeyed exact fact without crossing canonical identities.

        Multiple unkeyed facts with identical text remain ambiguous even when
        one subject matches; leave them untouched for explicit resolution.
        """
        normalized = _fact_equality(content)
        matches = [
            memory
            for memory in self._memories.values()
            if memory.user_id == user_id
            and memory.key is None
            and _fact_equality(memory.content) == normalized
        ]
        if len(matches) != 1:
            return None
        candidate = matches[0]
        if (
            subject
            and candidate.subject
            and _subject_identity(subject) != _subject_identity(candidate.subject)
        ):
            return None
        if (
            valid_from is not None
            and candidate.valid_from is not None
            and valid_from != candidate.valid_from
        ):
            return None
        return candidate

    def _find_exact_proposal_candidates(
        self,
        user_id: str,
        content: str,
        subject: str | None,
        valid_from: str | None,
    ) -> list[MemoryRecord]:
        """Find literal model-proposal duplicates without semantic inference.

        A different explicit subject or known validity date establishes distinct
        facts. An omitted field is not proof of a different identity, so retain
        an exact-content conflict rather than silently multiplying records.
        All matches are scoped to this owner; the caller publishes candidate
        data only when read access is independently authorized.
        """
        normalized = _fact_equality(content)
        matches = [
            memory
            for memory in self._memories.values()
            if memory.user_id == user_id
            and _fact_equality(memory.content) == normalized
            and not (
                subject
                and memory.subject
                and _subject_identity(subject) != _subject_identity(memory.subject)
            )
            and not (
                valid_from is not None
                and memory.valid_from is not None
                and valid_from != memory.valid_from
            )
        ]
        return sorted(matches, key=lambda memory: memory.memory_id)

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
            embedding_cache=dict(self._embedding_cache),
            embedding_cache_dirty=self._embedding_cache_dirty,
        )

    def _restore_mutation_state(self, snapshot: _MemoryMutationSnapshot) -> None:
        """Restore the last successfully committed live structures."""
        self._memories = dict(snapshot.memories)
        self._key_index = dict(snapshot.key_index)
        self._embedding_cache = dict(snapshot.embedding_cache)
        self._embedding_cache_dirty = snapshot.embedding_cache_dirty

    async def _async_refresh_missing_embeddings(self, scope_ids: Sequence[str]) -> bool:
        provider = self._embedding_provider
        if provider is None:
            return False
        model = self._embedding_model
        space_id = self._embedding_space_id
        retry_identity = self._embedding_retry_identity
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
                    or self._embedding_retry_identity != retry_identity
                ):
                    return False
                for memory, vector in zip(batch, vectors, strict=True):
                    current = self._memories.get(memory.memory_id)
                    if (
                        current is None
                        or current.user_id != memory.user_id
                        or current.user_id not in allowed_scopes
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
                # Keep the manager lease until its best-effort native write has
                # settled, including when reader retirement cancels this refresh.
                await async_settle_owned_operation(
                    self._async_save_embedding_cache_locked()
                )
        # A warm scope still offers a retry opportunity after the cooldown.
        # Best-effort persistence must never gate valid in-memory vectors.
        async with async_storage_lock(self._storage, self._lock):
            if (
                self._embedding_provider is not provider
                or self._embedding_model != model
                or self._embedding_space_id != space_id
                or self._embedding_retry_identity != retry_identity
            ):
                return False
            await async_settle_owned_operation(
                self._async_save_embedding_cache_locked()
            )
        return True

    async def _async_save_locked(self, *, replacing: bool = False) -> None:
        """Settle writes and rebuild memory indexes from authoritative storage."""
        if self._repair_required and not replacing:
            self._restore_committed_state()
            raise StorageRepairRequired("Persistent Memory")
        await _async_settle_transactional_save(
            self._async_persist_locked(),
            self._restore_committed_state,
            self._remember_committed_state,
            self._async_reconcile_failed_save,
            self._invalidate_after_unreadable_store,
        )
        if replacing:
            self._repair_required = False

    async def _async_reconcile_failed_save(self) -> None:
        """Reload validated facts and rebuild indexes after an ambiguous write."""
        disk_state = PersistentMemory(self._storage, self._embedding_cache_storage)
        await disk_state.async_initialize()
        self._repair_required = disk_state._repair_required
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

    def _cached_embedding(self, memory: MemoryRecord) -> Sequence[float] | None:
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
            self._embedding_cache = await asyncio.to_thread(
                _prepare_stored_embedding_cache, data
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
        if monotonic() < self._embedding_cache_retry_at:
            return False
        if self._embedding_cache_storage is None:
            self._embedding_cache_dirty = False
            if self._initialized:
                self._committed_state = self._snapshot_mutation_state()
            return True
        try:
            payload = await asyncio.to_thread(
                _embedding_cache_payload,
                dict(self._embedding_cache),
                frozenset(self._memories),
            )
            await self._embedding_cache_storage.async_save(payload)
            self._embedding_cache_dirty = False
            if self._embedding_cache_write_failed:
                _LOGGER.info(
                    "Persistent memory embedding cache save no longer reports a failure"
                )
            self._embedding_cache_write_failed = False
            self._embedding_cache_retry_at = 0.0
            if self._initialized:
                self._committed_state = self._snapshot_mutation_state()
            return True
        except Exception as err:
            if not self._embedding_cache_write_failed:
                log_handled_failure(
                    _LOGGER,
                    "Persistent memory embedding cache could not be saved; current in-memory "
                    "vectors remain usable. Cache persistence will retry on demand after a cooldown",
                    err,
                )
            self._embedding_cache_write_failed = True
            self._embedding_cache_retry_at = monotonic() + EMBEDDING_CACHE_RETRY_SECONDS
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

    def _require_writable(self) -> None:
        self._ensure_initialized()
        if self._repair_required:
            raise StorageRepairRequired("Persistent Memory")

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
    include_revision: bool = False,
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
    if include_revision:
        result["revision"] = memory_revision(memory)
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
                            "description": "Descriptive provenance only; use intent for the operation decision.",
                        },
                        **_memory_metadata_schema(include_scope=True),
                        **_memory_intent_schema(),
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
                "description": (
                    "Propose a NEW durable fact; never overwrite or bypass a conflict by changing "
                    "keys. needs_resolution means no write; search and decide what the facts mean."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "category": {"type": "string"},
                        "source": {
                            "type": "string",
                            "enum": ["explicit", "implicit"],
                            "description": (
                                "Descriptive origin only; explicit is not model authorization."
                            ),
                        },
                        "subject": {
                            "type": "string",
                            "description": (
                                "Specific person, device or item the assertion concerns; never infer "
                                "another person's preference."
                            ),
                        },
                        "key": {
                            "type": "string",
                            "maxLength": MAX_KEY_LENGTH,
                            "pattern": r"^[a-z0-9_-]+(?:\.[a-z0-9_-]+)*$",
                            "description": (
                                "Stable canonical identifier for this exact subject; never change it "
                                "to evade a conflict."
                            ),
                        },
                        "valid_from": {
                            "type": "string",
                            "description": "ISO 8601 time when the fact became true, if known.",
                        },
                        "scope": {"type": "string", "enum": ["personal", "household"]},
                        **_memory_intent_schema(),
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
                "description": (
                    "Find relevant facts for answers and before proposing or correcting. Rankings are "
                    "candidates, not semantic matches; absence is not proof of novelty."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "offset": {"type": "integer", "minimum": 0, "default": 0},
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
                "description": "Browse existing facts, their provenance and exact revisions.",
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
                "description": (
                    "Correct a scoped record using its exact ID and current revision. "
                    "Use intent=user_request for a current user correction request, or automatic "
                    "for clear corrections in Automatic mode. Never guess missing fields."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {
                            "type": "string",
                            "description": "Exact ID of the record found in this scope, not a similar record.",
                        },
                        "expected_revision": {
                            "type": "string",
                            "pattern": "^[0-9a-f]{64}$",
                            "description": (
                                "Current revision from memory_search, memory_list or opening context; "
                                "never guess."
                            ),
                        },
                        "source": {
                            "type": "string",
                            "enum": ["explicit", "implicit"],
                            "description": "Legacy descriptive origin only; cannot authorize edits or change provenance.",
                        },
                        "content": {"type": "string"},
                        "category": {"type": "string"},
                        **_memory_metadata_schema(include_scope=True),
                        **_memory_intent_schema(),
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
                    "required": ["memory_id", "expected_revision"],
                    "additionalProperties": False,
                },
            },
            "function": {"type": "memory", "operation": "update"},
        },
        {
            "spec": {
                "name": "memory_delete",
                "description": (
                    "Delete only specifically identified scoped records using their exact "
                    "IDs and fresh revisions. Set intent=user_request for current user requests, "
                    "or automatic for clear obsolete facts in Automatic mode. Confirm before broad deletion."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "targets": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "memory_id": {"type": "string"},
                                    "expected_revision": {
                                        "type": "string",
                                        "pattern": "^[0-9a-f]{64}$",
                                    },
                                },
                                "required": ["memory_id", "expected_revision"],
                                "additionalProperties": False,
                            },
                            "minItems": 1,
                            "maxItems": 50,
                        },
                        "scope": {"type": "string", "enum": ["personal", "household"]},
                        **_memory_intent_schema(),
                    },
                    "required": ["targets"],
                    "additionalProperties": False,
                },
            },
            "function": {"type": "memory", "operation": "delete"},
        },
    ]


def _memory_intent_schema() -> dict[str, Any]:
    return {
        "intent": {
            "type": "string",
            "enum": ["automatic", "user_request"],
            "description": (
                "Your interpretation: user_request only when the current user asks "
                "to modify persistent Memory in the target scope; otherwise automatic. "
                "Not proof of consent. Omission means automatic; source never authorizes."
            ),
        }
    }


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
    from .retrieval_text import cjk_terms, lexical_terms

    normalized = unicodedata.normalize("NFC", value).casefold().replace("\u2019", "'")
    normalized = re.sub(r"(?<=\w)'s\b|(?<=s)'(?=\W|$)", "", normalized)
    terms = [
        _stem(token) for token in lexical_terms(normalized) if token not in _STOP_WORDS
    ]
    return terms + sorted(cjk_terms(normalized) - set(terms))


def _tokens(value: str) -> set[str]:
    """Return fresh mutable tokens from the immutable lexical cache."""
    return set(_cached_memory_tokens(value))


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


def _snapshot_embedding(
    memory: MemoryRecord,
    cache: Mapping[str, EmbeddingCacheEntry],
    model: str,
    space: str,
) -> Sequence[float] | None:
    entry = cache.get(memory.memory_id)
    if (
        entry is None
        or entry.model != model
        or entry.space_id != space
        or entry.fingerprint != _embedding_fingerprint(memory)
    ):
        return None
    return entry.vector


def _rank_memory_snapshot(
    corpus: tuple[MemoryRecord, ...],
    query: str,
    limit: int,
    offset: int,
    query_embedding: Sequence[float] | None,
    hybrid: bool,
    embedding_cache: Mapping[str, EmbeddingCacheEntry],
    embedding_model: str,
    embedding_space_id: str,
    allow_fuzzy_only: bool = True,
    automatic_recall: bool = False,
) -> list[MemoryRecord]:
    """Rank an immutable generation without holding the live store lock."""
    query_terms = _token_list(query)
    query_tokens = set(query_terms)
    query_weights = _automatic_query_weights(query_terms) if automatic_recall else None
    if not corpus or (not query_tokens and not (hybrid and query_embedding)):
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
            query_weights=query_weights,
        )
        lexical += _metadata_bonus(query_tokens, memory, query_weights)
        normalized_content = _normalize(memory.content)
        if (
            normalized_query
            and query_tokens & token_set
            and bounded_phrase(normalized_query, normalized_content)
        ):
            lexical += 0.35
        elif len(query_terms) > 1 and ordered_term_phrase(query_terms, terms):
            lexical += 0.18
        genuine_lexical = lexical
        if lexical <= 0:
            lexical = _fuzzy_relevance(query_tokens, token_set)
        semantic = (
            _cosine_similarity(
                query_embedding,
                _snapshot_embedding(
                    memory, embedding_cache, embedding_model, embedding_space_id
                ),
            )
            if hybrid
            else None
        )
        relevance = lexical
        if semantic is not None:
            semantic = max(0.0, semantic)
            relevance = 0.58 * lexical + 0.42 * semantic
        if not (
            (lexical if allow_fuzzy_only else genuine_lexical)
            >= MIN_LEXICAL_RELEVANCE_SCORE
            or (semantic is not None and semantic >= MIN_SEMANTIC_SIMILARITY)
        ):
            continue
        ranked.append((relevance, memory.memory_id, memory))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [memory for _, _, memory in ranked[offset : offset + limit]]


def _bm25_term_weights(
    query_terms: Sequence[str], *, reduce_filler: bool = False
) -> dict[str, float]:
    """Preserve compound weights while optionally discounting auto-query framing."""
    weights = dict.fromkeys(query_terms, 1.0)
    if reduce_filler:
        for term in weights:
            if term in _AUTOMATIC_MEMORY_FILLER_TERMS:
                weights[term] = 0.15
    # Compound expansion must not multiply the weight of an original word.
    for term in weights:
        parts = set(term.split("-")) if "-" in term else set()
        if parts and parts <= weights.keys():
            weight = 1.0 / (len(parts) + 1)
            weights[term] = min(weights[term], weight)
            for part in parts:
                weights[part] = min(weights[part], weight)
    return weights


def _automatic_query_weights(query_terms: list[str]) -> dict[str, float] | None:
    """Use lower padding weights only for long queries with real search anchors."""
    unique = set(query_terms)
    if (
        len(unique) < 8
        or len(unique - _AUTOMATIC_MEMORY_FILLER_TERMS) < 2
        or not unique & _AUTOMATIC_MEMORY_FILLER_TERMS
    ):
        return None
    return _bm25_term_weights(query_terms, reduce_filler=True)


def _bm25_score(
    query_terms: list[str],
    document_terms: Sequence[str],
    document_frequency: Mapping[str, int],
    document_count: int,
    average_length: float,
    *,
    query_weights: Mapping[str, float] | None = None,
) -> float:
    """Score BM25 with matching weighted numerator and normalization."""
    cached_terms = (
        document_terms if isinstance(document_terms, tuple) else tuple(document_terms)
    )
    frequencies = _cached_memory_term_frequencies(cached_terms)
    k1, b = 1.2, 0.75
    score = 0.0
    max_score = 0.0
    weights = (
        query_weights if query_weights is not None else _bm25_term_weights(query_terms)
    )
    for term, weight in weights.items():
        df = document_frequency.get(term, 0)
        idf = math.log(1 + (document_count - df + 0.5) / (df + 0.5))
        tf = frequencies.get(term, 0)
        denominator = tf + k1 * (1 - b + b * len(document_terms) / average_length)
        if tf:
            score += weight * idf * (tf * (k1 + 1) / denominator)
        max_score += weight * idf * (k1 + 1) / (1 + k1 * (1 - b))
    return score / max_score if max_score else 0.0


def _metadata_bonus(
    query_tokens: set[str],
    memory: MemoryRecord,
    query_weights: Mapping[str, float] | None = None,
) -> float:
    category = _cached_memory_tokens(memory.category)
    subject = _cached_memory_tokens(memory.subject or "")
    key = _cached_memory_tokens((memory.key or "").replace(".", " "))
    if query_weights is None:
        return (
            0.10 * len(query_tokens & category)
            + 0.16 * len(query_tokens & subject)
            + 0.18 * len(query_tokens & key)
        )
    return sum(
        factor * sum(query_weights[term] for term in query_tokens & tokens)
        for factor, tokens in (
            (0.10, category),
            (0.16, subject),
            (0.18, key),
        )
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
    left: Sequence[float] | None, right: Sequence[float] | None
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


def _prepare_stored_memories(
    data: Any,
) -> tuple[
    dict[str, MemoryRecord], dict[tuple[str, str], str], dict[str, Any] | None, bool
]:
    """Validate detached stored facts and build indices without touching live state."""
    memories: dict[str, MemoryRecord] = {}
    key_index: dict[tuple[str, str], str] = {}
    needs_save = False
    repair_required = False
    if data is None:
        raw_memories: list[Any] = []
    elif not isinstance(data, Mapping) or "memories" not in data:
        raw_memories = []
        needs_save = True
        repair_required = True
    else:
        candidate = data.get("memories")
        if not isinstance(candidate, list):
            raw_memories = []
            needs_save = True
            repair_required = True
        else:
            raw_memories = candidate
            if len(raw_memories) > MAX_MEMORIES_PER_AGENT:
                raw_memories = raw_memories[:MAX_MEMORIES_PER_AGENT]
                needs_save = True
                repair_required = True

    legacy_embeddings_found = False
    seen_ids: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()
    for raw in raw_memories:
        needs_save |= isinstance(raw, Mapping) and bool(
            {"importance", "last_confirmed_at"} & raw.keys()
        )
        legacy_embeddings_found |= isinstance(raw, Mapping) and "embedding" in raw
        try:
            memory = _validate_persistent_memory_record(raw)
            if memory.memory_id in seen_ids:
                raise ValueError("duplicate persistent memory ID")
            if memory.key is not None:
                key_pair = (memory.user_id, memory.key)
                if key_pair in seen_keys:
                    raise ValueError("duplicate canonical key in memory scope")
                seen_keys.add(key_pair)
        except TypeError, ValueError:
            needs_save = True
            repair_required = True
            _LOGGER.warning("Ignoring malformed persistent memory record")
            continue
        seen_ids.add(memory.memory_id)
        memories[memory.memory_id] = memory
        if memory.key:
            key_index[(memory.user_id, memory.key)] = memory.memory_id
    payload = (
        {"memories": [_record_as_storage_dict(memory) for memory in memories.values()]}
        if legacy_embeddings_found or needs_save
        else None
    )
    if repair_required:
        # Keep corruption in place, including overflow records and opaque fields.
        payload = None
    return memories, key_index, payload, repair_required


def _prepare_stored_embedding_cache(data: Any) -> dict[str, EmbeddingCacheEntry]:
    """Validate detached regenerable cache entries before atomic publication."""
    entries: dict[str, EmbeddingCacheEntry] = {}
    raw_entries = data.get("embeddings", {}) if isinstance(data, Mapping) else {}
    if not isinstance(raw_entries, Mapping):
        raise ValueError("embedding cache entries must be an object")
    for memory_id, raw in raw_entries.items():
        if not isinstance(memory_id, str) or not isinstance(raw, Mapping):
            raise ValueError("embedding cache entry is malformed")
        model = raw.get("model")
        fingerprint = raw.get("fingerprint")
        if not isinstance(model, str) or not isinstance(fingerprint, str):
            raise ValueError("embedding cache metadata is malformed")
        entries[memory_id] = EmbeddingCacheEntry(
            model=model,
            fingerprint=fingerprint,
            space_id=raw.get("space_id", "")
            if isinstance(raw.get("space_id", ""), str)
            else "",
            vector=_clean_embedding(raw.get("vector")),
        )
    return entries


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
    record = replace(
        record,
        content=_clean_content(record.content),
        category=_clean_category(record.category),
        subject=_clean_optional(record.subject, "subject", MAX_SUBJECT_LENGTH),
        key=_clean_key(record.key),
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
    """Normalize whitespace without discarding significant case or punctuation."""
    return " ".join(unicodedata.normalize("NFC", value).split())


@lru_cache(maxsize=20_000)
def _subject_identity(value: str | None) -> str:
    """Fold case and whitespace without erasing subject punctuation."""
    return " ".join(unicodedata.normalize("NFC", value or "").casefold().split())


@lru_cache(maxsize=20_000)
def _normalize(value: str) -> str:
    return " ".join(
        _TOKEN_PATTERN.findall(
            unicodedata.normalize("NFC", value).casefold().replace("\u2019", "'")
        )
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
    value = _SPACE_PATTERN.sub(" ", value).strip()
    normalized = value.casefold()
    if not normalized or len(normalized) > MAX_CATEGORY_LENGTH:
        raise ValueError(f"category must be 1 to {MAX_CATEGORY_LENGTH} characters")
    return normalized


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


@lru_cache(maxsize=20_000)
def _updated_at_instant(value: str) -> datetime:
    """Sort validated timestamps by instant; legacy naive values mean UTC.

    Keep stored strings intact and reuse parsing across management pages. Python's
    stable sort retains storage insertion order when instants are equal.
    """
    parsed = dt_util.parse_datetime(value)
    assert parsed is not None  # Record validation precedes publication.
    # Aware datetime comparisons already compare instants. Converting to UTC
    # would overflow for valid offset timestamps at the year 1/9999 boundaries.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


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


def _embedding_cache_payload(
    entries: Mapping[str, EmbeddingCacheEntry], memory_ids: frozenset[str]
) -> dict[str, Any]:
    """Convert immutable cache snapshots away from the event loop."""
    return {
        "embeddings": {
            memory_id: {
                "model": entry.model,
                "fingerprint": entry.fingerprint,
                "space_id": entry.space_id,
                "vector": list(entry.vector),
            }
            for memory_id, entry in entries.items()
            if memory_id in memory_ids
        }
    }
