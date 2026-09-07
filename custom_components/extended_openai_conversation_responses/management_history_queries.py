"""Bounded management-only queries over retained Usage and Conversation history."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict
from heapq import heappush, heapreplace
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .conversation_archive import _excerpt, _normalize, _tokens
from .management_result_limits import (
    MANAGEMENT_ARCHIVE_LIST_PAGE_MAX,
    MANAGEMENT_ARCHIVE_SEARCH_PAGE_MAX,
    MANAGEMENT_ARCHIVE_TURN_PAGE_MAX,
    MANAGEMENT_DAILY_PAGE_MAX,
    MANAGEMENT_USAGE_BREAKDOWN_KEYS,
    MANAGEMENT_USAGE_PAGE_MAX,
    page_from_iterable,
    page_metadata,
)

MAX_MANAGEMENT_HISTORY_OFFSET = 10_000


def _bounded_mapping(
    value: Any, *, max_keys: int
) -> tuple[dict[str, int], dict[str, Any]]:
    """Project one counter mapping without constructing another unbounded mapping."""
    if not isinstance(value, dict):
        return {}, {
            "returned": 0,
            "has_more": False,
            "omitted_contributions": 0,
            "omitted_total": 0,
        }
    result: dict[str, int] = {}
    omitted_contributions = 0
    omitted_total = 0
    for raw_key, raw_amount in value.items():
        key = str(raw_key)
        amount = (
            raw_amount
            if isinstance(raw_amount, int) and not isinstance(raw_amount, bool)
            else 0
        )
        if key in result:
            result[key] += max(0, amount)
        elif len(result) < max_keys:
            result[key] = max(0, amount)
        else:
            omitted_contributions += 1
            omitted_total += max(0, amount)
    return result, {
        "returned": len(result),
        "has_more": omitted_contributions > 0,
        "omitted_contributions": omitted_contributions,
        "omitted_total": omitted_total,
    }


def _bounded_usage_day(day: dict[str, Any]) -> dict[str, Any]:
    result = dict(day)
    metadata: dict[str, Any] = {}
    for field in ("provider_breakdown", "model_breakdown", "api_mode_breakdown"):
        bounded, field_meta = _bounded_mapping(
            day.get(field, {}), max_keys=MANAGEMENT_USAGE_BREAKDOWN_KEYS
        )
        result[field] = bounded
        metadata[field] = field_meta
    result["breakdown_meta"] = metadata
    return result


def usage_summary(manager: Any) -> dict[str, Any]:
    """Return bounded aggregate Usage data for management rendering."""
    lifetime = manager.as_dict()
    details, details_meta = _bounded_mapping(
        lifetime.get("details", {}), max_keys=MANAGEMENT_USAGE_BREAKDOWN_KEYS
    )
    lifetime["details"] = details
    lifetime["details_meta"] = details_meta
    latest = asdict(manager.latest_run) if manager.latest_run is not None else None
    return {
        "lifetime": lifetime,
        "today": _bounded_usage_day(manager.today_summary()),
        "month": _bounded_usage_day(manager.month_summary()),
        "latest": latest,
    }


def usage_daily_page(
    manager: Any,
    *,
    start_date: str,
    end_date: str,
    limit: int = MANAGEMENT_DAILY_PAGE_MAX,
    offset: int = 0,
) -> dict[str, Any]:
    """Return one bounded page of daily aggregate history."""
    safe_limit = max(1, min(int(limit), MANAGEMENT_DAILY_PAGE_MAX))
    safe_offset = max(0, int(offset))
    matching_dates = (
        date for date in sorted(manager.daily) if start_date <= date <= end_date
    )
    page, has_more = page_from_iterable(
        matching_dates, offset=safe_offset, limit=safe_limit
    )
    days = [_bounded_usage_day(manager.daily[date]) for date in page]
    return {
        "days": days,
        **page_metadata(
            offset=safe_offset,
            limit=safe_limit,
            returned=len(days),
            has_more=has_more,
        ),
    }


def usage_runs_page(
    manager: Any,
    *,
    limit: int = 50,
    offset: int = 0,
    successful: bool | None = None,
) -> dict[str, Any]:
    """Page recent runs without first copying every retained matching run."""
    safe_limit = max(1, min(int(limit), MANAGEMENT_USAGE_PAGE_MAX))
    safe_offset = max(0, int(offset))
    matching = (
        run
        for run in reversed(manager.runs)
        if successful is None or run.successful == successful
    )
    page, has_more = page_from_iterable(matching, offset=safe_offset, limit=safe_limit)
    runs = [asdict(run) for run in page]
    return {
        "runs": runs,
        **page_metadata(
            offset=safe_offset,
            limit=safe_limit,
            returned=len(runs),
            has_more=has_more,
        ),
    }


def usage_requests_page(
    manager: Any,
    run_id: str,
    *,
    limit: int = MANAGEMENT_USAGE_PAGE_MAX,
    offset: int = 0,
) -> dict[str, Any]:
    """Page one run's requests without first copying every matching request."""
    safe_limit = max(1, min(int(limit), MANAGEMENT_USAGE_PAGE_MAX))
    safe_offset = max(0, int(offset))
    matching = (request for request in manager.requests if request.run_id == run_id)
    page, has_more = page_from_iterable(matching, offset=safe_offset, limit=safe_limit)
    requests: list[dict[str, Any]] = []
    for request in page:
        item = asdict(request)
        details, details_meta = _bounded_mapping(
            item.get("details", {}), max_keys=MANAGEMENT_USAGE_BREAKDOWN_KEYS
        )
        item["details"] = details
        item["details_meta"] = details_meta
        requests.append(item)
    return {
        "requests": requests,
        **page_metadata(
            offset=safe_offset,
            limit=safe_limit,
            returned=len(requests),
            has_more=has_more,
        ),
    }


