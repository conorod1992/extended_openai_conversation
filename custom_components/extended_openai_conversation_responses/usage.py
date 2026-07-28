"""Persistent, provider-reported usage statistics."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}.usage"
_USAGE_MANAGERS = f"{DOMAIN}.usage_managers"


class UsageStorage(Protocol):
    """Storage boundary used by the persistent usage manager."""

    async def async_load(self) -> dict[str, Any] | None:
        """Load usage data."""

    async def async_save(self, data: dict[str, Any]) -> None:
        """Persist usage data."""


@dataclass(slots=True)
class RequestUsage:
    """Usage reported by one provider request."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    details: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class UsageTotals:
    """Cumulative statistics for one conversation-agent subentry."""

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


def _integer(value: Any) -> int:
    """Return a safe non-negative integer from SDK or mapping data."""
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
        else 0
    )


def _value(source: Any, key: str) -> Any:
    """Read an SDK field without assuming a specific provider response type."""
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)


def extract_usage(usage: Any) -> RequestUsage:
    """Normalize Chat Completions or Responses usage metadata."""
    if usage is None:
        return RequestUsage()

    input_tokens = _integer(_value(usage, "input_tokens")) or _integer(
        _value(usage, "prompt_tokens")
    )
    output_tokens = _integer(_value(usage, "output_tokens")) or _integer(
        _value(usage, "completion_tokens")
    )
    total_tokens = _integer(_value(usage, "total_tokens"))
    if not total_tokens:
        total_tokens = input_tokens + output_tokens

    input_details = _value(usage, "input_tokens_details") or _value(
        usage, "prompt_tokens_details"
    )
    output_details = _value(usage, "output_tokens_details") or _value(
        usage, "completion_tokens_details"
    )
    cached_input_tokens = _integer(_value(input_details, "cached_tokens"))
    reasoning_tokens = _integer(_value(output_details, "reasoning_tokens"))

    details: dict[str, int] = {}
    for prefix, detail_source in (
        ("input", input_details),
        ("output", output_details),
    ):
        if detail_source is None:
            continue
        if hasattr(detail_source, "model_dump"):
            detail_source = detail_source.model_dump(exclude_none=True)
        elif not isinstance(detail_source, dict) and hasattr(detail_source, "__dict__"):
            detail_source = vars(detail_source)
        if not isinstance(detail_source, dict):
            continue
        for key, value in detail_source.items():
            if amount := _integer(value):
                details[f"{prefix}_{key}"] = amount

    return RequestUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
        details=details,
    )


class UsageManager:
    """Persist cumulative usage and notify diagnostic entities."""

    def __init__(self, storage: UsageStorage) -> None:
        """Initialize the manager."""
        self._storage = storage
        self.totals = UsageTotals()
        self._listeners: set[Callable[[], None]] = set()
        self._lock = asyncio.Lock()
        self._initialized = False

    async def async_initialize(self) -> None:
        """Load cumulative statistics once."""
        if self._initialized:
            return
        stored = await self._storage.async_load()
        if stored:
            allowed = {item.name for item in fields(UsageTotals)}
            values = {key: value for key, value in stored.items() if key in allowed}
            if isinstance(values.get("details"), dict):
                values["details"] = {
                    str(key): _integer(value)
                    for key, value in values["details"].items()
                    if _integer(value)
                }
            self.totals = UsageTotals(**values)
        self._initialized = True

    async def async_record_conversation(self) -> None:
        """Count one Home Assistant conversation, separately from API calls."""
        async with self._lock:
            self.totals.conversation_count += 1
            await self._async_save()

    async def async_record_request(
        self, *, successful: bool, usage: RequestUsage | None = None
    ) -> None:
        """Count one attempted API request and any provider-reported usage."""
        async with self._lock:
            self.totals.api_request_count += 1
            if successful:
                self.totals.successful_request_count += 1
            else:
                self.totals.failed_request_count += 1
            if usage is not None:
                self.totals.input_tokens += usage.input_tokens
                self.totals.output_tokens += usage.output_tokens
                self.totals.total_tokens += usage.total_tokens
                self.totals.cached_input_tokens += usage.cached_input_tokens
                self.totals.reasoning_tokens += usage.reasoning_tokens
                for key, value in usage.details.items():
                    self.totals.details[key] = self.totals.details.get(key, 0) + value
            await self._async_save()

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Listen for statistics changes."""
        self._listeners.add(listener)

        def remove_listener() -> None:
            self._listeners.discard(listener)

        return remove_listener

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot."""
        return asdict(self.totals)

    async def _async_save(self) -> None:
        """Persist and notify listeners."""
        if not self._initialized:
            raise RuntimeError("usage statistics have not been initialized")
        await self._storage.async_save(self.as_dict())
        for listener in tuple(self._listeners):
            listener()


async def async_get_usage(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> UsageManager:
    """Return the shared usage manager for one conversation-agent subentry."""
    managers: dict[tuple[str, str], UsageManager] = hass.data.setdefault(
        _USAGE_MANAGERS, {}
    )
    key = (entry_id, subentry_id)
    if key not in managers:
        storage = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{entry_id}.{subentry_id}",
        )
        manager = UsageManager(storage)
        await manager.async_initialize()
        managers[key] = manager
    return managers[key]
