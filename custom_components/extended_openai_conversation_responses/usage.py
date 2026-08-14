"""Token-only provider request and conversation-run usage accounting."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
import time
from typing import Any, Protocol
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
    DEFAULT_USAGE_RUN_RETENTION_DAYS,
    DOMAIN,
)

STORAGE_VERSION = 2
STORAGE_KEY_PREFIX = f"{DOMAIN}.usage"
_USAGE_MANAGERS = f"{DOMAIN}.usage_managers"
MAX_RECENT_LIMIT = 200


class UsageStorage(Protocol):
    async def async_load(self) -> dict[str, Any] | None: ...
    async def async_save(self, data: dict[str, Any]) -> None: ...


@dataclass(slots=True)
class RequestUsage:
    """Provider-reported tokens for one API request."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    details: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class UsageTotals:
    """Indefinite lifetime counters (legacy names retained for compatibility)."""

    conversation_count: int = 0
    api_request_count: int = 0
    successful_request_count: int = 0
    failed_request_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    details: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class UsageRequest:
    """Bounded, content-free detail for one provider request."""

    request_id: str
    run_id: str
    timestamp: str
    agent_subentry_id: str
    provider: str
    model: str
    api_mode: str
    successful: bool
    duration_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    request_stage: str = "other"
    tool_calls_requested: int = 0
    web_search_used: bool = False
    error_type: str | None = None
    details: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class UsageRun:
    """One complete Home Assistant user turn, potentially with many requests."""

    run_id: str
    started_at: str
    completed_at: str | None
    duration_ms: int
    agent_subentry_id: str
    home_assistant_conversation_id: str | None
    source_device_id: str | None
    request_count: int = 0
    successful_request_count: int = 0
    failed_request_count: int = 0
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    successful: bool = True
    models: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    api_modes: list[str] = field(default_factory=list)
    web_search_used: bool = False
    error_type: str | None = None


def _integer(value: Any) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
        else 0
    )


def _value(source: Any, key: str) -> Any:
    return source.get(key) if isinstance(source, dict) else getattr(source, key, None)


def extract_usage(usage: Any) -> RequestUsage:
    """Normalize Chat Completions or Responses token metadata."""
    if usage is None:
        return RequestUsage()
    input_tokens = _integer(_value(usage, "input_tokens")) or _integer(
        _value(usage, "prompt_tokens")
    )
    output_tokens = _integer(_value(usage, "output_tokens")) or _integer(
        _value(usage, "completion_tokens")
    )
    total_tokens = (
        _integer(_value(usage, "total_tokens")) or input_tokens + output_tokens
    )
    input_details = _value(usage, "input_tokens_details") or _value(
        usage, "prompt_tokens_details"
    )
    output_details = _value(usage, "output_tokens_details") or _value(
        usage, "completion_tokens_details"
    )
    cached_input_tokens = _integer(_value(input_details, "cached_tokens"))
    reasoning_tokens = _integer(_value(output_details, "reasoning_tokens"))
    details: dict[str, int] = {}
    for prefix, detail_source in (("input", input_details), ("output", output_details)):
        if detail_source is None:
            continue
        if hasattr(detail_source, "model_dump"):
            detail_source = detail_source.model_dump(exclude_none=True)
        elif not isinstance(detail_source, dict) and hasattr(detail_source, "__dict__"):
            detail_source = vars(detail_source)
        if isinstance(detail_source, dict):
            for key, value in detail_source.items():
                if amount := _integer(value):
                    details[f"{prefix}_{key}"] = amount
    return RequestUsage(
        input_tokens,
        output_tokens,
        total_tokens,
        cached_input_tokens,
        reasoning_tokens,
        details,
    )


