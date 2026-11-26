"""Durable scheduling for delayed configured Function Tools."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextvars import Context as TaskContext
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
import hashlib
import json
import logging
import re
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import voluptuous as vol

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import (
    DATA_EXPOSED_ENTITIES,
    async_should_expose,
)
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Context, CoreState, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er, llm
from homeassistant.util import dt as dt_util

from .agent_config import function_tool_enabled
from .agent_maintenance import AgentRestoreRecoveryRequired, get_agent_maintenance_gate
from .const import DOMAIN
from .exceptions import EffectOutcomeUnknown, EffectReplayBlocked
from .function_tool_quarantine import (
    _runtime_configured_function_tools as configured_function_tools_from_data,
)
from .function_tool_resolution import latest_function_tool_for_execution
from .function_yaml import dump_authored_function_configuration
from .ha_permissions import bind_active_ha_context, filter_entities_for_active_user
from .ha_tool_result_compat import ExecutionFailure, tool_result_data
from .helpers import get_exposed_entities
from .model_tool_results import local_function_results
from .mutation_outcomes import mark_mutation_failure
from .operational_errors import log_handled_failure
from .persistence_hardening import (
    _async_repair_private_store_mode,
    _async_settle_transactional_save,
)
from .record_timestamps import parse_datetime
from .request_origin import bind_request_satellite, get_request_satellite
from .scheduled_action_summary import describe_native_action, present_entities
from .strict_store import PropagatingWriteStore, _async_settle_store_io

_LOGGER = logging.getLogger(__name__)

DATA_DELAYED_TOOL_MANAGER = "delayed_tool_manager"
_DATA_DELAYED_STORE_LOCK = "delayed_tool_store_lock"
DELAYED_TOOL_STORAGE_VERSION = 1
DELAYED_TOOL_STORAGE_KEY = f"{DOMAIN}.delayed_tools"
MAX_DELAYED_CALLS = 256
MAX_DELAYED_CALLS_PER_AGENT = 32
MAX_DELAYED_ARGUMENT_BYTES = 16384
_AGENT_RETRY_SECONDS = 30
_CLOCK_RECHECK_SECONDS = 30
_DEFAULT_GRACE_SECONDS = 15 * 60
_MAX_AGENT_RETRIES = 10
_PENDING = "pending"
_EXECUTING = "executing"
_DELAYED_EXECUTION_MARKER = "_extended_openai_delayed_execution"
_ACTION_ID = re.compile(r"(?:[0-9a-f]{32}|legacy_[0-9a-f]{64})")
_TOOL_LABEL = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass(frozen=True, slots=True)
class ScheduledActionOwner:
    """Trusted request identity for one assistant's queue."""

    entry_id: str
    subentry_id: str
    user_id: str | None = None
    device_id: str | None = None
    satellite_id: str | None = None
    administrator: bool = False

    def allows(self, record: DelayedToolCall) -> bool:
        if (self.entry_id, self.subentry_id) != (record.entry_id, record.subentry_id):
            return False
        if self.administrator:
            return True
        if self.user_id or record.user_id is not None:
            return bool(self.user_id) and self.user_id == record.user_id
        # Prefer the most specific origin. Older records may only have a device
        # ID, but a stored satellite must never match a different satellite.
        if record.satellite_id and self.satellite_id:
            return record.satellite_id == self.satellite_id
        return (
            not record.satellite_id
            and bool(self.device_id)
            and record.device_id == self.device_id
        )


def _public_action_id(call_id: str) -> str:
    if len(call_id) == 32 and _ACTION_ID.fullmatch(call_id):
        return call_id
    # Historical stores accepted arbitrary strings. Keep them manageable without
    # echoing stored text or rewriting the queue's identifiers.
    return (
        "legacy_" + hashlib.sha256(call_id.encode("utf-8", "surrogatepass")).hexdigest()
    )


def valid_scheduled_action_id(value: Any) -> bool:
    """Accept only a complete ID returned by the scheduler."""
    return (
        isinstance(value, str)
        and len(value) <= 71
        and _ACTION_ID.fullmatch(value) is not None
    )


def delayed_tool_manager(hass: HomeAssistant) -> DelayedToolManager | None:
    """Get the existing scheduler without initiating recovery or startup."""
    manager = hass.data.get(DOMAIN, {}).get(DATA_DELAYED_TOOL_MANAGER)
    return manager if isinstance(manager, DelayedToolManager) else None


class _DelayedQueueLimitError(HomeAssistantError):
    """A saved queue cannot safely be admitted to the scheduler."""


def _parse_scheduled_utc(value: str) -> datetime | None:
    """Validate an instant with the same time zone rules used for execution."""
    try:
        parsed = parse_datetime(value)
        return dt_util.as_utc(parsed) if parsed is not None else None
    except ValueError, OverflowError:
        return None


def _validate_argument_budget(arguments: dict[str, Any]) -> None:
    size = 0
    try:
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(
            arguments
        ):
            size += len(chunk.encode("utf-8"))
            if size > MAX_DELAYED_ARGUMENT_BYTES:
                raise _DelayedQueueLimitError(
                    f"Delayed Function Tool arguments exceed {MAX_DELAYED_ARGUMENT_BYTES} bytes"
                )
    except (ValueError, TypeError, RecursionError) as err:
        raise HomeAssistantError(
            "Delayed Function Tool arguments must be bounded JSON"
        ) from err


