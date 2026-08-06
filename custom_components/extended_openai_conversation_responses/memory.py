"""Local persistent memory for conversation agents."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import logging
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

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}.memory"
ANONYMOUS_USER_ID = LEGACY_ANONYMOUS_SCOPE_ID
MAX_MEMORIES_PER_AGENT = 10_000
MAX_CONTENT_LENGTH = 1_000
MAX_CATEGORY_LENGTH = 64
MAX_SEARCH_LIMIT = 50
MAX_LIST_LIMIT = 100
MEMORY_TOOL_NAMES = {
    "memory_add",
    "memory_search",
    "memory_list",
    "memory_update",
    "memory_delete",
}

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


class MemoryStorage(Protocol):
    """Persistence boundary for a future alternative memory backend."""

    async def async_load(self) -> Mapping[str, Any] | Sequence[Any] | None:
        """Load stored memory data."""

    async def async_save(self, data: dict[str, Any]) -> None:
        """Persist memory data."""


class MemoryStore(Store[dict[str, Any]]):
    """Versioned Home Assistant Store backend."""

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: Any
    ) -> dict[str, Any]:
        """Migrate older memory payloads."""
        if old_major_version == 0:
            if isinstance(old_data, list):
                return {"memories": old_data}
            if isinstance(old_data, dict):
                return {"memories": old_data.get("memories", [])}
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


class PersistentMemory:
    """Concurrency-safe memory collection with bounded indexed search."""

    def __init__(self, storage: MemoryStorage) -> None:
        """Initialize memory collection."""
        self._storage = storage
        self._memories: dict[str, MemoryRecord] = {}
        self._token_index: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._initialized = False

    async def async_initialize(self) -> None:
        """Load memory data once."""
        async with self._lock:
            if self._initialized:
                return
            data = await self._storage.async_load()
            raw_memories = data.get("memories", []) if isinstance(data, Mapping) else []
            for raw in raw_memories:
                try:
                    memory = MemoryRecord(**raw)
                except TypeError, ValueError:
                    _LOGGER.warning("Ignoring malformed persistent memory record")
                    continue
                self._memories[memory.memory_id] = memory
                self._index(memory)
            self._initialized = True

    async def async_add(
        self, user_id: str, content: str, category: str, source: str
    ) -> dict[str, Any]:
        """Add a memory, or return a likely duplicate."""
        content = _clean_content(content)
        category = _clean_category(category)
        if source not in {"explicit", "implicit"}:
            raise ValueError("source must be explicit or implicit")
        _validate_privacy(content, source)

        async with self._lock:
            self._ensure_initialized()
            duplicate = self._find_duplicate(user_id, content)
            if duplicate:
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
            )
            self._memories[memory.memory_id] = memory
            self._index(memory)
            await self._async_save_locked()
            return {"status": "created", "memory": memory_as_dict(memory)}

    async def async_search(
        self,
        user_id: str,
        query: str,
        category: str | None = None,
        limit: int = 5,
    ) -> list[MemoryRecord]:
        """Return the most relevant memories without scanning model context."""
        self._ensure_initialized()
        limit = max(1, min(limit, MAX_SEARCH_LIMIT))
        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        category_filter = _clean_category(category) if category else None

        candidates: set[str] = set()
        for token in query_tokens:
            candidates.update(self._token_index.get((user_id, token), set()))

        normalized_query = _normalize(query)
        ranked: list[tuple[float, str, MemoryRecord]] = []
        for memory_id in candidates:
            memory = self._memories[memory_id]
            if category_filter and memory.category != category_filter:
                continue
            memory_tokens = _tokens(memory.content)
            overlap = len(query_tokens & memory_tokens)
            score = overlap / len(query_tokens)
            if normalized_query in _normalize(memory.content):
                score += 1
            ranked.append((score, memory.updated_at, memory))

        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [memory for _, _, memory in ranked[:limit]]

    async def async_list(
        self,
        user_id: str,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        """List memories for one user scope."""
        self._ensure_initialized()
        limit = max(1, min(limit, MAX_LIST_LIMIT))
        offset = max(0, offset)
        category_filter = _clean_category(category) if category else None
        memories = [
            memory
            for memory in self._memories.values()
            if memory.user_id == user_id
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
    ) -> MemoryRecord:
        """Update a memory owned by one user scope."""
        async with self._lock:
            self._ensure_initialized()
            current = self._owned_memory(user_id, memory_id)
            new_content = (
                _clean_content(content) if content is not None else current.content
            )
            new_category = (
                _clean_category(category) if category is not None else current.category
            )
            _validate_privacy(new_content, current.source)
            self._unindex(current)
            updated = MemoryRecord(
                memory_id=current.memory_id,
                user_id=current.user_id,
                content=new_content,
                category=new_category,
                source=current.source,
                created_at=current.created_at,
                updated_at=dt_util.utcnow().isoformat(),
            )
            self._memories[memory_id] = updated
            self._index(updated)
            await self._async_save_locked()
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
                self._unindex(current)
                updated = MemoryRecord(
                    memory_id=current.memory_id,
                    user_id=target_scope_id,
                    content=current.content,
                    category=current.category,
                    source=current.source,
                    created_at=current.created_at,
                    updated_at=dt_util.utcnow().isoformat(),
                )
                self._memories[memory_id] = updated
                self._index(updated)
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

    def _owned_memory(self, user_id: str, memory_id: str) -> MemoryRecord:
        memory = self._memories.get(memory_id)
        if memory is None or memory.user_id != user_id:
            raise ValueError("memory not found")
        return memory

    def _index(self, memory: MemoryRecord) -> None:
        for token in _tokens(memory.content):
            self._token_index[(memory.user_id, token)].add(memory.memory_id)

    def _unindex(self, memory: MemoryRecord) -> None:
        for token in _tokens(memory.content):
            key = (memory.user_id, token)
            ids = self._token_index.get(key)
            if ids is None:
                continue
            ids.discard(memory.memory_id)
            if not ids:
                del self._token_index[key]

    async def _async_save_locked(self) -> None:
        await self._storage.async_save(
            {"memories": [asdict(memory) for memory in self._memories.values()]}
        )

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
            HomeAssistantMemoryStorage(hass, entry_id, subentry_id)
        )
    manager = managers[key]
    await manager.async_initialize()
    return manager


def memory_user_id(context: Any) -> str:
    """Resolve Home Assistant's authenticated user scope."""
    ha_context = getattr(context, "context", None)
    return getattr(ha_context, "user_id", None) or ANONYMOUS_USER_ID


def memory_as_dict(
    memory: MemoryRecord, *, include_scope: bool = False
) -> dict[str, str]:
    """Serialize a memory, exposing its owner only to explicit admin callers."""
    result = {
        "memory_id": memory.memory_id,
        "content": memory.content,
        "category": memory.category,
        "source": memory.source,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
    }
    if include_scope:
        result["scope_id"] = memory.user_id
    return result


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
                    },
                    "required": ["content", "category", "source"],
                    "additionalProperties": False,
                },
            },
            "function": {"type": "memory", "operation": "add"},
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
                        }
                    },
                    "required": ["memory_ids"],
                    "additionalProperties": False,
                },
            },
            "function": {"type": "memory", "operation": "delete"},
        },
    ]


def _tokens(value: str) -> set[str]:
    return {
        _stem(token)
        for token in _TOKEN_PATTERN.findall(value.casefold())
        if len(token) > 1 and token not in _STOP_WORDS
    }


def _stem(token: str) -> str:
    """Apply small deterministic suffix normalization for local lookup."""
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


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