class UsageManager:
    """Persist compact totals/days separately from bounded request/run details."""

    def __init__(
        self,
        storage: UsageStorage,
        daily_storage: UsageStorage | None = None,
        detail_storage: UsageStorage | None = None,
        *,
        agent_subentry_id: str = "",
        request_retention_days: int = DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
        run_retention_days: int = DEFAULT_USAGE_RUN_RETENTION_DAYS,
    ) -> None:
        self._storage = storage
        self._daily_storage = daily_storage
        self._detail_storage = detail_storage
        self._agent_subentry_id = agent_subentry_id
        self.request_retention_days = request_retention_days
        self.run_retention_days = run_retention_days
        self.totals = UsageTotals()
        self.daily: dict[str, dict[str, Any]] = {}
        self.requests: list[UsageRequest] = []
        self.runs: list[UsageRun] = []
        self._listeners: set[Callable[[], None]] = set()
        self._lock = asyncio.Lock()
        self._initialized = False
        self._current_run: ContextVar[UsageRun | None] = ContextVar(
            f"usage_run_{id(self)}", default=None
        )
        self._run_started: dict[str, float] = {}

    async def async_initialize(self) -> None:
        if self._initialized:
            return
        stored = await self._storage.async_load()
        if stored:
            # Version-one payloads were the totals object itself. Version two keeps
            # the same compact shape, so migration cannot lose cumulative counters.
            values_source = stored.get("totals", stored)
            allowed = {item.name for item in fields(UsageTotals)}
            values = {
                key: values_source[key] for key in allowed if key in values_source
            }
            if isinstance(values.get("details"), dict):
                values["details"] = {
                    str(k): _integer(v)
                    for k, v in values["details"].items()
                    if _integer(v)
                }
            self.totals = UsageTotals(**values)
        if self._daily_storage is not None:
            data = await self._daily_storage.async_load() or {}
            self.daily = {
                str(k): v
                for k, v in data.get("days", {}).items()
                if isinstance(v, dict)
            }
        if self._detail_storage is not None:
            data = await self._detail_storage.async_load() or {}
            for raw in data.get("requests", []):
                try:
                    self.requests.append(UsageRequest(**raw))
                except TypeError, ValueError:
                    continue
            for raw in data.get("runs", []):
                try:
                    self.runs.append(UsageRun(**raw))
                except TypeError, ValueError:
                    continue
        self._initialized = True
        await self.async_prune_details(save=False)

    async def async_record_conversation(self) -> None:
        """Compatibility API; new conversation code uses ``async_run``."""
        async with self._lock:
            self.totals.conversation_count += 1
            await self._async_save_totals()

    @asynccontextmanager
    async def async_run(
        self,
        *,
        home_assistant_conversation_id: str | None = None,
        source_device_id: str | None = None,
    ) -> AsyncIterator[UsageRun]:
        """Guarantee exactly one finalized run on success, error, or cancellation."""
        started = dt_util.utcnow().isoformat()
        run = UsageRun(
            run_id=uuid4().hex,
            started_at=started,
            completed_at=None,
            duration_ms=0,
            agent_subentry_id=self._agent_subentry_id,
            home_assistant_conversation_id=home_assistant_conversation_id,
            source_device_id=source_device_id,
        )
        self._run_started[run.run_id] = time.monotonic()
        token = self._current_run.set(run)
        try:
            yield run
        except BaseException as err:
            run.successful = False
            run.error_type = type(err).__name__
            raise
        finally:
            self._current_run.reset(token)
            await self._async_finalize_run(run)

    def current_run(self) -> UsageRun | None:
        return self._current_run.get()

    def mark_current_run_failed(self, error_type: str) -> None:
        if run := self._current_run.get():
            run.successful = False
            run.error_type = error_type[:128]

    async def async_record_request(
        self,
        *,
        successful: bool,
        usage: RequestUsage | None = None,
        provider: str = "unknown",
        model: str = "unknown",
        api_mode: str = "unknown",
        duration_ms: int = 0,
        request_stage: str = "other",
        tool_calls_requested: int = 0,
        web_search_used: bool = False,
        error_type: str | None = None,
    ) -> None:
        """Count one request and optionally attach it to the current run."""
        usage = usage or RequestUsage()
        async with self._lock:
            self.totals.api_request_count += 1
            if successful:
                self.totals.successful_request_count += 1
            else:
                self.totals.failed_request_count += 1
            self._add_tokens(self.totals, usage)

            run = self._current_run.get()
            if run is not None:
                run.request_count += 1
                run.successful_request_count += int(successful)
                run.failed_request_count += int(not successful)
                run.tool_call_count += max(0, tool_calls_requested)
                run.web_search_used = run.web_search_used or web_search_used
                self._add_tokens(run, usage)
                for collection, value in (
                    (run.models, model),
                    (run.providers, provider),
                    (run.api_modes, api_mode),
                ):
                    if value and value not in collection:
                        collection.append(value)
                if not successful:
                    run.successful = False
                    run.error_type = error_type or run.error_type or "provider_error"
                if self.request_retention_days > 0:
                    self.requests.append(
                        UsageRequest(
                            request_id=uuid4().hex,
                            run_id=run.run_id,
                            timestamp=dt_util.utcnow().isoformat(),
                            agent_subentry_id=self._agent_subentry_id,
                            provider=provider,
                            model=model,
                            api_mode=api_mode,
                            successful=successful,
                            duration_ms=max(0, duration_ms),
                            input_tokens=usage.input_tokens,
                            output_tokens=usage.output_tokens,
                            total_tokens=usage.total_tokens,
                            cached_input_tokens=usage.cached_input_tokens,
                            reasoning_tokens=usage.reasoning_tokens,
                            request_stage=request_stage,
                            tool_calls_requested=max(0, tool_calls_requested),
                            web_search_used=web_search_used,
                            error_type=error_type,
                            details=dict(usage.details),
                        )
                    )
            await self._async_save_totals()
            if self._detail_storage is not None and run is not None:
                await self._async_save_details()
            self._notify()

    async def _async_finalize_run(self, run: UsageRun) -> None:
        async with self._lock:
            if run.completed_at is not None:
                return
            run.completed_at = dt_util.utcnow().isoformat()
            run.duration_ms = max(
                0,
                int(
                    (
                        time.monotonic()
                        - self._run_started.pop(run.run_id, time.monotonic())
                    )
                    * 1000
                ),
            )
            self.totals.conversation_count += 1
            if self.run_retention_days > 0:
                self.runs.append(run)
            day_key = dt_util.now().date().isoformat()
            day = self.daily.setdefault(day_key, _empty_day(day_key))
            _add_run_to_day(day, run)
            await self._async_save_totals()
            if self._daily_storage is not None:
                await self._daily_storage.async_save({"days": self.daily})
            if self._detail_storage is not None:
                await self._async_save_details()
            self._notify()

    async def async_prune_details(self, *, save: bool = True) -> dict[str, int]:
        now = dt_util.utcnow()
        request_cutoff = now - timedelta(days=max(0, self.request_retention_days))
        run_cutoff = now - timedelta(days=max(0, self.run_retention_days))
        old_request_count = len(self.requests)
        old_run_count = len(self.runs)
        self.requests = [
            r
            for r in self.requests
            if self.request_retention_days > 0
            and _parse_time(r.timestamp) >= request_cutoff
        ]
        self.runs = [
            r
            for r in self.runs
            if self.run_retention_days > 0 and _parse_time(r.started_at) >= run_cutoff
        ]
        if save and self._detail_storage is not None:
            await self._async_save_details()
        return {
            "deleted_requests": old_request_count - len(self.requests),
            "deleted_runs": old_run_count - len(self.runs),
        }

    async def async_clear_details(self, *, confirm: bool) -> dict[str, int]:
        if not confirm:
            raise ValueError("Explicit confirmation is required")
        result = {
            "deleted_requests": len(self.requests),
            "deleted_runs": len(self.runs),
        }
        self.requests.clear()
        self.runs.clear()
        if self._detail_storage is not None:
            await self._async_save_details()
        return result

    def summary_for_date(self, date: str) -> dict[str, Any]:
        return dict(self.daily.get(date, _empty_day(date)))

    def today_summary(self) -> dict[str, Any]:
        return self.summary_for_date(dt_util.now().date().isoformat())

    def month_summary(self, month: str | None = None) -> dict[str, Any]:
        month = month or dt_util.now().strftime("%Y-%m")
        result = _empty_day(month)
        result["date"] = month
        for date, day in self.daily.items():
            if date.startswith(month):
                _merge_day(result, day)
        _calculate_averages(result)
        return result

    def daily_series(
        self, start_date: str, end_date: str, limit: int = 366
    ) -> list[dict[str, Any]]:
        return [
            dict(self.daily[key])
            for key in sorted(self.daily)
            if start_date <= key <= end_date
        ][: max(1, min(limit, 366))]

    def recent_runs(
        self, *, limit: int = 50, offset: int = 0, successful: bool | None = None
    ) -> dict[str, Any]:
        items = [
            r
            for r in reversed(self.runs)
            if successful is None or r.successful == successful
        ]
        limit = max(1, min(limit, MAX_RECENT_LIMIT))
        page = items[max(0, offset) : max(0, offset) + limit]
        return {
            "runs": [asdict(r) for r in page],
            "offset": max(0, offset),
            "limit": limit,
            "has_more": len(items) > max(0, offset) + limit,
        }

    def requests_for_run(
        self, run_id: str, *, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        items = [r for r in self.requests if r.run_id == run_id]
        limit = max(1, min(limit, MAX_RECENT_LIMIT))
        page = items[max(0, offset) : max(0, offset) + limit]
        return {
            "requests": [asdict(r) for r in page],
            "offset": max(0, offset),
            "limit": limit,
            "has_more": len(items) > max(0, offset) + limit,
        }

    def breakdowns(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {
            "providers": {},
            "models": {},
            "api_modes": {},
        }
        for date, day in self.daily.items():
            if (start_date and date < start_date) or (end_date and date > end_date):
                continue
            for output_key, day_key in (
                ("providers", "provider_breakdown"),
                ("models", "model_breakdown"),
                ("api_modes", "api_mode_breakdown"),
            ):
                for value, tokens in day.get(day_key, {}).items():
                    result[output_key][value] = (
                        result[output_key].get(value, 0) + tokens
                    )
        return result

    @property
    def latest_run(self) -> UsageRun | None:
        return self.runs[-1] if self.runs else None

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self.totals)

    async def async_backup_data(self) -> dict[str, Any]:
        """Return all persisted usage categories without in-flight run state."""
        async with self._lock:
            if not self._initialized:
                raise RuntimeError("usage statistics have not been initialized")
            return {
                "totals": self.as_dict(),
                "daily": deepcopy(self.daily),
                "requests": [asdict(request) for request in self.requests],
                "runs": [asdict(run) for run in self.runs],
            }

    @staticmethod
    def validate_backup_data(
        data: Any, target_agent_id: str
    ) -> tuple[
        UsageTotals, dict[str, dict[str, Any]], list[UsageRequest], list[UsageRun]
    ]:
        """Validate exact replacement usage state without mutating counters."""
        if not isinstance(data, dict) or set(data) != {
            "totals",
            "daily",
            "requests",
            "runs",
        }:
            raise ValueError("usage data is incomplete or corrupted")
        totals_raw = data["totals"]
        allowed_totals = {item.name for item in fields(UsageTotals)}
        if not isinstance(totals_raw, dict) or set(totals_raw) != allowed_totals:
            raise ValueError("usage totals are invalid")
        _validate_counter_mapping(totals_raw, allowed_totals - {"details"})
        details = _validate_breakdown(totals_raw["details"], "usage total details")
        totals = UsageTotals(**{**totals_raw, "details": details})

        daily_raw = data["daily"]
        if not isinstance(daily_raw, dict) or len(daily_raw) > 3660:
            raise ValueError("daily usage data is invalid")
        daily: dict[str, dict[str, Any]] = {}
        for date, day in daily_raw.items():
            if not isinstance(date, str) or len(date) != 10:
                raise ValueError("daily usage date is invalid")
            try:
                datetime.fromisoformat(date)
            except ValueError as err:
                raise ValueError("daily usage date is invalid") from err
            daily[date] = _validate_usage_day(date, day)

        if not isinstance(data["requests"], list) or not isinstance(data["runs"], list):
            raise ValueError("usage details must be lists")
        requests = [
            _usage_request_from_backup(raw, target_agent_id) for raw in data["requests"]
        ]
        runs = [_usage_run_from_backup(raw, target_agent_id) for raw in data["runs"]]
        if len({request.request_id for request in requests}) != len(requests):
            raise ValueError("usage request IDs must be unique")
        if len({run.run_id for run in runs}) != len(runs):
            raise ValueError("usage run IDs must be unique")
        return totals, daily, requests, runs

    async def async_replace_backup(
        self,
        totals: UsageTotals,
        daily: dict[str, dict[str, Any]],
        requests: list[UsageRequest],
        runs: list[UsageRun],
    ) -> None:
        """Replace usage accounting and reapply current retention policies."""
        async with self._lock:
            self.totals = totals
            self.daily = deepcopy(daily)
            self.requests = list(requests)
            self.runs = list(runs)
            await self._async_save_totals()
            if self._daily_storage is not None:
                await self._daily_storage.async_save({"days": self.daily})
            if self._detail_storage is not None:
                await self._async_save_details()
        await self.async_prune_details()
        self._notify()

    @staticmethod
    def _add_tokens(target: Any, usage: RequestUsage) -> None:
        target.input_tokens += usage.input_tokens
        target.output_tokens += usage.output_tokens
        target.total_tokens += usage.total_tokens
        target.cached_input_tokens += usage.cached_input_tokens
        target.reasoning_tokens += usage.reasoning_tokens
        if isinstance(target, UsageTotals):
            for key, value in usage.details.items():
                target.details[key] = target.details.get(key, 0) + value

    async def _async_save_totals(self) -> None:
        if not self._initialized:
            raise RuntimeError("usage statistics have not been initialized")
        await self._storage.async_save(self.as_dict())

    async def _async_save_details(self) -> None:
        if self._detail_storage is not None:
            await self._detail_storage.async_save(
                {
                    "requests": [asdict(r) for r in self.requests],
                    "runs": [asdict(r) for r in self.runs],
                }
            )

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()


def _validate_counter_mapping(data: dict[str, Any], keys: set[str]) -> None:
    for key in keys:
        value = data.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"usage counter {key} is invalid")