def _validate_call_capacity(
    records: dict[str, DelayedToolCall],
    entry_id: str | None = None,
    subentry_id: str | None = None,
) -> None:
    counts: dict[tuple[str, str], int] = {}
    for record in records.values():
        key = (record.entry_id, record.subentry_id)
        counts[key] = counts.get(key, 0) + 1
    if entry_id is not None:
        assert subentry_id is not None
        key = (entry_id, subentry_id)
        counts[key] = counts.get(key, 0) + 1
    if len(records) + (entry_id is not None) > MAX_DELAYED_CALLS or any(
        count > MAX_DELAYED_CALLS_PER_AGENT for count in counts.values()
    ):
        raise _DelayedQueueLimitError("Delayed Function Tool queue capacity reached")


def tool_definition_fingerprint(tool: dict[str, Any]) -> str:
    """Hash the persisted schema and implementation, excluding presentation/state."""
    source = deepcopy(tool)
    payload = {key: source[key] for key in ("spec", "function") if key in source}
    encoded = dump_authored_function_configuration(payload)
    # V1 omitted information about authored key types and order. Treat it as
    # unverifiable while keeping the queue's stored hash format readable.
    return hashlib.sha256(b"eoai-authored-tool-v2\0" + encoded.encode()).hexdigest()


@dataclass(slots=True, frozen=True)
class DelayedToolCall:
    """Persisted delayed Function Tool invocation."""

    call_id: str
    entry_id: str
    subentry_id: str
    tool_name: str
    arguments: dict[str, Any]
    due_at: str
    created_at: str
    user_id: str | None = None
    device_id: str | None = None
    status: str = _PENDING
    retry_count: int = 0
    definition_fingerprint: str | None = None
    satellite_id: str | None = None
    # Missing/null means eventual execution, including all pre-expiry jobs.
    expires_at: str | None = None
    # Preserve absent legacy metadata on read/write, while newly scheduled
    # catch-up jobs explicitly persist null. This flag is never stored.
    _expiry_metadata_present: bool = field(default=True, repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        """Serialize the call for Home Assistant storage."""
        payload = asdict(self)
        payload.pop("_expiry_metadata_present")
        if self.satellite_id is None:
            # Preserve legacy queue shape when there is no physical origin.
            payload.pop("satellite_id")
        if self.expires_at is None and not self._expiry_metadata_present:
            payload.pop("expires_at")
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> DelayedToolCall:
        """Validate and deserialize one stored call."""
        if not isinstance(raw, dict):
            raise ValueError("stored delayed call is not an object")
        required_strings = (
            "call_id",
            "entry_id",
            "subentry_id",
            "tool_name",
            "due_at",
            "created_at",
        )
        for key in required_strings:
            if not isinstance(raw.get(key), str) or not raw[key]:
                raise ValueError(f"stored delayed call has invalid {key}")
        if not isinstance(raw.get("arguments"), dict):
            raise ValueError("stored delayed call has invalid arguments")
        _validate_argument_budget(raw["arguments"])
        status = raw.get("status", _PENDING)
        if status not in {_PENDING, _EXECUTING}:
            raise ValueError("stored delayed call has invalid status")
        retry_count = raw.get("retry_count", 0)
        if not isinstance(retry_count, int) or retry_count < 0:
            raise ValueError("stored delayed call has invalid retry_count")
        due = _parse_scheduled_utc(raw["due_at"])
        if due is None:
            raise ValueError("stored delayed call has invalid due_at timestamp")
        if _parse_scheduled_utc(raw["created_at"]) is None:
            raise ValueError("stored delayed call has invalid created_at timestamp")
        user_id = raw.get("user_id")
        device_id = raw.get("device_id")
        satellite_id = raw.get("satellite_id")
        expires_at = raw.get("expires_at")
        if expires_at is not None:
            if not isinstance(expires_at, str):
                raise ValueError("stored delayed call has invalid expires_at")
            expiry = _parse_scheduled_utc(expires_at)
            if expiry is None or expiry < due:
                raise ValueError("stored delayed call has invalid expires_at timestamp")
        fingerprint = raw.get("definition_fingerprint")
        if fingerprint is not None and (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in fingerprint)
        ):
            raise ValueError("stored delayed call has invalid definition_fingerprint")
        if user_id is not None and not isinstance(user_id, str):
            raise ValueError("stored delayed call has invalid user_id")
        if device_id is not None and not isinstance(device_id, str):
            raise ValueError("stored delayed call has invalid device_id")
        if satellite_id is not None and not isinstance(satellite_id, str):
            raise ValueError("stored delayed call has invalid satellite_id")
        return cls(
            call_id=raw["call_id"],
            entry_id=raw["entry_id"],
            subentry_id=raw["subentry_id"],
            tool_name=raw["tool_name"],
            arguments=deepcopy(raw["arguments"]),
            due_at=raw["due_at"],
            created_at=raw["created_at"],
            user_id=user_id,
            device_id=device_id,
            status=status,
            retry_count=retry_count,
            definition_fingerprint=fingerprint,
            satellite_id=satellite_id,
            expires_at=expires_at,
            _expiry_metadata_present="expires_at" in raw,
        )


def _delay_as_timedelta(value: Any) -> timedelta:
    """Normalize the same time-period shapes accepted by Home Assistant scripts."""
    try:
        delay = cast(timedelta, cv.time_period(value))
    except OverflowError as err:
        raise HomeAssistantError(
            "Invalid Function Tool delay: duration is too large"
        ) from err
    except (TypeError, ValueError, vol.Invalid) as err:
        raise HomeAssistantError(f"Invalid Function Tool delay: {err}") from err
    if delay.total_seconds() < 0:
        raise HomeAssistantError("Function Tool delay cannot be negative")
    return delay


