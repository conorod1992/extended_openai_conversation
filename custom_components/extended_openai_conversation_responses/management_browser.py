"""Complete bounded browsing for persistent-memory management surfaces."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import management_ui
from .memory import MAX_LIST_LIMIT, async_get_memory, memory_as_dict

_PATCHED = "extended_openai_management_browser"
ManagementCommand = Callable[
    [HomeAssistant, str, bool, dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]
]


def _bounded_limit(message: dict[str, Any], default: int) -> int:
    """Return one management page size within the Memory backend's hard bound."""
    return max(1, min(int(message.get("limit", default)), MAX_LIST_LIMIT))


def _bounded_offset(message: dict[str, Any]) -> int:
    """Return a non-negative management page offset."""
    return max(0, int(message.get("offset", 0)))


def _search_projection(record: Any) -> str:
    """Mirror the former browser-side Memory filter without touching retrieval."""
    return " ".join(
        str(value or "")
        for value in (record.content, record.category, record.source)
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
        for record in memory._memories.values()  # noqa: SLF001
        if record.user_id == owner and query.casefold() in _search_projection(record)
    ]
    records.sort(key=lambda record: record.updated_at, reverse=True)
    page = records[offset : offset + limit]
    return {
        "memories": [memory_as_dict(record, include_scope=include_scope) for record in page],
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
            memory_as_dict(record, include_scope=include_scope) for record in records
        ],
        "scope_id": scope_id,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
    }


def wrap_management_browser(original: ManagementCommand) -> ManagementCommand:
    """Add complete paged persistent-memory browsing to the management API."""

    async def wrapped(
        hass: HomeAssistant,
        user_id: str,
        is_admin: bool,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        if message.get("section") != "memories" or message.get("action") not in {
            "list",
            "search",
        }:
            return await original(hass, user_id, is_admin, message)

        entry_id = message.get("entry_id")
        subentry_id = message.get("subentry_id")
        if not isinstance(entry_id, str) or not isinstance(subentry_id, str):
            raise HomeAssistantError("entry_id and subentry_id are required")
        management_ui.entry_and_agent(hass, entry_id, subentry_id)
        scope_id = management_ui._selected_scope(  # noqa: SLF001
            user_id, is_admin, message.get("scope_id")
        )
        owner = management_ui._memory_scope(scope_id)  # noqa: SLF001
        memory = await async_get_memory(hass, entry_id, subentry_id)

        if message.get("action") == "list":
            return await _list_page(
                memory,
                owner,
                scope_id,
                message,
                include_scope=is_admin,
            )

        query = str(message.get("query", "")).strip()
        if not query:
            return await _list_page(
                memory,
                owner,
                scope_id,
                message,
                include_scope=is_admin,
            )
        return _search_page(
            memory,
            owner,
            query,
            limit=_bounded_limit(message, 100),
            offset=_bounded_offset(message),
            include_scope=is_admin,
        )

    return wrapped


def install_management_browser() -> bool:
    """Install complete Memory browsing before authorization wrappers are applied."""
    if getattr(management_ui, _PATCHED, False):
        return False
    management_ui.async_management_command = wrap_management_browser(  # type: ignore[assignment]
        management_ui.async_management_command
    )
    setattr(management_ui, _PATCHED, True)
    return True