def _validate_breakdown(value: Any, label: str) -> dict[str, int]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str)
        and isinstance(amount, int)
        and not isinstance(amount, bool)
        and amount >= 0
        for key, amount in value.items()
    ):
        raise ValueError(f"{label} is invalid")
    return dict(value)


def _validate_usage_day(date: str, value: Any) -> dict[str, Any]:
    expected = _empty_day(date)
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("daily usage record is invalid")
    if value.get("date") != date:
        raise ValueError("daily usage date does not match its key")
    counter_keys = {
        key
        for key, default in expected.items()
        if isinstance(default, int) and not isinstance(default, bool)
    }
    _validate_counter_mapping(value, counter_keys)
    for key in (
        "average_requests_per_completed_run",
        "average_duration_ms_per_completed_run",
    ):
        amount = value[key]
        if (
            not isinstance(amount, int | float)
            or isinstance(amount, bool)
            or amount < 0
        ):
            raise ValueError(f"daily usage value {key} is invalid")
    result = dict(value)
    for key in ("provider_breakdown", "model_breakdown", "api_mode_breakdown"):
        result[key] = _validate_breakdown(value[key], key)
    return result


def _usage_request_from_backup(raw: Any, target_agent_id: str) -> UsageRequest:
    if not isinstance(raw, dict):
        raise ValueError("usage request must be an object")
    try:
        request = UsageRequest(**{**raw, "agent_subentry_id": target_agent_id})
    except TypeError as err:
        raise ValueError("usage request is invalid") from err
    string_values = (
        request.request_id,
        request.run_id,
        request.timestamp,
        request.agent_subentry_id,
        request.provider,
        request.model,
        request.api_mode,
        request.request_stage,
    )
    counter_keys = {
        "duration_ms",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
        "tool_calls_requested",
    }
    if (
        not all(isinstance(value, str) for value in string_values)
        or not request.request_id
        or not request.run_id
        or not isinstance(request.successful, bool)
        or not isinstance(request.web_search_used, bool)
        or (request.error_type is not None and not isinstance(request.error_type, str))
        or _parse_time(request.timestamp) == datetime.min.replace(tzinfo=dt_util.UTC)
    ):
        raise ValueError("usage request metadata is invalid")
    values = asdict(request)
    _validate_counter_mapping(values, counter_keys)
    request.details = _validate_breakdown(request.details, "usage request details")
    return request