async def async_remove_stored_agent_calls(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> None:
    """Erase unloaded-agent arguments without arming any scheduled execution."""
    async with _delayed_store_lock(hass):
        # Setup may have published a manager while this deletion waited. Its
        # in-memory generation must participate in the same store transaction.
        manager = hass.data.get(DOMAIN, {}).get(DATA_DELAYED_TOOL_MANAGER)
        if isinstance(manager, DelayedToolManager):
            await manager._async_remove_agent_locked(entry_id, subentry_id)
        else:
            await _async_remove_stored_agent_calls_locked(
                hass, lambda parent, child: (parent, child) == (entry_id, subentry_id)
            )


async def async_prune_orphaned_delayed_calls(hass: HomeAssistant) -> None:
    """Remove records whose saved owner no longer exists, including disk-only jobs."""

    def deleted(entry_id: str, subentry_id: str) -> bool:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            return True
        if entry.domain != DOMAIN:
            return False
        return subentry_id not in entry.subentries

    async with _delayed_store_lock(hass):
        manager = delayed_tool_manager(hass)
        if manager is not None and manager._setup_complete and not manager._quarantined:
            await manager._async_remove_calls_locked(deleted)
        else:
            await _async_remove_stored_agent_calls_locked(hass, deleted)


def _delayed_store_lock(hass: HomeAssistant) -> asyncio.Lock:
    """Serialize every owner of the shared delayed-call storage generation."""
    lock: asyncio.Lock = hass.data.setdefault(DOMAIN, {}).setdefault(
        _DATA_DELAYED_STORE_LOCK, asyncio.Lock()
    )
    return lock


async def _async_load_delayed_queue_store(
    store: PropagatingWriteStore,
) -> dict[str, Any] | None:
    try:
        return await _async_settle_store_io(store.async_load())
    except (KeyError, NotImplementedError) as err:
        # HA's Store can reject an invalid outer version or data envelope
        # before the queue has a chance to validate its own payload.
        raise ValueError("Persisted delayed Function Tool Store is malformed") from err


async def _async_remove_stored_agent_calls_locked(
    hass: HomeAssistant, remove: Callable[[str, str], bool]
) -> None:
    """Read and replace stored calls while retaining shared-store ownership."""
    store = PropagatingWriteStore(
        hass,
        DELAYED_TOOL_STORAGE_VERSION,
        DELAYED_TOOL_STORAGE_KEY,
        private=True,
        atomic_writes=True,
    )
    data = await _async_load_delayed_queue_store(store)
    if data is None or data == {}:
        return
    if not isinstance(data, dict) or not isinstance(data.get("calls"), list):
        raise ValueError("persisted delayed Function Tools are malformed")
    survivors = [
        raw
        for raw in data["calls"]
        if not (
            isinstance(raw, dict)
            and isinstance(parent := raw.get("entry_id"), str)
            and isinstance(child := raw.get("subentry_id"), str)
            and parent
            and child
            and remove(parent, child)
        )
    ]
    if len(survivors) != len(data["calls"]):
        await _async_settle_store_io(store.async_save({**data, "calls": survivors}))


class DelayedToolManager:
    """Persist, recover, and execute delayed configured Function Tools."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the scheduler."""
        self.hass = hass
        self._store = PropagatingWriteStore(
            hass,
            DELAYED_TOOL_STORAGE_VERSION,
            DELAYED_TOOL_STORAGE_KEY,
            private=True,
            atomic_writes=True,
        )
        self._records: dict[str, DelayedToolCall] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._invalidated_tasks: set[asyncio.Task[None]] = set()
        self._lock = _delayed_store_lock(hass)
        self._setup_lock = asyncio.Lock()
        self._started = False
        self._setup_complete = False
        self._quarantined = False
        self._quarantine_reason: str | None = None

    async def async_setup(self) -> None:
        """Load persisted calls and arm recovery once Home Assistant is running."""
        if self._setup_complete:
            return
        async with self._setup_lock:
            if self._setup_complete:
                return

            # Cancellation can leave an executor-backed tool or write settling.
            # Do not load/rearm the next generation while those workers still own it.
            settling = tuple(self._invalidated_tasks)
            try:
                if settling:
                    await asyncio.shield(
                        asyncio.gather(*settling, return_exceptions=True)
                    )
            finally:
                self._invalidated_tasks.difference_update(
                    task for task in settling if task.done()
                )

            async with self._lock:
                try:
                    await _async_repair_private_store_mode(self._store)
                    await self._async_load_locked()
                except _DelayedQueueLimitError as err:
                    # Valid but oversized queues retain the existing repair policy.
                    self._quarantine("saved queue exceeds its limits")
                    _LOGGER.error(
                        "Delayed Function Tool scheduler disabled: %s. Saved jobs "
                        "are preserved; remove affected agents in Management or "
                        "reduce the saved queue while Home Assistant is stopped, "
                        "then restart Home Assistant to retry recovery",
                        err,
                    )
                except (HomeAssistantError, OSError, ValueError, TypeError) as err:
                    # Do not interpret unreadable jobs as an empty queue or arm any
                    # previously saved work. A later setup call must not retry.
                    self._quarantine("saved storage is unreadable or malformed")
                    _LOGGER.error(
                        "Delayed Function Tool scheduler disabled: saved storage "
                        "needs repair (%s). Jobs are preserved; repair the storage "
                        "while Home Assistant is stopped, then restart to retry",
                        type(err).__name__,
                    )

    def _quarantine(self, reason: str) -> None:
        self._records.clear()
        self._quarantined = True
        self._quarantine_reason = reason
        self._setup_complete = True

    async def _async_load_locked(self) -> None:
        """Publish startup recovery without overlapping another store owner."""
        raw_data = await _async_load_delayed_queue_store(self._store)
        if raw_data is None or raw_data == {}:
            raw_calls = []
        elif not isinstance(raw_data, dict) or not isinstance(
            raw_data.get("calls"), list
        ):
            raise ValueError("Persisted delayed Function Tool queue is malformed")
        else:
            raw_calls = raw_data["calls"]
        dirty = False
        recovered: dict[str, DelayedToolCall] = {}
        for raw in raw_calls:
            try:
                record = DelayedToolCall.from_dict(raw)
            except ValueError as err:
                dirty = True
                _LOGGER.warning(
                    "Ignoring invalid persisted delayed Function Tool: %s", err
                )
                continue
            if record.status == _EXECUTING:
                # A previous process persisted the execution boundary before
                # invoking the tool. Replaying it could duplicate a side effect.
                dirty = True
                _LOGGER.warning(
                    "Not replaying interrupted delayed Function Tool `%s`; its "
                    "prior execution outcome is indeterminate",
                    record.tool_name,
                )
                continue
            recovered[record.call_id] = record
            _validate_call_capacity(recovered)

        # Never truncate durable work to fit the new limits or arm an oversized store.
        _validate_call_capacity(recovered)
        if dirty:
            await _async_settle_store_io(
                self._store.async_save(self._storage_payload(recovered))
            )
        self._records = recovered

        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._handle_stop)
        running = self.hass.state == CoreState.running
        if not running:
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._handle_started
            )
        self._setup_complete = True
        if running:
            self._handle_started()

    async def async_schedule(
        self,
        entity: Any,
        tool_name: str,
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        *,
        function_tool: dict[str, Any] | None = None,
        execution_delay: dict[str, Any] | timedelta | None = None,
    ) -> DelayedToolCall:
        """Persist a delayed call before exposing it as scheduled."""
        if self._quarantined:
            raise HomeAssistantError(
                "Delayed Function Tool scheduler is disabled because the "
                f"{self._quarantine_reason or 'saved queue is unavailable'}; "
                "repair the saved data and restart Home Assistant"
            )
        if not self._setup_complete:
            raise HomeAssistantError("Delayed Function Tool scheduler is unavailable")
        implementation = (function_tool or {}).get("function", {})
        if (
            implementation.get("type") == "native"
            and implementation.get("name") == "execute_service"
        ):
            from .safety_hardening import _validate_execute_service_request

            _validate_execute_service_request(arguments)
        delay = _delay_as_timedelta(
            execution_delay if execution_delay is not None else arguments.get("delay")
        )
        now = dt_util.utcnow()
        catch_up = (function_tool or {}).get("delay_catch_up", False)
        if not isinstance(catch_up, bool):
            raise HomeAssistantError("Function Tool delay_catch_up must be a boolean")
        context = getattr(llm_context, "context", None)
        from .execution_outcomes import (
            execution_completed,
            execution_operation,
            execution_rejected,
        )

        async with self._lock:
            if not self._setup_complete or self._quarantined:
                raise HomeAssistantError(
                    "Delayed Function Tool scheduler is unavailable"
                )
            _validate_call_capacity(
                self._records, entity.entry.entry_id, entity.subentry.subentry_id
            )
            _validate_argument_budget(arguments)
            try:
                due_at = now + delay
                expires_at = (
                    None
                    if catch_up
                    else due_at + timedelta(seconds=_DEFAULT_GRACE_SECONDS)
                )
            except OverflowError as err:
                raise HomeAssistantError(
                    "Function Tool delay exceeds the supported scheduling range"
                ) from err
            record = DelayedToolCall(
                call_id=uuid4().hex,
                entry_id=entity.entry.entry_id,
                subentry_id=entity.subentry.subentry_id,
                tool_name=tool_name,
                arguments=deepcopy(arguments),
                due_at=due_at.isoformat(),
                expires_at=expires_at.isoformat() if expires_at is not None else None,
                created_at=now.isoformat(),
                user_id=getattr(context, "user_id", None),
                device_id=getattr(llm_context, "device_id", None),
                satellite_id=get_request_satellite(),
                definition_fingerprint=tool_definition_fingerprint(function_tool)
                if function_tool is not None
                else None,
            )
            updated = {**self._records, record.call_id: record}
            with execution_operation(
                "delayed_job",
                {
                    "operation": "schedule",
                    "entry_id": record.entry_id,
                    "subentry_id": record.subentry_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "delay_seconds": delay.total_seconds(),
                    "definition": record.definition_fingerprint,
                    "user_id": record.user_id,
                    "device_id": record.device_id,
                    "satellite_id": record.satellite_id,
                },
            ) as operation:
                try:
                    await self._async_save_records_transactionally(updated)
                except BaseException as err:
                    if not self._setup_complete or self._quarantined:
                        if isinstance(err, Exception) and operation.receipt is not None:
                            raise EffectOutcomeUnknown() from None
                        raise
                    retained = self._records.get(record.call_id)
                    if retained is None:
                        execution_rejected(operation.receipt)
                        raise
                    if retained != record:
                        if isinstance(err, Exception):
                            raise EffectOutcomeUnknown() from None
                        raise
                    execution_completed(operation.receipt)
                    if not isinstance(err, Exception):
                        raise
                    # The validated readback confirmed this exact submission.
                    record = retained
                finally:
                    if self._started and self._records.get(record.call_id) == record:
                        self._arm(record.call_id)
        return record

    @property
    def available(self) -> bool:
        """Whether management can inspect an authoritative queue generation."""
        return self._setup_complete and not self._quarantined

    async def async_list_scheduled_actions(
        self,
        owner: ScheduledActionOwner,
        *,
        authorized: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Return a bounded, safe view of this owner's pending actions."""
        async with self._lock:
            if authorized is not None and not authorized():
                return {"status": "denied"}
            if not self.available:
                return {"status": "unavailable"}
            now = dt_util.utcnow()
            pending = [
                (due, record)
                for record in self._records.values()
                if record.status == _PENDING
                and owner.allows(record)
                and not self._execution_grace_expired(record, now)
                and (due := _parse_scheduled_utc(record.due_at)) is not None
            ]
        # The records are immutable snapshots. The per-assistant admission limit
        # also bounds projection work and the size of this reply.
        native_tools = self._summary_tools(owner) if pending else {}
        fingerprints: dict[str, str | None] = {}
        return {
            "status": "ok",
            "actions": [
                {
                    "action_id": _public_action_id(record.call_id),
                    "scheduled_at": due.isoformat(),
                    "description": self._action_description(
                        record, native_tools.get(record.tool_name), owner, fingerprints
                    ),
                }
                for due, record in sorted(
                    pending, key=lambda item: (item[0], item[1].call_id)
                )
            ],
        }

    def _summary_tools(self, owner: ScheduledActionOwner) -> dict[str, dict[str, Any]]:
        """Read tool definitions for display, independently of the live tool scope."""
        try:
            entry = self.hass.config_entries.async_get_entry(owner.entry_id)
            subentry = (
                entry.subentries.get(owner.subentry_id) if entry is not None else None
            )
            if subentry is not None:
                from .function_configuration_repair import isolated_function_tools

                tools, _, _ = isolated_function_tools(dict(subentry.data))
                return {
                    tool["spec"]["name"]: tool
                    for tool in tools
                    if tool["function"].get("type") == "native"
                    and tool["function"].get("name")
                    in {"execute_service", "execute_service_single"}
                }
        except Exception as err:
            log_handled_failure(
                _LOGGER, "Could not read Scheduled Action summary tools", err
            )
        return {}

    def _action_description(
        self,
        record: DelayedToolCall,
        tool: dict[str, Any] | None,
        owner: ScheduledActionOwner,
        fingerprints: dict[str, str | None],
    ) -> str:
        name = record.tool_name
        fallback = (
            f"Function Tool: {name}" if _TOOL_LABEL.fullmatch(name) else "Function Tool"
        )
        if tool is None or record.definition_fingerprint is None:
            return fallback

        try:
            if name not in fingerprints:
                # Remember an unavailable fingerprint for this listing only.
                fingerprints[name] = None
                fingerprints[name] = tool_definition_fingerprint(tool)
            if fingerprints[name] == record.definition_fingerprint:
                return (
                    describe_native_action(
                        self.hass,
                        tool["function"]["name"],
                        record.arguments,
                        lambda entities: self._visible_summary_entities(
                            entities, owner
                        ),
                    )
                    or fallback
                )
        except Exception as err:
            log_handled_failure(_LOGGER, "Could not describe a Scheduled Action", err)
        return fallback

    def _visible_summary_entities(
        self, entities: list[str], owner: ScheduledActionOwner
    ) -> list[str]:
        entities = present_entities(self.hass, entities)
        if owner.administrator:
            return entities
        if not entities or DATA_EXPOSED_ENTITIES not in self.hass.data:
            return []
        exposed = [
            {"entity_id": entity_id}
            for entity_id in entities
            if async_should_expose(self.hass, "conversation", entity_id)
        ]
        with bind_active_ha_context(Context(user_id=owner.user_id)):
            return [
                item["entity_id"]
                for item in filter_entities_for_active_user(self.hass, exposed)
            ]

    async def async_cancel_scheduled_action(
        self,
        owner: ScheduledActionOwner,
        action_id: str,
        *,
        authorized: Callable[[], bool] | None = None,
    ) -> dict[str, str]:
        """Durably cancel one authorized pending action under the claim lock."""
        if not valid_scheduled_action_id(action_id):
            raise HomeAssistantError("A complete scheduled action ID is required")
        from .execution_outcomes import (
            execution_completed,
            execution_operation,
            execution_rejected,
        )

        async with self._lock:
            if authorized is not None and not authorized():
                return {"status": "denied"}
            if not self.available:
                return {"status": "unavailable"}
            record = self._records.get(action_id)
            if action_id.startswith("legacy_"):
                record = next(
                    (
                        item
                        for item in self._records.values()
                        if _public_action_id(item.call_id) == action_id
                    ),
                    None,
                )
            if (
                record is None
                or not owner.allows(record)
                or record.status != _PENDING
                or self._execution_grace_expired(record, dt_util.utcnow())
            ):
                return {"status": "not_pending"}
            call_id = record.call_id
            updated = dict(self._records)
            del updated[call_id]
            with execution_operation(
                "delayed_job",
                {
                    "operation": "cancel",
                    "entry_id": owner.entry_id,
                    "subentry_id": owner.subentry_id,
                    "action_id": action_id,
                },
            ) as operation:
                try:
                    await self._async_save_records_transactionally(updated)
                except EffectReplayBlocked:
                    raise
                except BaseException as err:
                    if self.available and call_id not in self._records:
                        # A successful write with cancelled acknowledgement, or
                        # authoritative reconciliation after a write error.
                        execution_completed(operation.receipt)
                    elif self.available:
                        execution_rejected(operation.receipt)
                        if isinstance(err, Exception):
                            raise mark_mutation_failure(
                                HomeAssistantError(
                                    "The scheduled action could not be cancelled"
                                ),
                                "rejected",
                            ) from None
                    elif isinstance(err, Exception):
                        if operation.receipt is not None:
                            raise EffectOutcomeUnknown() from None
                        raise mark_mutation_failure(
                            HomeAssistantError(
                                "Cancellation could not be confirmed. Refresh before trying again."
                            ),
                            "uncertain",
                        ) from None
                    if not isinstance(err, Exception):
                        raise
                finally:
                    # Request cancellation only. Awaiting this task under
                    # the store lock or a maintenance lease can deadlock.
                    if (
                        self.available
                        and call_id not in self._records
                        and (task := self._tasks.pop(call_id, None)) is not None
                    ):
                        task.cancel()
        return {"status": "cancelled", "action_id": action_id}

    async def async_remove_agent(self, entry_id: str, subentry_id: str) -> None:
        """Erase scheduled arguments belonging to a deleted assistant."""
        async with self._lock:
            await self._async_remove_agent_locked(entry_id, subentry_id)

    async def _async_remove_agent_locked(self, entry_id: str, subentry_id: str) -> None:
        """Erase one agent from the current authoritative storage generation."""

        def matching_owner(parent: str, child: str) -> bool:
            return (parent, child) == (entry_id, subentry_id)

        if not self._setup_complete or self._quarantined:
            # Setup or quarantine can leave runtime records empty while storage
            # still contains jobs. Delete from the authoritative generation.
            await _async_remove_stored_agent_calls_locked(self.hass, matching_owner)
            return
        await self._async_remove_calls_locked(matching_owner)

    async def _async_remove_calls_locked(
        self, remove: Callable[[str, str], bool]
    ) -> None:
        removed = {
            call_id
            for call_id, record in self._records.items()
            if remove(record.entry_id, record.subentry_id)
        }
        if not removed:
            return
        await self._async_save_records_transactionally(
            {
                call_id: record
                for call_id, record in self._records.items()
                if call_id not in removed
            }
        )
        for call_id in removed:
            task = self._tasks.pop(call_id, None)
            if task is not None:
                task.cancel()

    async def _async_reconcile_failed_save(
        self, claim: DelayedToolCall | None = None
    ) -> None:
        """Reload validated calls after a failed write acknowledgement."""
        raw_data = await _async_load_delayed_queue_store(self._store)
        if raw_data is None or raw_data == {}:
            raw_calls = []
        elif not isinstance(raw_data, dict) or not isinstance(
            raw_data.get("calls"), list
        ):
            raise ValueError("persisted delayed Function Tools are malformed")
        else:
            raw_calls = raw_data["calls"]
        records: dict[str, DelayedToolCall] = {}
        for raw in raw_calls:
            record = DelayedToolCall.from_dict(raw)
            previous = self._records.get(record.call_id)
            task = self._tasks.get(record.call_id)
            # An already executing live worker still consumes admission capacity.
            # The claim caller has not dispatched yet. It can adopt the exact
            # durable claim when the save committed but its acknowledgement failed.
            live_execution = (
                previous is not None
                and previous.status == _EXECUTING
                and task is not None
                and not task.done()
            )
            if record.status != _EXECUTING or live_execution or record == claim:
                records[record.call_id] = record
                _validate_call_capacity(records)
        _validate_call_capacity(records)
        self._records = records

    def _invalidate_after_unreadable_store(self) -> None:
        """Stop the scheduler rather than execute from unverified in-memory data."""
        self._setup_complete = False
        self._started = False
        for call_id, task in self._tasks.items():
            self._invalidated_tasks.add(task)
            record = self._records.get(call_id)
            if record is None or record.status != _EXECUTING:
                task.cancel()
        self._records.clear()
        self._tasks.clear()

    async def _async_save_records_transactionally(
        self,
        records: dict[str, DelayedToolCall],
        *,
        claim: DelayedToolCall | None = None,
    ) -> None:
        """Save a complete generation and reconcile any failed acknowledgement."""

        async def reconcile() -> None:
            await self._async_reconcile_failed_save(claim)

        await _async_settle_transactional_save(
            self._store.async_save(self._storage_payload(records)),
            lambda: None,
            lambda: setattr(self, "_records", records),
            reconcile,
            self._invalidate_after_unreadable_store,
        )

    @callback
    def _handle_started(self, _event: Any = None) -> None:
        """Arm all pending calls after startup or an in-process setup."""
        if self._started or self._quarantined:
            return
        self._started = True
        for call_id in tuple(self._records):
            self._arm(call_id)

    @callback
    def _handle_stop(self, _event: Any = None) -> None:
        """Cancel only calls that have not crossed the persisted execution boundary."""
        self._started = False
        for call_id, task in tuple(self._tasks.items()):
            record = self._records.get(call_id)
            if record is None or record.status == _PENDING:
                task.cancel()

    def _arm(self, call_id: str) -> None:
        """Create one waiter for a pending persisted call."""
        if self._quarantined:
            return
        existing = self._tasks.get(call_id)
        if existing is not None and not existing.done():
            return
        record = self._records.get(call_id)
        if record is None or record.status != _PENDING:
            return
        self._tasks[call_id] = asyncio.create_task(
            self._async_wait_and_execute(call_id),
            name=f"{DOMAIN}-delayed-{call_id[:8]}",
            context=TaskContext(),
        )

    async def _async_wait_and_execute(self, call_id: str) -> None:
        """Wait until due, retry transient state failures, then execute once."""
        try:
            while self._started:
                record = self._records.get(call_id)
                if record is None or record.status != _PENDING:
                    return
                due_at = _parse_scheduled_utc(record.due_at)
                if due_at is None:
                    if await self._async_discard(call_id, "invalid due timestamp"):
                        return
                    await asyncio.sleep(_AGENT_RETRY_SECONDS)
                    continue
                remaining = (due_at - dt_util.utcnow()).total_seconds()
                if remaining > 0:
                    # asyncio sleeps against a monotonic clock. Bound the wait
                    # and resample wall time for both forward and backward jumps.
                    await asyncio.sleep(min(remaining, _CLOCK_RECHECK_SECONDS))
                    continue
                await asyncio.sleep(0)
                record = self._records.get(call_id)
                if record is None or record.status != _PENDING:
                    return
                retry = await self._async_execute_due(call_id)
                if not retry:
                    return
                await asyncio.sleep(_AGENT_RETRY_SECONDS)
        except asyncio.CancelledError:
            raise
        finally:
            if self._tasks.get(call_id) is asyncio.current_task():
                self._tasks.pop(call_id, None)

    async def _async_execute_due(self, call_id: str) -> bool:
        """Run due work in a fresh context, including its maintenance lease."""
        return await asyncio.create_task(
            self._async_execute_due_isolated(call_id), context=TaskContext()
        )

    async def _async_execute_due_isolated(self, call_id: str) -> bool:
        from .execution_outcomes import detached_execution

        record = self._records.get(call_id)
        if record is None or record.status != _PENDING:
            return False
        gate = get_agent_maintenance_gate(
            self.hass, record.entry_id, record.subentry_id
        )
        # Establish the lease inside the new context so nested tool execution can
        # reenter it safely even when a restore is waiting for exclusive access.
        with detached_execution():
            try:
                async with gate.shared():
                    return await self._async_execute_due_detached(call_id)
            except AgentRestoreRecoveryRequired:
                current = self._records.get(call_id)
                if current is None or current.status != _PENDING:
                    return False
                if self._execution_grace_expired(current, dt_util.utcnow()):
                    return not await self._async_discard(
                        call_id, "execution grace period expired"
                    )
                return True

    @staticmethod
    def _execution_grace_expired(record: DelayedToolCall, now: datetime) -> bool:
        if record.expires_at is None:
            return False
        expiry = _parse_scheduled_utc(record.expires_at)
        return expiry is None or now >= expiry

    async def _async_execute_due_detached(self, call_id: str) -> bool:
        """Re-authorize a due call against live configuration and execute it once."""
        record = self._records.get(call_id)
        if record is None or record.status != _PENDING:
            return False

        due_at = _parse_scheduled_utc(record.due_at)
        if due_at is None:
            return not await self._async_discard(call_id, "invalid due timestamp")
        now = dt_util.utcnow()
        if due_at > now:
            return True
        if self._execution_grace_expired(record, now):
            return not await self._async_discard(
                call_id, "execution grace period expired"
            )

        with bind_active_ha_context(Context(user_id=record.user_id)):
            entry = self.hass.config_entries.async_get_entry(record.entry_id)
            if entry is None or entry.disabled_by is not None:
                return not await self._async_discard(
                    call_id, "config entry is unavailable"
                )
            subentry = entry.subentries.get(record.subentry_id)
            if subentry is None or subentry.subentry_type != "conversation":
                return not await self._async_discard(
                    call_id, "conversation agent is unavailable"
                )

            try:
                current_tools = configured_function_tools_from_data(subentry.data)
            except Exception:
                _LOGGER.exception(
                    "Unable to validate live Function Tools for delayed `%s`",
                    record.tool_name,
                )
                return not await self._async_discard(
                    call_id, "live Function Tool configuration is invalid"
                )
            current_tool = next(
                (
                    tool
                    for tool in current_tools
                    if tool.get("spec", {}).get("name") == record.tool_name
                ),
                None,
            )
            if (
                current_tool is None
                or not function_tool_enabled(current_tool)
                or current_tool.get("function", {}).get("type") == "ha_llm"
            ):
                return not await self._async_discard(
                    call_id, "Function Tool was removed or disabled"
                )

            if record.user_id is not None:
                user = await self.hass.auth.async_get_user(record.user_id)
                if user is None or getattr(user, "is_active", True) is not True:
                    return not await self._async_discard(
                        call_id, "originating user is no longer active"
                    )

            if (
                record.definition_fingerprint is None
                or tool_definition_fingerprint(current_tool)
                != record.definition_fingerprint
            ):
                return not await self._async_discard(
                    call_id,
                    "Function Tool definition changed or the scheduled definition is unknown",
                )

            agent = self._resolve_agent(record.entry_id, record.subentry_id)
            if agent is None:
                return await self._async_retry_agent(record)

            try:
                current_tool = latest_function_tool_for_execution(agent, current_tool)
            except HomeAssistantError as err:
                return not await self._async_discard(
                    call_id, f"Function Tool is unavailable: {err}"
                )

            try:
                claimed, retry = await self._async_claim_due(call_id)
            except Exception:
                _LOGGER.exception(
                    "Unable to persist execution boundary for delayed Function Tool `%s`",
                    record.tool_name,
                )
                return True
            if not claimed:
                return retry

            execution_context = Context(user_id=record.user_id)
            delayed_context = SimpleNamespace(
                context=execution_context,
                device_id=record.device_id,
                **{_DELAYED_EXECUTION_MARKER: True},
            )
            try:
                with (
                    bind_active_ha_context(execution_context),
                    bind_request_satellite(record.satellite_id),
                    local_function_results(),
                ):
                    exposed_entities = get_exposed_entities(self.hass)
                    scheduled_utc = _parse_scheduled_utc(record.due_at)
                    if scheduled_utc is not None:
                        lateness = (dt_util.utcnow() - scheduled_utc).total_seconds()
                        if lateness > 300:
                            # Persisted IDs need not be UUIDs. Hash rather than
                            # logging an arbitrary stored identifier or tool data.
                            safe_id = hashlib.sha256(
                                record.call_id.encode()
                            ).hexdigest()[:12]
                            _LOGGER.warning(
                                "Delayed job %s is starting %.1f seconds late (scheduled UTC: %s)",
                                safe_id,
                                lateness,
                                scheduled_utc.isoformat(),
                            )
                    tool_result = await agent._execute_function_tool(
                        current_tool,
                        llm.ToolInput(
                            id=record.call_id,
                            tool_name=record.tool_name,
                            tool_args=deepcopy(record.arguments),
                            external=True,
                        ),
                        delayed_context,
                        exposed_entities,
                    )
            except Exception:
                _LOGGER.exception(
                    "Delayed Function Tool `%s` failed during execution",
                    record.tool_name,
                )
            else:
                payload = tool_result_data(tool_result)
                outcome = payload.get("result") if isinstance(payload, dict) else None
                results = outcome if isinstance(outcome, list) else [outcome]
                failed = sum(isinstance(item, ExecutionFailure) for item in results)
                if failed:
                    _LOGGER.warning(
                        "Delayed Function Tool `%s` reported %d failed result(s) out of %d",
                        record.tool_name,
                        failed,
                        len(results),
                    )
                else:
                    _LOGGER.info(
                        "Executed delayed Function Tool `%s`", record.tool_name
                    )
            finally:
                await self._async_finalize(call_id)
            return False

    async def _async_claim_due(self, call_id: str) -> tuple[bool, bool]:
        """Check time and cancellation under the storage lock before claiming work.

        Authorization and maintenance/storage gate waits can cross either clock
        boundary. An expired pending call is only removed transactionally; a
        failed removal propagates to the waiter's retry path, never execution.
        """
        async with self._lock:
            record = self._records.get(call_id)
            if record is None or record.status != _PENDING:
                return False, False
            now = dt_util.utcnow()
            due = _parse_scheduled_utc(record.due_at)
            if due is None:
                raise ValueError("invalid due timestamp")
            if due > now:
                return False, True
            updated = dict(self._records)
            if record.expires_at is not None:
                expiry = _parse_scheduled_utc(record.expires_at)
                if expiry is None or now >= expiry:
                    updated.pop(call_id)
                    await self._async_save_records_transactionally(updated)
                    _LOGGER.info(
                        "Cancelled delayed Function Tool `%s`: execution grace period expired",
                        record.tool_name,
                    )
                    return False, False
            claim = updated[call_id] = replace(record, status=_EXECUTING)
            try:
                await self._async_save_records_transactionally(updated, claim=claim)
            except Exception:
                if self._records.get(call_id) != claim:
                    raise
                _LOGGER.warning("Recovered a committed delayed Function Tool claim")
            return True, False

    async def _async_retry_agent(self, record: DelayedToolCall) -> bool:
        """Retry a transient agent reload without losing the persisted call."""
        if record.retry_count >= _MAX_AGENT_RETRIES:
            return not await self._async_discard(
                record.call_id, "conversation agent did not become available"
            )
        updated = replace(record, retry_count=record.retry_count + 1)
        try:
            await self._async_replace_record(updated)
        except Exception:
            _LOGGER.exception(
                "Unable to persist retry state for delayed Function Tool `%s`",
                record.tool_name,
            )
        # Keep the in-process retry safety budget monotonic across a Store outage.
        # The persisted record was reconciled above; this bounded retry counter is
        # a runtime safety overlay and will be included in the next healthy write.
        current_record = self._records.get(record.call_id)
        if (
            current_record is not None
            and current_record.status == record.status
            and current_record.retry_count <= updated.retry_count
        ):
            self._records[record.call_id] = updated
        return True

    def _resolve_agent(self, entry_id: str, subentry_id: str) -> Any | None:
        """Resolve the live conversation entity without retaining stale objects."""
        registry = er.async_get(self.hass)
        for registry_entry in registry.entities.values():
            if (
                registry_entry.platform == DOMAIN
                and registry_entry.domain == conversation.DOMAIN
                and registry_entry.config_entry_id == entry_id
                and registry_entry.config_subentry_id == subentry_id
            ):
                agent = conversation.async_get_agent(
                    self.hass, registry_entry.entity_id
                )
                if getattr(agent, "ready_for_scheduled_actions", False):
                    return agent
        return None

    async def _async_replace_record(self, record: DelayedToolCall) -> None:
        """Persist one state transition before updating live state."""
        async with self._lock:
            if record.call_id not in self._records:
                return
            updated = dict(self._records)
            updated[record.call_id] = record
            await self._async_save_records_transactionally(updated)

    async def _async_discard(self, call_id: str, reason: str) -> bool:
        """Persist cancellation, returning whether the pending call was removed."""
        record = self._records.get(call_id)
        if record is None:
            return True
        try:
            async with self._lock:
                updated = dict(self._records)
                updated.pop(call_id, None)
                await self._async_save_records_transactionally(updated)
        except Exception:
            _LOGGER.exception(
                "Unable to persist cancellation for delayed Function Tool `%s`",
                record.tool_name,
            )
            return self._setup_complete and call_id not in self._records
        _LOGGER.info(
            "Cancelled delayed Function Tool `%s`: %s", record.tool_name, reason
        )
        return True

    async def _async_finalize(self, call_id: str) -> None:
        """Remove an executed call without ever making it eligible for replay again."""
        record = self._records.get(call_id)
        if record is None:
            return
        try:
            async with self._lock:
                updated = dict(self._records)
                updated.pop(call_id, None)
                await self._async_save_records_transactionally(updated)
        except Exception:
            # The persisted record is already marked executing. Drop the live copy;
            # startup recovery also discards executing records, so this cannot replay.
            self._records.pop(call_id, None)
            _LOGGER.exception(
                "Unable to remove completed delayed Function Tool `%s` from storage; "
                "its executing tombstone will be discarded on next startup",
                record.tool_name,
            )

    @staticmethod
    def _storage_payload(
        records: dict[str, DelayedToolCall],
    ) -> dict[str, list[dict[str, Any]]]:
        return {"calls": [record.as_dict() for record in records.values()]}


async def async_setup_delayed_tools(hass: HomeAssistant) -> DelayedToolManager:
    """Set up the shared durable scheduler used by the base tool executor."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    existing = domain_data.get(DATA_DELAYED_TOOL_MANAGER)
    if isinstance(existing, DelayedToolManager):
        manager = existing
    else:
        manager = DelayedToolManager(hass)
        domain_data[DATA_DELAYED_TOOL_MANAGER] = manager
    await manager.async_setup()
    return manager
