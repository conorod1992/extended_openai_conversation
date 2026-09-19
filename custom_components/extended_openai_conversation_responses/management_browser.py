"""Complete bounded browsing for persistent-memory management surfaces."""

from __future__ import annotations

from typing import Any

from .memory import MAX_LIST_LIMIT, memory_as_dict, memory_revision


def _bounded_limit(message: dict[str, Any], default: int) -> int:
    """Return one management page size within the Memory backend's hard bound."""
    return max(1, min(int(message.get("limit", default)), MAX_LIST_LIMIT))


def _bounded_offset(message: dict[str, Any]) -> int:
    """Return a non-negative management page offset."""
    return max(0, int(message.get("offset", 0)))


def _search_projection(record: Any) -> str:
    """Mirror the former browser-side Memory filter without touching retrieval."""
    return " ".join(
        str(value or "") for value in (record.content, record.category, record.source)
    ).casefold()


def _search_page(
    memory: Any,
    owner: str,
    query: str,
    *,
    limit: int,
    offset: int,
    include_scope: bool,
) -> dict[str, Any]:
    """Search the complete in-memory management projection with bounded output."""
    # PersistentMemory has already been initialized by async_get_memory(). Taking one
    # event-loop-local snapshot avoids paging/sorting the same collection repeatedly.
    records = [
        record
        for record in memory._memories.values()
        if record.user_id == owner and query.casefold() in _search_projection(record)
    ]
    records.sort(key=lambda record: record.updated_at, reverse=True)
    page = records[offset : offset + limit]
    return {
        "memories": [
            management_memory_dict(record, include_scope=include_scope)
            for record in page
        ],
        "offset": offset,
        "limit": limit,
        "has_more": len(records) > offset + limit,
        "total": len(records),
        "query": query,
    }


async def _list_page(
    memory: Any,
    owner: str,
    scope_id: str,
    message: dict[str, Any],
    *,
    include_scope: bool,
) -> dict[str, Any]:
    """Return one exact Memory page plus an authoritative continuation flag."""
    limit = _bounded_limit(message, 100)
    offset = _bounded_offset(message)
    category = message.get("category")
    records = await memory.async_list(owner, category, limit, offset)
    has_more = False
    if len(records) == limit:
        has_more = bool(
            await memory.async_list(owner, category, 1, offset + len(records))
        )
    return {
        "memories": [
            management_memory_dict(record, include_scope=include_scope)
            for record in records
        ],
        "scope_id": scope_id,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
    }


async def async_browse_memories(
    memory: Any,
    owner: str,
    scope_id: str,
    message: dict[str, Any],
    *,
    include_scope: bool,
) -> dict[str, Any]:
    """Return a complete, bounded list/search page for an authorized Memory owner."""
    query = str(message.get("query", "")).strip()
    if message.get("action") == "list" or not query:
        return await _list_page(
            memory, owner, scope_id, message, include_scope=include_scope
        )
    return _search_page(
        memory,
        owner,
        query,
        limit=_bounded_limit(message, 100),
        offset=_bounded_offset(message),
        include_scope=include_scope,
    )


def management_memory_dict(record: Any, *, include_scope: bool) -> dict[str, Any]:
    """Expose the edit token only on the Management projection."""
    return memory_as_dict(record, include_scope=include_scope) | {
        "revision": memory_revision(record)
    }