def _usage_run_from_backup(raw: Any, target_agent_id: str) -> UsageRun:
    if not isinstance(raw, dict):
        raise ValueError("usage run must be an object")
    try:
        run = UsageRun(**{**raw, "agent_subentry_id": target_agent_id})
    except TypeError as err:
        raise ValueError("usage run is invalid") from err
    counter_keys = {
        "duration_ms",
        "request_count",
        "successful_request_count",
        "failed_request_count",
        "tool_call_count",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
    }
    if (
        not isinstance(run.run_id, str)
        or not run.run_id
        or not isinstance(run.started_at, str)
        or (run.completed_at is not None and not isinstance(run.completed_at, str))
        or not isinstance(run.agent_subentry_id, str)
        or (
            run.home_assistant_conversation_id is not None
            and not isinstance(run.home_assistant_conversation_id, str)
        )
        or (
            run.source_device_id is not None
            and not isinstance(run.source_device_id, str)
        )
        or not isinstance(run.successful, bool)
        or not isinstance(run.web_search_used, bool)
        or (run.error_type is not None and not isinstance(run.error_type, str))
        or not all(
            isinstance(item, str)
            for collection in (run.models, run.providers, run.api_modes)
            if isinstance(collection, list)
            for item in collection
        )
        or not all(
            isinstance(collection, list)
            for collection in (run.models, run.providers, run.api_modes)
        )
        or _parse_time(run.started_at) == datetime.min.replace(tzinfo=dt_util.UTC)
        or (
            run.completed_at is not None
            and _parse_time(run.completed_at)
            == datetime.min.replace(tzinfo=dt_util.UTC)
        )
    ):
        raise ValueError("usage run metadata is invalid")
    _validate_counter_mapping(asdict(run), counter_keys)
    return run