def usage_breakdowns(
    manager: Any,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Aggregate breakdowns with an independent bound on distinct dimensions."""
    output_fields = {
        "providers": "provider_breakdown",
        "models": "model_breakdown",
        "api_modes": "api_mode_breakdown",
    }
    result: dict[str, dict[str, int]] = {key: {} for key in output_fields}
    metadata: dict[str, dict[str, Any]] = {
        key: {
            "returned": 0,
            "has_more": False,
            "omitted_contributions": 0,
            "omitted_total": 0,
        }
        for key in output_fields
    }
    for date in sorted(manager.daily):
        if (start_date and date < start_date) or (end_date and date > end_date):
            continue
        day = manager.daily[date]
        for output_key, day_key in output_fields.items():
            values = day.get(day_key, {})
            if not isinstance(values, dict):
                continue
            bucket = result[output_key]
            field_meta = metadata[output_key]
            for raw_key, raw_amount in values.items():
                key = str(raw_key)
                amount = (
                    raw_amount
                    if isinstance(raw_amount, int) and not isinstance(raw_amount, bool)
                    else 0
                )
                amount = max(0, amount)
                if key in bucket:
                    bucket[key] += amount
                elif len(bucket) < MANAGEMENT_USAGE_BREAKDOWN_KEYS:
                    bucket[key] = amount
                else:
                    field_meta["has_more"] = True
                    field_meta["omitted_contributions"] += 1
                    field_meta["omitted_total"] += amount
    for key, bucket in result.items():
        metadata[key]["returned"] = len(bucket)
    return {**result, "breakdown_meta": metadata}


def _validate_archive_page(offset: int, limit: int, maximum: int) -> tuple[int, int]:
    safe_offset = max(0, int(offset))
    if safe_offset > MAX_MANAGEMENT_HISTORY_OFFSET:
        raise HomeAssistantError(
            f"offset must not exceed {MAX_MANAGEMENT_HISTORY_OFFSET}; narrow the query before paging further"
        )
    return safe_offset, max(1, min(int(limit), maximum))


async def _async_archive_query[T](
    archive: Any, query: Callable[..., T], *args: Any
) -> T:
    """Keep archive state immutable while a CPU-heavy query runs off-loop."""
    async with archive._lock:
        archive._ensure_initialized()
        task = asyncio.ensure_future(asyncio.to_thread(query, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError as cancelled:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            with suppress(BaseException):
                task.result()
            raise cancelled


def _archive_list_sync(
    sessions: dict[str, Any], scope_id: str, offset: int, limit: int
) -> dict[str, Any]:
    keep = offset + limit
    heap: list[tuple[str, str, Any]] = []
    total = 0
    for session in sessions.values():
        if session.scope_id != scope_id or session.retention_state != "retained":
            continue
        total += 1
        candidate = (session.last_message_at, session.session_id, session)
        if len(heap) < keep:
            heappush(heap, candidate)
        elif candidate[:2] > heap[0][:2]:
            heapreplace(heap, candidate)
    ranked = sorted(heap, key=lambda item: item[:2], reverse=True)
    page = ranked[offset : offset + limit]
    records = [asdict(item[2]) for item in page]
    return {
        "sessions": records,
        **page_metadata(
            offset=offset,
            limit=limit,
            returned=len(records),
            has_more=total > offset + len(records),
            total=total,
        ),
    }


async def archive_list_page(
    archive: Any, scope_id: str, *, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    """List retained sessions with bounded ranking work memory."""
    safe_offset, safe_limit = _validate_archive_page(
        offset, limit, MANAGEMENT_ARCHIVE_LIST_PAGE_MAX
    )
    return await _async_archive_query(
        archive,
        _archive_list_sync,
        archive._sessions,
        scope_id,
        safe_offset,
        safe_limit,
    )


def _archive_search_sync(
    sessions: dict[str, Any],
    turns_by_session: dict[str, list[Any]],
    scope_id: str,
    query: str,
    start_date: str | None,
    end_date: str | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    query_tokens = _tokens(query)
    normalized_query = _normalize(query)
    keep = offset + limit
    heap: list[tuple[float, str, str, str, Any, Any]] = []
    total = 0
    for session in sessions.values():
        if session.scope_id != scope_id or session.retention_state != "retained":
            continue
        for turn in turns_by_session.get(session.session_id, ()):
            date = turn.timestamp[:10]
            if start_date and date < start_date:
                continue
            if end_date and date > end_date:
                continue
            combined = f"{turn.user_text} {turn.assistant_text}"
            normalized_combined = _normalize(combined)
            tokens = _tokens(combined)
            overlap = len(query_tokens & tokens)
            if (
                query_tokens
                and not overlap
                and normalized_query not in normalized_combined
            ):
                continue
            score = overlap / max(1, len(query_tokens))
            if normalized_query and normalized_query in normalized_combined:
                score += 2
            total += 1
            candidate = (
                score,
                turn.timestamp,
                session.session_id,
                turn.turn_id,
                session,
                turn,
            )
            if len(heap) < keep:
                heappush(heap, candidate)
            elif candidate[:4] > heap[0][:4]:
                heapreplace(heap, candidate)
    ranked = sorted(heap, key=lambda item: item[:4], reverse=True)
    page = ranked[offset : offset + limit]
    records = [
        {
            "session_id": session.session_id,
            "turn_id": turn.turn_id,
            "date": turn.timestamp[:10],
            "timestamp": turn.timestamp,
            "title": session.title,
            "excerpt": _excerpt(f"{turn.user_text}\n{turn.assistant_text}", query),
        }
        for _, _, _, _, session, turn in page
    ]
    return {
        "results": records,
        **page_metadata(
            offset=offset,
            limit=limit,
            returned=len(records),
            has_more=total > offset + len(records),
            total=total,
        ),
    }


async def archive_search_page(
    archive: Any,
    scope_id: str,
    query: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Search retained turns using bounded ranking memory off the event loop."""
    if not isinstance(query, str) or not query.strip():
        raise HomeAssistantError("query must not be blank")
    safe_offset, safe_limit = _validate_archive_page(
        offset, limit, MANAGEMENT_ARCHIVE_SEARCH_PAGE_MAX
    )
    return await _async_archive_query(
        archive,
        _archive_search_sync,
        archive._sessions,
        archive._turns,
        scope_id,
        query,
        start_date,
        end_date,
        safe_offset,
        safe_limit,
    )


async def archive_get_page(
    archive: Any,
    scope_id: str,
    session_id: str,
    *,
    start_turn: int = 0,
    limit: int = MANAGEMENT_ARCHIVE_TURN_PAGE_MAX,
) -> dict[str, Any]:
    """Return one management turn page with explicit continuation metadata."""
    safe_start = max(0, int(start_turn))
    safe_limit = max(1, min(int(limit), MANAGEMENT_ARCHIVE_TURN_PAGE_MAX))
    result: dict[str, Any] = await archive.async_get(
        scope_id, session_id, safe_start, safe_limit
    )
    returned = len(result.get("turns", []))
    result.update(
        page_metadata(
            offset=safe_start,
            limit=safe_limit,
            returned=returned,
            has_more=bool(result.get("has_more")),
            total=int(result.get("session", {}).get("turn_count", returned)),
        )
    )
    result["start_turn"] = safe_start
    return result