def _empty_day(date: str) -> dict[str, Any]:
    return {
        "date": date,
        "run_count": 0,
        "successful_run_count": 0,
        "failed_run_count": 0,
        "api_request_count": 0,
        "successful_request_count": 0,
        "failed_request_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "tool_call_count": 0,
        "web_search_run_count": 0,
        "total_run_duration_ms": 0,
        "average_tokens_per_completed_run": 0,
        "average_requests_per_completed_run": 0.0,
        "average_duration_ms_per_completed_run": 0.0,
        "provider_breakdown": {},
        "model_breakdown": {},
        "api_mode_breakdown": {},
    }


def _add_run_to_day(day: dict[str, Any], run: UsageRun) -> None:
    day["run_count"] += 1
    day["successful_run_count"] += int(run.successful)
    day["failed_run_count"] += int(not run.successful)
    day["api_request_count"] += run.request_count
    day["successful_request_count"] += run.successful_request_count
    day["failed_request_count"] += run.failed_request_count
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
    ):
        day[key] += getattr(run, key)
    day["tool_call_count"] += run.tool_call_count
    day["web_search_run_count"] += int(run.web_search_used)
    day["total_run_duration_ms"] += run.duration_ms
    for key, values in (
        ("provider_breakdown", run.providers),
        ("model_breakdown", run.models),
        ("api_mode_breakdown", run.api_modes),
    ):
        for value in values:
            day[key][value] = day[key].get(value, 0) + run.total_tokens
    _calculate_averages(day)


def _merge_day(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict):
            bucket = target.setdefault(key, {})
            for name, amount in value.items():
                bucket[name] = bucket.get(name, 0) + amount
        elif key not in {
            "date",
            "average_tokens_per_completed_run",
            "average_requests_per_completed_run",
            "average_duration_ms_per_completed_run",
        } and isinstance(value, int):
            target[key] += value


def _calculate_averages(day: dict[str, Any]) -> None:
    count = day["run_count"]
    if count:
        day["average_tokens_per_completed_run"] = round(day["total_tokens"] / count)
        day["average_requests_per_completed_run"] = round(
            day["api_request_count"] / count, 2
        )
        day["average_duration_ms_per_completed_run"] = round(
            day["total_run_duration_ms"] / count, 2
        )


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_util.UTC)


async def async_get_usage(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> UsageManager:
    managers: dict[tuple[str, str], UsageManager] = hass.data.setdefault(
        _USAGE_MANAGERS, {}
    )
    key = (entry_id, subentry_id)
    if key not in managers:
        prefix = f"{STORAGE_KEY_PREFIX}.{entry_id}.{subentry_id}"
        manager = UsageManager(
            Store(hass, 1, prefix),
            Store(hass, STORAGE_VERSION, f"{prefix}.daily"),
            Store(
                hass,
                STORAGE_VERSION,
                f"{prefix}.details",
                private=True,
                atomic_writes=True,
                serialize_in_event_loop=False,
            ),
            agent_subentry_id=subentry_id,
        )
        await manager.async_initialize()
        managers[key] = manager
    return managers[key]
