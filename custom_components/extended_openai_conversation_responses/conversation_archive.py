"""Local, scoped and searchable conversation transcript archive."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta, tzinfo
from heapq import heappush, heapreplace
from itertools import chain
import json
import logging
from pathlib import Path
import re
import stat
import sys
from threading import RLock
from typing import Any, Protocol
import unicodedata
from uuid import uuid4
import weakref

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .agent_maintenance import get_agent_maintenance_gate
from .const import CONF_ARCHIVE_RETENTION_DAYS, DEFAULT_ARCHIVE_RETENTION_DAYS, DOMAIN
from .persistence_hardening import (
    StorageRepairRequired,
    _async_settle_transactional_save,
)
from .retrieval_text import (
    _stem as _shared_stem,
    word_character_projection,
    word_tokens,
)
from .runtime_hardening import MAX_MODEL_TOOL_RESULT_CHARACTERS
from .scope import ResolvedDataScope
from .storage_namespaces import ARCHIVE_PREFIX as STORAGE_KEY_PREFIX
from .strict_store import PropagatingWriteStore

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 2
_PARTITION_STORAGE_VERSION = 1

MAX_SEARCH_LIMIT = 50
MAX_SEARCH_OFFSET = 10_000
_SEARCH_CACHE_BYTES = 8 * 1024 * 1024
_SEARCH_CACHE_TURNS = 4096
_SEARCH_FEATURES: dict[int, tuple[Any, str, frozenset[str], int]] = {}
_SEARCH_FEATURE_BYTES = 0
_SEARCH_FEATURE_LOCK = RLock()
MAX_GET_LIMIT = 50
MAX_TEXT_LENGTH = 40_000
MAX_TITLE_LENGTH = 80
MAX_EXCERPT_LENGTH = 500
_ARCHIVE_MANAGERS = f"{DOMAIN}.archive_managers"
_ARCHIVE_RETENTION = f"{DOMAIN}.archive_retention"
_TOKEN_PATTERN = re.compile(r"\w+(?:[+#]+|(?:['\u2019-]\w+)*)", re.UNICODE)
_SPACE_PATTERN = re.compile(r"\s+")


def _scope_retention_allowed(
    scope: ResolvedDataScope, shared_archive_enabled: bool
) -> bool:
    """Return whether the current scope policy permits transcript retention."""
    return bool(
        scope.allows_retention
        and (scope.scope_type != "shared" or shared_archive_enabled)
    )


_STOP_WORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "did",
    "do",
    "does",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "our",
    "said",
    "say",
    "tell",
    "the",
    "to",
    "we",
    "what",
    "when",
    "where",
    "who",
    "why",
    "you",
    "your",
}


@dataclass(slots=True, frozen=True)
class ArchiveSession:
    """Content-free session metadata."""

    session_id: str
    home_assistant_conversation_id: str | None
    agent_subentry_id: str
    scope_id: str
    scope_type: str
    scope_source: str
    source_device_id: str | None
    started_at: str
    last_message_at: str
    title: str
    turn_count: int
    retention_state: str
    last_activity_at: str | None = None

    @property
    def has_saved_history(self) -> bool:
        """Whether the session contains at least one retained turn."""
        return self.retention_state == "retained" and self.turn_count > 0

    @property
    def retains_metadata(self) -> bool:
        """Whether this session belongs in the durable Archive."""
        return self.retention_state not in {"unretained", "unretained_private"}

    @property
    def is_private(self) -> bool:
        """Whether an explicit privacy boundary is active."""
        return self.retention_state in {"private", "unretained_private"}


@dataclass(slots=True, frozen=True, weakref_slot=True)
class ArchiveTurn:
    """Exactly one user message and final assistant response."""

    turn_id: str
    session_id: str
    run_id: str | None
    timestamp: str
    user_text: str
    assistant_text: str
    successful: bool
    sequence: int | None = None


def archive_sessions_past_retention(
    sessions: Iterable[ArchiveSession],
    turns: Iterable[ArchiveTurn],
    retention_days: int,
    *,
    now: datetime | None = None,
) -> set[str]:
    """Use the same expiry rule in retention maintenance and restore preview."""
    cutoff = (now or dt_util.utcnow()) - timedelta(days=max(1, retention_days))
    latest: dict[str, datetime] = {}
    for turn in turns:
        stamp = _parse_time(turn.timestamp)
        previous = latest.get(turn.session_id)
        if previous is None or stamp > previous:
            latest[turn.session_id] = stamp
    return {
        session.session_id
        for session in sessions
        if (
            _parse_time(session.last_activity_at or session.last_message_at)
            if session.is_private
            else latest.get(session.session_id) or _parse_time(session.last_message_at)
        )
        < cutoff
    }


def archive_session_inactive(
    session: ArchiveSession, now: datetime, inactivity_minutes: int
) -> bool:
    """Apply the same inactivity boundary during live turns and restore."""
    return (
        _parse_time(session.last_activity_at or session.last_message_at)
        + timedelta(minutes=max(1, inactivity_minutes))
        < now
    )


@callback
def async_start_archive_retention(hass: HomeAssistant) -> None:
    """Own one expiry timer for the configured assistants, including disabled entities."""
    if _ARCHIVE_RETENTION in hass.data:
        return
    token = object()

    async def _async_prune_archive_retention(
        _now: datetime, *, startup: bool = False
    ) -> None:
        from .agent_config import normalized_agent_integer
        from .temporary_memory import async_expire_existing_temporary_memory

        for entry in hass.config_entries.async_entries(DOMAIN):
            for subentry in tuple(entry.subentries.values()):
                owner = hass.data.get(_ARCHIVE_RETENTION)
                if owner is None or owner[0] is not token:
                    return
                if subentry.subentry_type != "conversation":
                    continue
                entry_id, subentry_id = entry.entry_id, subentry.subentry_id
                try:
                    gate = get_agent_maintenance_gate(hass, entry_id, subentry_id)
                    async with gate.shared(maintenance=True):
                        owner = hass.data.get(_ARCHIVE_RETENTION)
                        if owner is None or owner[0] is not token:
                            return
                        current = hass.config_entries.async_get_entry(entry_id)
                        agent = current.subentries.get(subentry_id) if current else None
                        if agent is None or agent.subentry_type != "conversation":
                            continue
                        if startup:
                            # Expiry applies to all configured assistants.
                            try:
                                await async_expire_existing_temporary_memory(
                                    hass, entry_id, subentry_id
                                )
                            except Exception:
                                _LOGGER.exception(
                                    "Startup Temporary Memory expiry failed for %s/%s",
                                    entry_id,
                                    subentry_id,
                                )
                        archive = await async_get_archive(hass, entry_id, subentry_id)
                        await archive.async_prune(
                            normalized_agent_integer(
                                agent.data,
                                CONF_ARCHIVE_RETENTION_DAYS,
                                DEFAULT_ARCHIVE_RETENTION_DAYS,
                            )
                        )
                except Exception:
                    _LOGGER.exception(
                        "Background conversation archive retention failed for %s/%s",
                        entry_id,
                        subentry_id,
                    )

    cancel = async_track_time_interval(
        hass, _async_prune_archive_retention, timedelta(days=1)
    )
    startup = hass.async_create_background_task(
        _async_prune_archive_retention(dt_util.utcnow(), startup=True),
        "EOAI startup assistant retention",
        eager_start=False,
    )

    @callback
    def stop(_event: Any) -> None:
        owner = hass.data.get(_ARCHIVE_RETENTION)
        if owner is not None and owner[0] is token:
            hass.data.pop(_ARCHIVE_RETENTION)
            cancel()
            if not startup.done():
                startup.cancel()

    remove_stop = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop)
    hass.data[_ARCHIVE_RETENTION] = (token, stop, remove_stop)


@callback
def async_stop_archive_retention(hass: HomeAssistant) -> None:
    """Retire the timer when the final provider is permanently removed."""
    if (owner := hass.data.get(_ARCHIVE_RETENTION)) is not None:
        _, stop, remove_stop = owner
        stop(None)
        remove_stop()


class ArchiveStorage(Protocol):
    """Partitioned persistence boundary."""

    async def async_load_metadata(self) -> dict[str, Any] | None: ...
    async def async_save_metadata(self, data: dict[str, Any]) -> None: ...
    async def async_load_partition(self, partition: str) -> dict[str, Any] | None: ...
    async def async_save_partition(
        self, partition: str, data: dict[str, Any]
    ) -> None: ...


def _stored_archive_partitions(metadata_path: str, prefix: str) -> set[str]:
    """Inventory exact owned monthly files when damaged metadata lost its index."""
    stem = prefix + ".turns."
    result: set[str] = set()
    for path in Path(metadata_path).parent.iterdir():
        if not path.name.startswith(stem):
            continue
        month = path.name[len(stem) :]
        if re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", month) and stat.S_ISREG(
            path.stat(follow_symlinks=False).st_mode
        ):
            result.add(month)
    return result


class _ArchiveMetadataStore(PropagatingWriteStore):
    """Guard the archive and its transaction journal against old writers."""

    _migrating_payload: dict[str, Any] | None = None
    migration_deferred = False

    async def _async_load_data(self) -> Any:
        try:
            payload = await super()._async_load_data()
        except OSError:
            previous = self._migrating_payload
            if (
                previous is None
                or "pending_append" in previous
                or "pending_partitions" in previous
                or self.corruption_unavailable
            ):
                raise
            # A plain v1 snapshot is readable during a write outage. Guard any
            # partition change until a v2 metadata write actually succeeds.
            self.migration_deferred = True
            return previous
        else:
            self.migration_deferred = False
            return payload
        finally:
            self._migrating_payload = None

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        if old_major_version != 1:
            raise NotImplementedError
        # The current decoder already reads both generations of v1 payloads.
        # Store commits the new version before the archive replays any journal.
        if isinstance(old_data, dict):
            self._migrating_payload = old_data
        return old_data

    async def async_save(self, data: dict[str, Any]) -> None:
        await super().async_save(data)
        self.migration_deferred = False

    def require_migrated(self) -> None:
        if self.migration_deferred:
            raise OSError("Archive metadata must be upgraded before changing history")


class HomeAssistantArchiveStorage:
    """Small metadata store plus independent monthly transcript stores."""

    def __init__(self, hass: HomeAssistant, entry_id: str, subentry_id: str) -> None:
        prefix = f"{STORAGE_KEY_PREFIX}.{entry_id}.{subentry_id}"
        self._hass = hass
        self._prefix = prefix
        self._agent_identity = (entry_id, subentry_id)
        # All production reads and writes enter through this metadata store.
        # An older reader must stop here before it can replace any partition.
        self._metadata = _ArchiveMetadataStore(
            hass,
            STORAGE_VERSION,
            f"{prefix}.metadata",
            private=True,
            atomic_writes=True,
            serialize_in_event_loop=False,
        ).bind_agent(entry_id, subentry_id)

    def _partition_store(self, partition: str) -> Store[dict[str, Any]]:
        return PropagatingWriteStore(
            self._hass,
            _PARTITION_STORAGE_VERSION,
            f"{self._prefix}.turns.{partition}",
            private=True,
            atomic_writes=True,
            serialize_in_event_loop=False,
        ).bind_agent(*self._agent_identity)

    async def async_load_metadata(self) -> dict[str, Any] | None:
        return await self._metadata.async_load()

    async def async_save_metadata(self, data: dict[str, Any]) -> None:
        await self._metadata.async_save(data)

    async def async_load_partition(self, partition: str) -> dict[str, Any] | None:
        return await self._partition_store(partition).async_load()

    async def async_save_partition(self, partition: str, data: dict[str, Any]) -> None:
        self._metadata.require_migrated()
        await self._partition_store(partition).async_save(data)

    async def async_remove_partition(self, partition: str) -> None:
        """Remove an obsolete monthly transcript store."""
        self._metadata.require_migrated()
        await self._partition_store(partition).async_remove()

    async def async_stage_journal_recovery(
        self,
        sessions: list[ArchiveSession],
        turns: list[ArchiveTurn],
        active: dict[str, str] | None,
    ) -> None:
        """Stage validated full-agent recovery without replaying damaged local intent."""
        gate = get_agent_maintenance_gate(self._hass, *self._agent_identity)
        if not gate.owns_exclusive() or not gate.recovery_required:
            raise RuntimeError(
                "Archive recovery requires exclusive quarantined maintenance"
            )
        gate.require_available()

        def prepare() -> tuple[
            dict[str, ArchiveSession], dict[str, list[ArchiveTurn]], dict[str, str]
        ]:
            checked_sessions, checked_turns = ConversationArchive.validate_backup_data(
                _archive_payload(sessions, turns),
                self._agent_identity[1],
                local_recovery=active is not None,
            )
            bindings = ConversationArchive.validate_recovery_bindings(
                active or {}, checked_sessions
            )
            turn_map = _replacement_turn_map(checked_turns)
            return (
                {session.session_id: session for session in checked_sessions},
                turn_map,
                bindings,
            )

        session_map, turn_map, bindings = await asyncio.to_thread(prepare)
        writer = self
        discovered: set[str] = set()
        try:
            old = await self.async_load_metadata()
        except StorageRepairRequired:
            # A validated full-agent journal owns replacement here. Keep HA's
            # corruption evidence and its ordinary write barrier intact; only
            # this correctly bound fresh writer may stage the recovery intent.
            old = None
            discovered = await asyncio.to_thread(
                _stored_archive_partitions, self._metadata.path, self._prefix
            )
            writer = HomeAssistantArchiveStorage(self._hass, *self._agent_identity)
        old_names: list[Any] = []
        if isinstance(old, dict):
            if isinstance(old.get("partitions"), list):
                old_names.extend(old["partitions"])
            if isinstance(old.get("pending_partitions"), dict):
                old_names.extend(old["pending_partitions"])
        partitions = ConversationArchive._partitions_for_turns(turn_map)
        changed = (
            partitions
            | discovered
            | {
                name
                for name in old_names
                if isinstance(name, str)
                and re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", name)
            }
        )
        pending = {
            name: ConversationArchive._partition_payload_for_state(name, turn_map)
            for name in sorted(changed)
        }
        # Old indexed months receive empty payloads if absent from the selected
        # snapshot. Failed writes leave the authoritative full-agent journal for
        # retry; normal Archive initialization validates and replays this intent.
        await writer.async_save_metadata(
            ConversationArchive._metadata_payload_for_state(
                session_map, bindings, partitions, pending
            )
        )


class ConversationArchive:
    """Concurrency-safe scoped archive with deterministic session privacy."""

    def __init__(self, storage: ArchiveStorage, agent_subentry_id: str) -> None:
        self._storage = storage
        self._agent_subentry_id = agent_subentry_id
        self._sessions: dict[str, ArchiveSession] = {}
        self._turns: dict[str, list[ArchiveTurn]] = defaultdict(list)
        self._active: dict[str, str] = {}
        self._partitions: set[str] = set()
        self._pending_partitions: set[str] = set()
        self._expired_pending_prune: set[str] = set()
        self._durable_metadata: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._initialized = False
        self._repair_required = False

    @property
    def read_available(self) -> bool:
        """Whether a validated archive generation is available for reads."""
        return self._initialized and not (
            isinstance(self._storage, HomeAssistantArchiveStorage)
            and self._storage._metadata.corruption_unavailable
        )

    @property
    def needs_storage_repair(self) -> bool:
        """Whether the archive requires repair before another ordinary write."""
        return self._repair_required

    def private_continuity_bindings(self) -> dict[str, str]:
        """Return content-free identities under the caller's exclusive restore lease."""
        return self._continuity_bindings("private")

    def retained_continuity_bindings(self) -> dict[str, str]:
        """Identify unchanged retained conversations under exclusive maintenance."""
        return self._continuity_bindings("retained")

    def retained_ha_default_scopes(self) -> dict[str, str]:
        """Identify retained Archive bindings keyed by their native conversation ID."""
        return {
            session.home_assistant_conversation_id: session.scope_id
            for key, session_id in self._active.items()
            if (session := self._sessions.get(session_id)) is not None
            and session.retention_state == "retained"
            and session.agent_subentry_id == self._agent_subentry_id
            and session.home_assistant_conversation_id
            and key == f"conversation:{session.home_assistant_conversation_id}"
        }

    def _continuity_bindings(self, retention_state: str) -> dict[str, str]:
        return {
            key.removeprefix("continuity:"): session.home_assistant_conversation_id
            for key, session_id in self._active.items()
            if key.startswith("continuity:")
            and (session := self._sessions.get(session_id)) is not None
            and session.retention_state == retention_state
            and session.home_assistant_conversation_id is not None
        }

    @property
    def initialized(self) -> bool:
        """Expose failed-generation recovery state to live reconciliation."""
        return self._initialized

    async def async_initialize(self) -> None:
        async with self._lock:
            if self._initialized:
                return
            loaded = await self._storage.async_load_metadata()
            self._durable_metadata = loaded
            data = loaded if loaded is not None else {}
            repair_required = not isinstance(data, dict)
            if repair_required:
                data = {}
            pending = data.get("pending_partitions")
            if "pending_append" in data:
                if "pending_partitions" in data:
                    raise ValueError("conversation archive transaction is corrupted")
                await self._async_replay_pending_append(data)
                data = {
                    key: value for key, value in data.items() if key != "pending_append"
                }
                await self._async_write_metadata(data)
            elif pending is not None:
                # Metadata is the transaction marker. Complete any interrupted
                # cross-store write before exposing archive state after restart.
                if not isinstance(pending, dict) or any(
                    not isinstance(partition, str)
                    or re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", partition) is None
                    or not isinstance(payload, dict)
                    or not isinstance(payload.get("turns"), list)
                    for partition, payload in pending.items()
                ):
                    raise ValueError("conversation archive transaction is corrupted")
                await self._async_validate_pending_transaction(data, pending)
                for partition, payload in sorted(pending.items()):
                    await self._storage.async_save_partition(partition, payload)
                data = {
                    key: value
                    for key, value in data.items()
                    if key != "pending_partitions"
                }
                await self._async_write_metadata(data)
            staged_sessions: dict[str, ArchiveSession] = {}
            staged_turns: dict[str, list[ArchiveTurn]] = defaultdict(list)
            private_titles = False
            normalized_records = False
            seen_turn_ids: set[str] = set()
            sessions = data.get("sessions")
            repair_required |= "sessions" in data and not isinstance(sessions, list)
            for raw in sessions if isinstance(sessions, list) else []:
                try:
                    session = _validated_session(raw)
                    if session.session_id in staged_sessions:
                        raise ValueError("duplicate session identifier")
                except TypeError, ValueError:
                    repair_required = True
                    _LOGGER.warning("Ignoring malformed conversation archive session")
                    normalized_records = True
                    continue
                if session.retention_state == "private" and session.title:
                    session = replace(session, title="")
                    private_titles = True
                staged_sessions[session.session_id] = session
            active = data.get("active")
            staged_active = {
                key: value
                for key, value in (active if isinstance(active, dict) else {}).items()
                if isinstance(key, str)
                and isinstance(value, str)
                and value in staged_sessions
            }
            repair_required |= "active" in data and (
                not isinstance(active, dict) or len(staged_active) != len(active)
            )
            partitions = data.get("partitions")
            staged_partitions = {
                value
                for value in (partitions if isinstance(partitions, list) else [])
                if isinstance(value, str)
                and re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", value)
            }
            repair_required |= "partitions" in data and (
                not isinstance(partitions, list)
                or len(staged_partitions) != len(partitions)
            )
            raw_payloads: dict[str, Any] = {}
            for partition in sorted(staged_partitions):
                payload = await self._storage.async_load_partition(partition)
                raw_payloads[partition] = payload
                turns = payload.get("turns") if isinstance(payload, dict) else None
                repair_required |= not isinstance(turns, list)
                for raw in turns if isinstance(turns, list) else []:
                    try:
                        turn = _validated_turn(raw)
                        if (
                            turn.session_id in staged_sessions
                            and staged_sessions[turn.session_id].retention_state
                            == "private"
                        ):
                            normalized_records = True
                            continue
                        if (
                            turn.turn_id in seen_turn_ids
                            or turn.timestamp[:7] != partition
                            or turn.session_id not in staged_sessions
                            or staged_sessions[turn.session_id].retention_state
                            == "private"
                        ):
                            raise ValueError("archive turn association is invalid")
                    except TypeError, ValueError:
                        repair_required = True
                        _LOGGER.warning("Ignoring malformed conversation archive turn")
                        normalized_records = True
                        continue
                    seen_turn_ids.add(turn.turn_id)
                    staged_turns[turn.session_id].append(turn)
            for session_id, session in staged_sessions.items():
                try:
                    staged_turns[session_id] = _ordered_session_turns(
                        staged_turns[session_id]
                    )
                except ValueError:
                    # Preserve ambiguous ordering evidence and block later writes.
                    repair_required = True
                    _LOGGER.warning("Conversation archive turn order needs repair")
                if session.turn_count != len(staged_turns[session_id]):
                    staged_sessions[session_id] = replace(
                        session, turn_count=len(staged_turns[session_id])
                    )
                    normalized_records = True
            # Retry/cancellation must never expose a partially loaded generation.
            # Persist legacy private-title cleanup before publishing normalized state.
            self._repair_required = repair_required
            if repair_required:
                # Privacy purges are independent of repair: remove only content
                # bound to validated private sessions, retaining all other raw data.
                private_ids = {
                    key
                    for key, session in staged_sessions.items()
                    if session.retention_state == "private"
                }
                if private_titles:
                    await self._async_write_metadata(
                        {
                            **data,
                            "sessions": [
                                {**raw, "title": ""}
                                if isinstance(raw, dict)
                                and isinstance(raw.get("session_id"), str)
                                and raw["session_id"] in private_ids
                                else raw
                                for raw in (sessions or [])
                            ],
                        }
                    )
                for partition, payload in raw_payloads.items():
                    if isinstance(payload, dict) and isinstance(
                        payload.get("turns"), list
                    ):
                        kept = [
                            raw
                            for raw in payload["turns"]
                            if not (
                                isinstance(raw, dict)
                                and isinstance(raw.get("session_id"), str)
                                and raw["session_id"] in private_ids
                            )
                        ]
                        if len(kept) != len(payload["turns"]):
                            await self._storage.async_save_partition(
                                partition, {**payload, "turns": kept}
                            )
            if normalized_records and not repair_required:
                # Commit repair through the same metadata journal boundary before
                # exposing it. Failed repair is retried from durable intent.
                repaired_partitions = {
                    partition: self._partition_payload_for_state(
                        partition, staged_turns
                    )
                    for partition in sorted(staged_partitions)
                }
                await self._async_write_metadata(
                    self._metadata_payload_for_state(
                        staged_sessions,
                        staged_active,
                        staged_partitions,
                        repaired_partitions,
                    )
                )
                for partition, payload in repaired_partitions.items():
                    await self._storage.async_save_partition(partition, payload)
            if (private_titles or normalized_records) and not repair_required:
                await self._async_write_metadata(
                    self._metadata_payload_for_state(
                        staged_sessions, staged_active, staged_partitions
                    )
                )
            self._sessions = staged_sessions
            self._turns = defaultdict(list, staged_turns)
            self._active = staged_active
            self._partitions = staged_partitions
            self._pending_partitions.clear()
            self._initialized = True

    async def _async_replay_pending_append(self, data: dict[str, Any]) -> None:
        """Replay a single planned turn over its validated monthly base."""
        intent = data["pending_append"]
        try:
            if (
                not isinstance(intent, dict)
                or set(intent) != {"turn", "partition_existed"}
                or not isinstance(intent["partition_existed"], bool)
            ):
                raise ValueError
            turn = _validated_turn(intent["turn"])
            month = turn.timestamp[:7]
            if (
                turn.sequence is None
                or not isinstance(data.get("partitions"), list)
                or month not in data["partitions"]
            ):
                raise ValueError
        except ValueError as err:
            raise ValueError("conversation archive transaction is corrupted") from err

        existing = await self._storage.async_load_partition(month)

        def merge() -> tuple[dict[str, Any], bool]:
            # Only a newly indexed month may be absent. An already-applied append
            # may be the only record in that newly indexed month.
            if existing is None and not intent["partition_existed"]:
                return {"turns": [intent["turn"]]}, True
            turns = existing.get("turns") if isinstance(existing, dict) else None
            if not isinstance(turns, list):
                raise ValueError("archive append base partition is invalid")
            matching_turns = [
                raw
                for raw in turns
                if isinstance(raw, dict) and raw.get("turn_id") == turn.turn_id
            ]
            if (
                len(matching_turns) > 1
                or (matching_turns and _validated_turn(matching_turns[0]) != turn)
                or (
                    not intent["partition_existed"]
                    and len(turns) != len(matching_turns)
                )
            ):
                raise ValueError("archive append conflicts with the base partition")
            if matching_turns:
                return {"turns": turns}, False
            return {"turns": [*turns, intent["turn"]]}, True

        try:
            payload, needs_write = await asyncio.to_thread(merge)
        except ValueError as err:
            raise ValueError("conversation archive transaction is corrupted") from err
        # The delta cannot repair a damaged base. Validate the complete result,
        # including counts, private associations and unchanged months, before any write.
        await self._async_validate_pending_transaction(
            data, {month: payload}, appended_turn=turn
        )
        if needs_write:
            await self._storage.async_save_partition(month, payload)

    async def _async_validate_pending_transaction(
        self,
        data: dict[str, Any],
        pending: dict[str, Any],
        *,
        appended_turn: ArchiveTurn | None = None,
    ) -> None:
        """Validate the complete candidate before the first replay write.

        Pending payloads override their destinations; those destinations need not
        load successfully. Unchanged partitions must still validate because the
        journal does not contain replacement evidence for them.
        """
        names = data.get("partitions", [])
        if (
            not isinstance(names, list)
            or any(
                not isinstance(name, str)
                or re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", name) is None
                for name in names
            )
            or len(set(names)) != len(names)
        ):
            raise ValueError("conversation archive transaction is corrupted")
        payloads = dict(pending)
        for name in names:
            if name not in payloads:
                payloads[name] = await self._storage.async_load_partition(name)

        def validate() -> None:
            for raw in data.get("sessions", []):
                _validated_session(raw)
            turns: list[Any] = []
            for name, payload in payloads.items():
                if not isinstance(payload, dict) or not isinstance(
                    payload.get("turns"), list
                ):
                    raise ValueError("archive partition is invalid")
                if name not in names and payload["turns"]:
                    raise ValueError("removed archive partitions must be empty")
                for raw in payload["turns"]:
                    turn = _validated_turn(raw)
                    if turn.timestamp[:7] != name:
                        raise ValueError("archive turn partition is invalid")
                    turns.append(raw)
            sessions, checked_turns = self.validate_backup_data(
                {"sessions": data.get("sessions", []), "turns": turns},
                self._agent_subentry_id,
                local_recovery=True,
            )
            if appended_turn is not None:
                previous_max = max(
                    (
                        turn.sequence
                        for turn in checked_turns
                        if turn.session_id == appended_turn.session_id
                        and turn.turn_id != appended_turn.turn_id
                        and turn.sequence is not None
                    ),
                    default=-1,
                )
                if appended_turn.sequence != previous_max + 1:
                    raise ValueError(
                        "archive append sequence does not follow stored turns"
                    )
            self.validate_recovery_bindings(data.get("active", {}), sessions)

        try:
            await asyncio.to_thread(validate)
        except (TypeError, ValueError) as err:
            raise ValueError("conversation archive transaction is corrupted") from err

    async def async_begin_session(
        self,
        session_key: str,
        scope: ResolvedDataScope,
        home_assistant_conversation_id: str | None,
        *,
        archive_enabled: bool,
        shared_archive_enabled: bool,
        inactivity_minutes: int,
        live_logical_conversation: bool = False,
        on_durable_write: Callable[[], None] | None = None,
    ) -> ArchiveSession | None:
        """Resolve the stable scope once and return the exact active session."""
        if not archive_enabled:
            return None
        async with self._lock:
            self._ensure_initialized()
            current = self._sessions.get(self._active.get(session_key, ""))
            now = dt_util.utcnow()
            may_retain = _scope_retention_allowed(scope, shared_archive_enabled)
            if (
                current is not None
                and (
                    not archive_session_inactive(current, now, inactivity_minutes)
                    or (live_logical_conversation and current.is_private)
                )
                and current.scope_id == scope.scope_id
                and current.home_assistant_conversation_id
                == home_assistant_conversation_id
                and current.retention_state != "closed"
                and (may_retain or current.retention_state != "retained")
                and not (may_retain and current.retention_state == "unretained")
            ):
                current = replace(current, last_activity_at=now.isoformat())
                # Live activity is authoritative even when a durable refresh fails.
                self._sessions[current.session_id] = current
                if current.is_private and current.retains_metadata:
                    await self._async_publish_session_locked(
                        session_key, current, on_durable_write=on_durable_write
                    )
                return current

            timestamp = now.isoformat()
            session = ArchiveSession(
                session_id=uuid4().hex,
                home_assistant_conversation_id=home_assistant_conversation_id,
                agent_subentry_id=self._agent_subentry_id,
                scope_id=scope.scope_id,
                scope_type=scope.scope_type,
                scope_source=scope.source,
                source_device_id=scope.device_id,
                started_at=timestamp,
                last_message_at=timestamp,
                last_activity_at=timestamp,
                title="",
                turn_count=0,
                retention_state="retained" if may_retain else "unretained",
            )
            await self._async_publish_session_locked(
                session_key, session, on_durable_write=on_durable_write
            )
            return session

    async def async_record_turn(
        self,
        session_id: str,
        *,
        run_id: str | None,
        user_text: str,
        assistant_text: str,
        successful: bool,
    ) -> ArchiveTurn | None:
        """Retain one turn using the Archive journal before publishing live state."""
        user_text = _clean_text(user_text)
        assistant_text = _clean_text(assistant_text)
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            timestamp = dt_util.utcnow().isoformat()
            if session.is_private:
                # A slow turn is active until completion too. Persist activity
                # without retaining content or changing the active binding.
                if timestamp != session.last_activity_at or self._pending_partitions:
                    session = replace(session, last_activity_at=timestamp)
                    self._sessions[session.session_id] = session
                    if session.retains_metadata:
                        await self._async_publish_session_locked(None, session)
                return None
            if session.retention_state != "retained":
                return None
            previous_turns = self._turns.get(session_id, [])
            upgraded_legacy = bool(previous_turns) and all(
                turn.sequence is None for turn in previous_turns
            )
            if upgraded_legacy:
                previous_turns = [
                    replace(turn, sequence=index)
                    for index, turn in enumerate(previous_turns)
                ]
            next_sequence = (
                max(
                    (
                        turn.sequence
                        for turn in previous_turns
                        if turn.sequence is not None
                    ),
                    default=-1,
                )
                + 1
            )
            turn = ArchiveTurn(
                turn_id=uuid4().hex,
                session_id=session_id,
                run_id=run_id,
                timestamp=timestamp,
                user_text=user_text,
                assistant_text=assistant_text,
                successful=successful,
                sequence=next_sequence,
            )
            sessions = dict(self._sessions)
            sessions[session_id] = replace(
                session,
                last_message_at=timestamp,
                last_activity_at=timestamp,
                title=session.title or _title(user_text),
                turn_count=session.turn_count + 1,
            )
            turns = dict(self._turns)
            turns[session_id] = [*previous_turns, turn]
            if upgraded_legacy or self._pending_partitions:
                # Legacy sequence upgrades and unfinished writes may affect multiple
                # months. The existing replacement journal can carry their full state.
                await self._async_commit_state_locked(
                    sessions=sessions,
                    turns=turns,
                    active=dict(self._active),
                    changed_partitions={timestamp[:7]}
                    | (
                        self._changed_partitions(previous_turns)
                        if upgraded_legacy
                        else set()
                    ),
                )
            else:
                await self._async_commit_append_locked(sessions, turns, turn)
            return turn

    async def async_make_private(self, session_id: str) -> dict[str, Any]:
        """Delete retained turns and journal the privacy boundary before publishing."""
        async with self._lock:
            session = self._require_session(session_id)
            if not session.retains_metadata:
                self._sessions[session_id] = replace(
                    session, retention_state="unretained_private"
                )
                return {
                    "private_mode_enabled": True,
                    "session_id": session_id,
                    "deleted_turns": 0,
                    "future_turns_retained": False,
                }
            turns = dict(self._turns)
            removed = list(turns.pop(session_id, ()))
            sessions = dict(self._sessions)
            sessions[session_id] = ArchiveSession(
                **{
                    **asdict(session),
                    "title": "",
                    "turn_count": 0,
                    "retention_state": "private",
                    "last_activity_at": dt_util.utcnow().isoformat(),
                }
            )
            await self._async_commit_state_locked(
                sessions=sessions,
                turns=turns,
                active=dict(self._active),
                changed_partitions=self._changed_partitions(removed),
            )
            return {
                "private_mode_enabled": True,
                "session_id": session_id,
                "deleted_turns": len(removed),
                "future_turns_retained": False,
            }

    async def async_resume_saving(
        self,
        session_key: str,
        previous_session_id: str,
        scope: ResolvedDataScope,
        *,
        shared_archive_enabled: bool,
    ) -> ArchiveSession:
        """End a private boundary and create a fresh retained session."""
        async with self._lock:
            previous = self._require_session(previous_session_id)
            timestamp = dt_util.utcnow().isoformat()
            session = ArchiveSession(
                session_id=uuid4().hex,
                home_assistant_conversation_id=previous.home_assistant_conversation_id,
                agent_subentry_id=self._agent_subentry_id,
                scope_id=scope.scope_id,
                scope_type=scope.scope_type,
                scope_source=scope.source,
                source_device_id=scope.device_id,
                started_at=timestamp,
                last_message_at=timestamp,
                last_activity_at=timestamp,
                title="",
                turn_count=0,
                retention_state=(
                    "retained"
                    if _scope_retention_allowed(scope, shared_archive_enabled)
                    else "unretained"
                ),
            )
            await self._async_publish_session_locked(session_key, session)
            return session

    async def async_query_snapshot(
        self,
        scope_id: str,
        query: Callable[..., dict[str, Any]],
        *args: Any,
        include_turns: bool = False,
    ) -> dict[str, Any]:
        """Run a trusted read on a retained snapshot and validate before publication.

        The worker receives detached containers of immutable records. Only returned
        rows are rechecked, so unrelated writes and appends do not invalidate a read.
        Ranking, paging and output shape remain the caller's responsibility.
        """
        async with self._lock:
            self._ensure_initialized()
            sessions = {
                key: session
                for key, session in self._sessions.items()
                if session.scope_id == scope_id
                and session.retention_state == "retained"
                and key not in self._expired_pending_prune
            }
            if include_turns:
                turns = {key: tuple(self._turns.get(key, ())) for key in sessions}
                snapshot_args = (sessions, turns, scope_id, *args)
            else:
                snapshot_args = (sessions, scope_id, *args)
        result = await asyncio.to_thread(query, *snapshot_args)
        # Deletion/privacy changes may occur while ranking. Never publish their old text.
        async with self._lock:
            self._ensure_initialized()
            for record in result.get("results", result.get("sessions", ())):
                session_id = record["session_id"]
                current = self._sessions.get(session_id)
                if (
                    current is None
                    or current.scope_id != scope_id
                    or current.retention_state != "retained"
                    or session_id in self._expired_pending_prune
                ):
                    raise HomeAssistantError(
                        "Archive changed during search; retry the query"
                    )
                if "turn_id" in record:
                    index = record["turn_index"]
                    original = turns[session_id][index]
                    current_turns = self._turns.get(session_id, ())
                    # Appends preserve indices. Pruning/replacement invalidates the
                    # cursor, so retry rather than scanning whole histories here.
                    if (
                        index >= len(current_turns)
                        or current_turns[index] is not original
                    ):
                        raise HomeAssistantError(
                            "Archive changed during search; retry the query"
                        )
        return result

    async def async_search(
        self,
        scope_id: str,
        query: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 5,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Perform bounded phrase/token lexical search within one exact scope."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be blank")
        limit = max(1, min(limit, MAX_SEARCH_LIMIT))
        offset = max(0, offset)
        if offset > MAX_SEARCH_OFFSET:
            raise ValueError(
                f"offset must not exceed {MAX_SEARCH_OFFSET}; narrow the query"
            )
        return await self.async_query_snapshot(
            scope_id,
            _search_archive_query,
            query,
            start_date,
            end_date,
            limit,
            offset,
            dt_util.DEFAULT_TIME_ZONE,
            include_turns=True,
        )

    async def async_get(
        self,
        scope_id: str,
        session_id: str,
        start_turn: int = 0,
        limit: int = 6,
        *,
        text_offset: int = 0,
    ) -> dict[str, Any]:
        """Return bounded turns only after exact ownership validation."""
        session = self._require_readable_session(scope_id, session_id)
        start_turn = max(0, start_turn)
        limit = max(1, min(limit, MAX_GET_LIMIT))
        turns = self._turns.get(session_id, [])
        result: dict[str, Any] = {
            "session": asdict(session),
            "turns": [],
            "start_turn": start_turn,
            "limit": limit,
            "text_offset": max(0, text_offset),
            "has_more": False,
            "next_start_turn": None,
            "next_text_offset": 0,
        }
        # Bound the actual serialized result, including JSON escaping and session
        # metadata, so the generic tool wrapper never cuts this structured page.
        budget = MAX_MODEL_TOOL_RESULT_CHARACTERS - 1024
        for index in range(start_turn, min(len(turns), start_turn + limit)):
            turn = turns[index]
            offset = max(0, text_offset) if index == start_turn else 0
            total = len(turn.user_text) + len(turn.assistant_text)
            if offset > total:
                raise ValueError("text_offset exceeds turn text length")

            def chunk(
                length: int, turn: ArchiveTurn = turn, offset: int = offset
            ) -> dict[str, Any]:
                user_end = min(len(turn.user_text), offset + length)
                return {
                    **_turn_payload(turn),
                    "user_text": turn.user_text[
                        min(offset, len(turn.user_text)) : user_end
                    ],
                    "assistant_text": turn.assistant_text[
                        max(0, offset - len(turn.user_text)) : max(
                            0, offset + length - len(turn.user_text)
                        )
                    ],
                }

            result["turns"].append(chunk(total - offset))
            result.update(has_more=True, next_start_turn=index + 1, next_text_offset=0)
            if len(json.dumps(result, ensure_ascii=False)) > budget:
                if len(result["turns"]) > 1:
                    result["turns"].pop()
                    result.update(next_start_turn=index, next_text_offset=0)
                    return result
                low, high = 0, total - offset
                while low < high:
                    size = (low + high + 1) // 2
                    result["turns"][0] = chunk(size)
                    result.update(next_start_turn=index, next_text_offset=offset + size)
                    if len(json.dumps(result, ensure_ascii=False)) <= budget:
                        low = size
                    else:
                        high = size - 1
                if low == 0:
                    raise ValueError("archive page metadata exceeds result limit")
                result["turns"][0] = chunk(low)
                result.update(next_start_turn=index, next_text_offset=offset + low)
                return result
        next_turn = start_turn + len(result["turns"])
        more = next_turn < len(turns)
        result.update(has_more=more, next_start_turn=next_turn if more else None)
        return result

    async def async_get_before(
        self,
        scope_id: str,
        session_id: str,
        before_turn: int,
        limit: int = 6,
        *,
        before_text_offset: int = 0,
    ) -> dict[str, Any]:
        """Read the nearest earlier fragments without changing forward paging.

        The exclusive (turn, text-offset) cursor is authoritative. Iterate
        backwards and return fragments in chronological order with an exact
        starting cursor for the next backwards request.
        """
        session = self._require_readable_session(scope_id, session_id)
        turns = self._turns.get(session_id, [])
        before_turn = max(0, int(before_turn))
        before_text_offset = max(0, int(before_text_offset))
        limit = max(1, min(limit, MAX_GET_LIMIT))
        if before_turn > len(turns) or (
            before_turn == len(turns) and before_text_offset
        ):
            raise ValueError("Invalid archive backward cursor")
        if before_turn < len(turns):
            turn = turns[before_turn]
            if before_text_offset > len(turn.user_text) + len(turn.assistant_text):
                raise ValueError("Invalid archive backward text offset")

        result: dict[str, Any] = {
            "session": asdict(session),
            "turns": [],
            "start_turn": before_turn,
            "limit": limit,
            "text_offset": before_text_offset,
            "has_more": before_turn < len(turns),
            "next_start_turn": before_turn,
            "next_text_offset": before_text_offset,
        }
        budget = MAX_MODEL_TOOL_RESULT_CHARACTERS - 1024
        index = before_turn if before_text_offset else before_turn - 1
        for _ in range(limit):
            if index < 0:
                break
            turn = turns[index]
            user_length = len(turn.user_text)
            end = (
                before_text_offset
                if index == before_turn
                else user_length + len(turn.assistant_text)
            )

            def chunk(
                start: int, source: ArchiveTurn = turn, end_offset: int = end
            ) -> dict[str, Any]:
                source_user_length = len(source.user_text)
                return {
                    **_turn_payload(source),
                    "user_text": source.user_text[
                        min(start, source_user_length) : min(
                            end_offset, source_user_length
                        )
                    ],
                    "assistant_text": source.assistant_text[
                        max(0, start - source_user_length) : max(
                            0, end_offset - source_user_length
                        )
                    ],
                }

            prior_start = result["start_turn"]
            prior_offset = result["text_offset"]
            result["turns"].insert(0, chunk(0))
            result["start_turn"] = index
            result["text_offset"] = 0
            if len(json.dumps(result, ensure_ascii=False)) > budget:
                if len(result["turns"]) > 1:
                    result["turns"].pop(0)
                    result["start_turn"] = prior_start
                    result["text_offset"] = prior_offset
                    break
                # Choose the longest fitting suffix, with positive progress.
                low, high = 0, end
                while low < high:
                    start = (low + high) // 2
                    result["turns"][0] = chunk(start)
                    result["text_offset"] = start
                    if len(json.dumps(result, ensure_ascii=False)) <= budget:
                        high = start
                    else:
                        low = start + 1
                if low == end:
                    raise ValueError("archive page metadata exceeds result limit")
                result["turns"][0] = chunk(low)
                result["text_offset"] = low
                break
            index -= 1
        return result

    async def async_list_sessions(
        self, scope_id: str, *, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        """List bounded content-free metadata for one scope."""
        limit = max(1, min(limit, MAX_SEARCH_LIMIT))
        sessions = [
            session
            for session in self._sessions.values()
            if session.scope_id == scope_id
            and session.has_saved_history
            and session.session_id not in self._expired_pending_prune
        ]
        sessions.sort(key=lambda item: _parse_time(item.last_message_at), reverse=True)
        page = sessions[max(0, offset) : max(0, offset) + limit]
        return {
            "sessions": [asdict(session) for session in page],
            "offset": max(0, offset),
            "limit": limit,
            "has_more": len(sessions) > max(0, offset) + limit,
        }

    async def async_delete_session(
        self, scope_id: str, session_id: str
    ) -> dict[str, int]:
        """Delete one exact owned transcript using the restart-safe journal."""
        async with self._lock:
            self._require_owned_session(scope_id, session_id)
            sessions = dict(self._sessions)
            del sessions[session_id]
            turns = dict(self._turns)
            removed = list(turns.pop(session_id, ()))
            active = {
                key: value for key, value in self._active.items() if value != session_id
            }
            await self._async_commit_state_locked(
                sessions=sessions,
                turns=turns,
                active=active,
                changed_partitions=self._changed_partitions(removed),
            )
            return {"deleted_sessions": 1, "deleted_turns": len(removed)}

    async def async_clear_scope(
        self, scope_id: str, *, confirm: bool
    ) -> dict[str, int]:
        """Clear one scope transactionally after explicit confirmation."""
        if not confirm:
            raise ValueError("Explicit confirmation is required")
        async with self._lock:
            targets = {
                session.session_id
                for session in self._sessions.values()
                if session.scope_id == scope_id and not session.is_private
            }
            if not targets:
                return {"deleted_sessions": 0, "deleted_turns": 0}
            sessions = {
                session_id: session
                for session_id, session in self._sessions.items()
                if session_id not in targets
            }
            turns = dict(self._turns)
            removed = [
                turn for session_id in targets for turn in turns.pop(session_id, ())
            ]
            active = {
                key: value
                for key, value in self._active.items()
                if value not in targets
            }
            await self._async_commit_state_locked(
                sessions=sessions,
                turns=turns,
                active=active,
                changed_partitions=self._changed_partitions(removed),
            )
            return {
                "deleted_sessions": len(targets),
                "deleted_turns": len(removed),
            }

    async def async_delete_selected(
        self, scope_id: str, session_ids: list[str], *, confirm: bool
    ) -> dict[str, int]:
        """Delete selected owned sessions transactionally after confirmation."""
        if not confirm:
            raise ValueError("Explicit confirmation is required")
        if not session_ids or len(session_ids) > MAX_SEARCH_LIMIT:
            raise ValueError(f"session_ids must contain 1 to {MAX_SEARCH_LIMIT} IDs")
        async with self._lock:
            return await self._async_delete_owned_locked(scope_id, set(session_ids))

    async def _async_delete_owned_locked(
        self, scope_id: str, targets: set[str]
    ) -> dict[str, int]:
        """Delete an owned set in one journal transaction under the caller's lock."""
        # Validate every supplied ID while holding the same lock used for commit.
        for session_id in targets:
            self._require_owned_session(scope_id, session_id)
        sessions = {
            session_id: session
            for session_id, session in self._sessions.items()
            if session_id not in targets
        }
        turns = dict(self._turns)
        removed = [turn for session_id in targets for turn in turns.pop(session_id, ())]
        active = {
            key: value for key, value in self._active.items() if value not in targets
        }
        await self._async_commit_state_locked(
            sessions=sessions,
            turns=turns,
            active=active,
            changed_partitions=self._changed_partitions(removed),
        )
        return {
            "deleted_sessions": len(targets),
            "deleted_turns": len(removed),
        }

    async def async_delete_date_range(
        self,
        scope_id: str,
        start_date: str,
        end_date: str,
        *,
        confirm: bool,
        allow_outside_range: bool = True,
    ) -> dict[str, Any]:
        """Delete matching whole sessions or return the review required for outside turns."""
        if not confirm:
            raise ValueError("Explicit confirmation is required")
        if not isinstance(start_date, str) or not isinstance(end_date, str):
            raise ValueError("a valid start_date and end_date are required")
        start, end = _local_date_bounds(start_date, end_date, dt_util.DEFAULT_TIME_ZONE)
        async with self._lock:
            self._ensure_initialized()
            sessions = _sessions_in_delete_range(
                (
                    session
                    for session in self._sessions.values()
                    if allow_outside_range
                    or session.session_id not in self._expired_pending_prune
                ),
                scope_id,
                start,
                end,
            )
            if not sessions:
                return {"deleted_sessions": 0, "deleted_turns": 0}
            if not allow_outside_range:
                preview = await asyncio.to_thread(
                    _describe_date_range_deletion, sessions, self._turns, start, end
                )
                if preview.get("status") == "user_confirmation_required":
                    return preview
            targets = {session.session_id for session in sessions}
            return await self._async_delete_owned_locked(scope_id, targets)

    async def async_preview_delete_date_range(
        self, scope_id: str, start_date: str, end_date: str
    ) -> dict[str, Any]:
        """Describe whole sessions and their earlier turns without deleting them."""
        start, end = _local_date_bounds(start_date, end_date, dt_util.DEFAULT_TIME_ZONE)
        return await self.async_query_snapshot(
            scope_id, _preview_delete_range, start, end, include_turns=True
        )

    async def async_prune(self, retention_days: int) -> dict[str, int]:
        """Delete expired transcript sessions using the restart-safe journal."""
        async with self._lock:
            targets = await asyncio.to_thread(
                archive_sessions_past_retention,
                tuple(self._sessions.values()),
                chain.from_iterable(tuple(self._turns.values())),
                retention_days,
            )
            # Expiry takes effect for reads even if the journal cannot be written.
            self._expired_pending_prune = targets
            # A previous prune can have committed its deletion intent before
            # a partition write failed. Retry that work even with no new targets.
            if not targets and not self._pending_partitions:
                return {"deleted_sessions": 0, "deleted_turns": 0}
            sessions = {
                session_id: session
                for session_id, session in self._sessions.items()
                if session_id not in targets
            }
            turns = dict(self._turns)
            removed = [
                turn for session_id in targets for turn in turns.pop(session_id, ())
            ]
            active = {
                key: value
                for key, value in self._active.items()
                if value not in targets
            }
            await self._async_commit_state_locked(
                sessions=sessions,
                turns=turns,
                active=active,
                changed_partitions=self._changed_partitions(removed),
            )
            self._expired_pending_prune = set()
            return {
                "deleted_sessions": len(targets),
                "deleted_turns": len(removed),
            }

    def active_session(self, session_key: str) -> ArchiveSession | None:
        return self._sessions.get(self._active.get(session_key, ""))

    def stats(self) -> dict[str, Any]:
        return {
            "backend": "home_assistant_monthly_store",
            "storage_version": STORAGE_VERSION,
            "session_count": len(self._sessions),
            "turn_count": sum(len(turns) for turns in self._turns.values()),
            "partition_count": len(self._partitions),
            **({"storage_repair_required": True} if self._repair_required else {}),
        }

    def scope_counts(self) -> dict[str, int]:
        """Return retained session totals grouped by data scope."""
        counts: dict[str, int] = {}
        for session in self._sessions.values():
            if (
                not session.has_saved_history
                or session.session_id in self._expired_pending_prune
            ):
                continue
            counts[session.scope_id] = counts.get(session.scope_id, 0) + 1
        return counts

    async def async_backup_data(self) -> dict[str, Any]:
        """Return retained archive content without active-session runtime state."""
        async with self._lock:
            self._require_writable()
            sessions = [
                session
                for session in self._sessions.values()
                if session.retains_metadata
                and session.session_id not in self._expired_pending_prune
            ]
            retained_ids = {session.session_id for session in sessions}
            turns = [
                turn
                for session_id, items in self._turns.items()
                if session_id in retained_ids
                for turn in items
            ]
            return await asyncio.to_thread(_archive_payload, sessions, turns)

    async def async_recovery_data(self) -> dict[str, Any]:
        """Capture same-agent recovery state; portable exports omit active bindings."""
        async with self._lock:
            self._require_writable()
            # Records are immutable; the lock protects selection and serialization.
            sessions = [
                session
                for session in self._sessions.values()
                if session.retention_state != "unretained_private"
            ]
            session_ids = {session.session_id for session in sessions}
            turns = [turn for items in self._turns.values() for turn in items]
            payload = await asyncio.to_thread(_archive_payload, sessions, turns)
            payload["active"] = {
                key: session_id
                for key, session_id in self._active.items()
                if session_id in session_ids
            }
            return payload

    @staticmethod
    def validate_recovery_bindings(
        active: Any, sessions: list[ArchiveSession]
    ) -> dict[str, str]:
        """Accept only local bindings to validated sessions in the same snapshot."""
        session_ids = {item.session_id for item in sessions}
        if not isinstance(active, dict) or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or value not in session_ids
            for key, value in active.items()
        ):
            raise ValueError("archive recovery bindings are invalid")
        return dict(active)

    @staticmethod
    def validate_backup_data(
        data: Any, target_agent_id: str, *, local_recovery: bool = False
    ) -> tuple[list[ArchiveSession], list[ArchiveTurn]]:
        """Validate portable data or content-free local recovery session metadata."""
        if not isinstance(data, dict) or set(data) != {"sessions", "turns"}:
            raise ValueError("archive data is incomplete or corrupted")
        if not isinstance(data["sessions"], list) or not isinstance(
            data["turns"], list
        ):
            raise ValueError("archive sessions and turns must be lists")
        sessions: list[ArchiveSession] = []
        session_ids: set[str] = set()
        for raw in data["sessions"]:
            if not isinstance(raw, dict):
                raise ValueError("archive session must be an object")
            raw = {**raw, "agent_subentry_id": target_agent_id}
            session = _validated_session(raw, allow_unretained=local_recovery)
            if session.session_id in session_ids:
                raise ValueError("archive session metadata is invalid")
            session_ids.add(session.session_id)
            sessions.append(
                replace(session, title="")
                if session.retention_state in {"private", "unretained"}
                else session
            )

        turns: list[ArchiveTurn] = []
        turn_ids: set[str] = set()
        per_session: dict[str, int] = defaultdict(int)
        session_turns: dict[str, list[ArchiveTurn]] = defaultdict(list)
        private_session_ids = {
            session.session_id
            for session in sessions
            if session.retention_state == "private"
        }
        unretained_session_ids = {
            session.session_id
            for session in sessions
            if session.retention_state == "unretained"
        }
        for raw in data["turns"]:
            if not isinstance(raw, dict):
                raise ValueError("archive turn must be an object")
            turn = _validated_turn(raw)
            turn = replace(turn, timestamp=_parse_time(turn.timestamp).isoformat())
            if turn.session_id in private_session_ids:
                raise ValueError("private archive sessions cannot contain turns")
            if turn.session_id in unretained_session_ids:
                raise ValueError("unretained archive sessions cannot contain turns")
            if turn.turn_id in turn_ids or turn.session_id not in session_ids:
                raise ValueError("archive turn metadata is invalid")
            turn_ids.add(turn.turn_id)
            per_session[turn.session_id] += 1
            session_turns[turn.session_id].append(turn)
            turns.append(turn)
        if any(
            session.turn_count != per_session[session.session_id]
            for session in sessions
        ):
            raise ValueError("archive turn counts do not match session metadata")
        for items in session_turns.values():
            _ordered_session_turns(items)
        return sessions, turns

    async def async_replace_backup(
        self,
        sessions: list[ArchiveSession],
        turns: list[ArchiveTurn],
        *,
        active: dict[str, str] | None = None,
    ) -> None:
        """Replace durable archive data through the same restart-safe journal."""
        sessions, turns = await asyncio.to_thread(
            lambda: self.validate_backup_data(
                _archive_payload(sessions, turns),
                self._agent_subentry_id,
                local_recovery=active is not None,
            )
        )
        bindings = (
            self.validate_recovery_bindings(active, sessions)
            if active is not None
            else {}
        )
        async with self._lock:
            self._ensure_initialized()
            session_map = {
                session.session_id: replace(session, title="")
                if session.retention_state == "private"
                else session
                for session in sessions
            }
            turn_map = _replacement_turn_map(turns)
            new_partitions = {turn.timestamp[:7] for turn in turns}
            await self._async_commit_state_locked(
                sessions=session_map,
                turns=dict(turn_map),
                active=bindings,
                changed_partitions=set(self._partitions) | new_partitions,
                replacing=True,
            )

    def _require_session(self, session_id: str) -> ArchiveSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError("conversation session not found")
        return session

    def _require_readable_session(
        self, scope_id: str, session_id: str
    ) -> ArchiveSession:
        if session_id in self._expired_pending_prune:
            raise ValueError("conversation session not found")
        return self._require_owned_session(scope_id, session_id)

    def _require_owned_session(self, scope_id: str, session_id: str) -> ArchiveSession:
        session = self._require_session(session_id)
        if session.scope_id != scope_id or session.retention_state != "retained":
            raise ValueError("conversation session not found")
        return session

    async def _async_publish_session_locked(
        self,
        session_key: str | None,
        session: ArchiveSession,
        *,
        on_durable_write: Callable[[], None] | None = None,
    ) -> None:
        """Publish session metadata and an optional active binding."""
        self._require_writable()
        if not session.retains_metadata and not self._pending_partitions:
            self._sessions[session.session_id] = session
            if session_key is not None:
                self._active[session_key] = session.session_id
            return
        sessions = {**self._sessions, session.session_id: session}
        active = (
            {**self._active, session_key: session.session_id}
            if session_key is not None
            else self._active
        )
        metadata = await asyncio.to_thread(
            self._metadata_with_pending,
            sessions,
            active,
            self._turns,
            set(self._partitions),
            set(self._pending_partitions),
        )

        def publish() -> None:
            self._sessions[session.session_id] = session
            if session_key is not None:
                self._active[session_key] = session.session_id

        await self._async_commit_metadata(metadata, publish)
        if on_durable_write is not None:
            on_durable_write()

    async def _async_write_metadata(self, metadata: dict[str, Any]) -> None:
        """Record the exact durable generation, independently of live activity."""
        await self._storage.async_save_metadata(metadata)
        self._durable_metadata = metadata

    async def _async_commit_metadata(
        self,
        metadata: dict[str, Any],
        publish: Callable[[], None],
        *,
        record_execution: bool = False,
    ) -> None:
        previous = self._durable_metadata

        async def reconcile() -> None:
            persisted = await self._storage.async_load_metadata()
            if persisted == metadata:
                self._durable_metadata = metadata
                publish()
            elif persisted != previous:
                raise ValueError("conversation archive metadata cannot be reconciled")

        await _async_settle_transactional_save(
            self._async_write_metadata(metadata),
            lambda: None,
            publish,
            reconcile,
            self._invalidate_after_unreadable_metadata,
            record_execution=record_execution,
        )

    @staticmethod
    def _metadata_payload_for_state(
        sessions: dict[str, ArchiveSession],
        active: dict[str, str],
        partitions: set[str],
        pending_partitions: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Serialize one candidate Archive metadata state."""
        persisted_sessions = {
            session.session_id: session
            for session in sessions.values()
            if session.retains_metadata
        }
        payload: dict[str, Any] = {
            "sessions": [
                _session_payload(session) for session in persisted_sessions.values()
            ],
            "active": {
                key: value
                for key, value in active.items()
                if value in persisted_sessions
            },
            "partitions": sorted(partitions),
        }
        if pending_partitions is not None:
            payload["pending_partitions"] = pending_partitions
        return payload

    @staticmethod
    def _partition_payload_for_state(
        partition: str, turns: dict[str, list[ArchiveTurn]]
    ) -> dict[str, Any]:
        return {
            "turns": [
                _turn_payload(turn)
                for session_turns in turns.values()
                for turn in session_turns
                if turn.timestamp.startswith(partition)
            ]
        }

    @staticmethod
    def _partitions_for_turns(turns: dict[str, list[ArchiveTurn]]) -> set[str]:
        return {
            turn.timestamp[:7]
            for session_turns in turns.values()
            for turn in session_turns
        }

    @staticmethod
    def _changed_partitions(turns: list[ArchiveTurn]) -> set[str]:
        return {turn.timestamp[:7] for turn in turns}

    def _metadata_with_pending(
        self,
        sessions: dict[str, ArchiveSession],
        active: dict[str, str],
        turns: dict[str, list[ArchiveTurn]],
        partitions: set[str],
        pending_names: set[str],
    ) -> dict[str, Any]:
        if (
            pending_names
            and self._durable_metadata is not None
            and "pending_append" in self._durable_metadata
        ):
            return {
                **self._metadata_payload_for_state(sessions, active, partitions),
                "pending_append": self._durable_metadata["pending_append"],
            }
        pending = (
            {
                name: self._partition_payload_for_state(name, turns)
                for name in sorted(pending_names)
            }
            if pending_names
            else None
        )
        return self._metadata_payload_for_state(sessions, active, partitions, pending)

    def _prepare_commit_payloads(
        self,
        sessions: dict[str, ArchiveSession],
        active: dict[str, str],
        turns: dict[str, list[ArchiveTurn]],
        previous_partitions: set[str],
        pending_names: set[str],
    ) -> tuple[set[str], dict[str, dict[str, Any]], dict[str, Any]]:
        """Build only changed partition payloads in one pass over the candidate."""
        partitions: set[str] = set()
        pending: dict[str, dict[str, Any]] = {
            name: {"turns": []} for name in pending_names
        }
        for session_turns in turns.values():
            for turn in session_turns:
                month = turn.timestamp[:7]
                partitions.add(month)
                if month in pending:
                    pending[month]["turns"].append(_turn_payload(turn))
        for name in previous_partitions - partitions:
            pending.setdefault(name, {"turns": []})
        return (
            partitions,
            {name: pending[name] for name in sorted(pending)},
            self._metadata_payload_for_state(sessions, active, partitions),
        )

    async def _async_remove_partition_locked(self, partition: str) -> None:
        """Remove one obsolete monthly store when the storage boundary supports it."""
        remover = getattr(self._storage, "async_remove_partition", None)
        if callable(remover):
            await remover(partition)
            return

        # Compatibility for custom/test storage boundaries that predate the explicit
        # removal method. Archive callers do not need to know about Store internals.
        store_factory = getattr(self._storage, "_partition_store", None)
        if not callable(store_factory):
            return
        store = store_factory(partition)
        remove = getattr(store, "async_remove", None)
        if callable(remove):
            await remove()

    async def _async_commit_append_locked(
        self,
        sessions: dict[str, ArchiveSession],
        turns: dict[str, list[ArchiveTurn]],
        turn: ArchiveTurn,
    ) -> None:
        """Journal one turn, then write its full monthly partition only once."""
        self._require_writable()
        month = turn.timestamp[:7]
        partitions = self._partitions | {month}

        def prepare() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
            metadata = self._metadata_payload_for_state(
                sessions, self._active, partitions
            )
            payload = self._partition_payload_for_state(month, self._turns)
            appended = _turn_payload(turn)
            payload["turns"].append(appended)
            intent = {
                **metadata,
                "pending_append": {
                    "turn": appended,
                    "partition_existed": month in self._partitions,
                },
            }
            return metadata, intent, payload

        metadata, intent, payload = await asyncio.to_thread(prepare)

        def publish_intent() -> None:
            self._sessions = sessions
            self._turns = defaultdict(list, turns)
            self._partitions = partitions
            self._pending_partitions = {month}

        await self._async_commit_metadata(intent, publish_intent, record_execution=True)
        await self._storage.async_save_partition(month, payload)
        await self._async_commit_metadata(metadata, self._pending_partitions.clear)

    async def _async_commit_state_locked(
        self,
        *,
        sessions: dict[str, ArchiveSession],
        turns: dict[str, list[ArchiveTurn]],
        active: dict[str, str],
        changed_partitions: set[str],
        replacing: bool = False,
    ) -> None:
        """Journal candidate Archive state before publishing it in memory.

        The first metadata replacement commits intent even if acknowledgement
        fails. Reconcile against persisted metadata before reporting that error;
        later publications must retain the journal that restart will complete.
        Partition/final-metadata failures preserve the published durable intent.
        """
        self._ensure_initialized()
        if not replacing:
            self._require_writable()
        previous_partitions = set(self._partitions)
        previous_pending = set(self._pending_partitions)
        # Candidate dictionaries belong to this locked mutation. Published turn
        # lists are replaced wholesale and their records are immutable.
        partitions, pending, metadata = await asyncio.to_thread(
            self._prepare_commit_payloads,
            sessions,
            active,
            turns,
            previous_partitions,
            changed_partitions | previous_pending,
        )
        pending_names = set(pending)
        intent = {**metadata, "pending_partitions": pending}

        def publish_intent() -> None:
            self._sessions = sessions
            self._turns = defaultdict(list, turns)
            self._active = active
            self._partitions = partitions
            self._pending_partitions = set(pending_names)
            if replacing:
                self._expired_pending_prune.clear()

        await self._async_commit_metadata(intent, publish_intent, record_execution=True)

        for partition, payload in pending.items():
            await self._storage.async_save_partition(partition, payload)

        # Empty months are already durably overwritten above. Physical deletion is
        # housekeeping only; metadata remains the authoritative partition index.
        for partition in sorted(pending_names - partitions):
            try:
                await self._async_remove_partition_locked(partition)
            except Exception:
                _LOGGER.warning(
                    "Unable to remove obsolete conversation archive partition %s",
                    partition,
                    exc_info=True,
                )

        def publish_complete() -> None:
            self._pending_partitions.clear()
            if replacing:
                self._repair_required = False

        await self._async_commit_metadata(metadata, publish_complete)

    def _invalidate_after_unreadable_metadata(self) -> None:
        """Prevent stale state from overwriting an unvalidated durable journal."""
        self._sessions.clear()
        self._turns.clear()
        self._active.clear()
        self._partitions.clear()
        self._pending_partitions.clear()
        self._expired_pending_prune.clear()
        self._durable_metadata = None
        self._initialized = False

    def _require_writable(self) -> None:
        self._ensure_initialized()
        if self._repair_required:
            raise StorageRepairRequired("Conversation Archive")

    def _ensure_initialized(self) -> None:
        if isinstance(self._storage, HomeAssistantArchiveStorage):
            self._storage._metadata.require_available()
        if not self._initialized:
            raise RuntimeError("conversation archive has not been initialized")


async def async_get_archive(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> ConversationArchive:
    """Return one shared archive manager per conversation agent."""
    async with get_agent_maintenance_gate(hass, entry_id, subentry_id).shared(
        maintenance=True
    ):
        managers: dict[tuple[str, str], ConversationArchive] = hass.data.setdefault(
            _ARCHIVE_MANAGERS, {}
        )
        key = (entry_id, subentry_id)
        if key not in managers:
            managers[key] = ConversationArchive(
                HomeAssistantArchiveStorage(hass, entry_id, subentry_id), subentry_id
            )
        await managers[key].async_initialize()
        return managers[key]


def archive_tools() -> list[dict[str, Any]]:
    """Return bounded model-facing archive and deterministic privacy tools."""
    return [
        _tool(
            "conversation_search",
            "Search prior retained conversations.",
            {
                "query": {"type": "string", "minLength": 1},
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_SEARCH_OFFSET,
                },
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            ["query"],
            "search",
        ),
        _tool(
            "conversation_get",
            "Read a page from one retained conversation.",
            {
                "session_id": {"type": "string"},
                "start_turn": {"type": "integer", "minimum": 0},
                "text_offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            ["session_id"],
            "get",
        ),
        _tool(
            "conversation_private",
            "Stop retaining this conversation and delete its retained turns.",
            {},
            [],
            "private",
        ),
        _tool(
            "conversation_resume_saving",
            "Start a new retained session after private mode.",
            {},
            [],
            "resume",
        ),
        _tool(
            "conversation_delete_current",
            "Delete the current retained conversation.",
            {},
            [],
            "delete_current",
        ),
        _tool(
            "conversation_delete_selected",
            "Delete confirmed selected retained sessions.",
            {
                "session_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 50,
                },
                "confirm": {"type": "boolean"},
            },
            ["session_ids", "confirm"],
            "delete_selected",
        ),
        _tool(
            "conversation_delete_date_range",
            "Preview or delete sessions by the date of their last retained turn. "
            "First call with confirm=false and show the affected sessions and counts. "
            "If any turns fall outside the requested dates, direct the user to "
            "Extended OpenAI → Data & Memory → Conversation history to review and "
            "confirm whole-conversation deletion. Otherwise ask for confirmation "
            "before calling with confirm=true.",
            {
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "confirm": {"type": "boolean"},
            },
            ["start_date", "end_date", "confirm"],
            "delete_range",
        ),
    ]


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
    operation: str,
) -> dict[str, Any]:
    return {
        "spec": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
        "function": {"type": "archive", "operation": operation},
    }


def _sessions_in_delete_range(
    sessions: Iterable[ArchiveSession], scope_id: str, start: datetime, end: datetime
) -> list[ArchiveSession]:
    return [
        session
        for session in sessions
        if session.scope_id == scope_id
        and session.has_saved_history
        and start <= _parse_time(session.last_message_at) < end
    ]


def _preview_delete_range(
    sessions: dict[str, ArchiveSession],
    turns: dict[str, tuple[ArchiveTurn, ...]],
    scope_id: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    return _describe_date_range_deletion(
        _sessions_in_delete_range(sessions.values(), scope_id, start, end),
        turns,
        start,
        end,
    )


def _describe_date_range_deletion(
    sessions: list[ArchiveSession],
    turns: Mapping[str, Sequence[ArchiveTurn]],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    targets = sorted(
        sessions,
        key=lambda session: (session.last_message_at, session.session_id),
        reverse=True,
    )
    earlier_turns = later_turns = 0
    for session in targets:
        for turn in turns.get(session.session_id, ()):
            timestamp = _parse_time(turn.timestamp)
            earlier_turns += timestamp < start
            later_turns += timestamp >= end
    result = {
        "sessions": [
            {
                "session_id": session.session_id,
                "title": session.title,
                "last_message_at": session.last_message_at,
                "turn_count": len(turns.get(session.session_id, ())),
            }
            for session in targets[:MAX_SEARCH_LIMIT]
        ],
        "session_count": len(targets),
        "turn_count": sum(
            len(turns.get(session.session_id, ())) for session in targets
        ),
        "earlier_turn_count": earlier_turns,
        "later_turn_count": later_turns,
        "has_more_sessions": len(targets) > MAX_SEARCH_LIMIT,
    }
    if earlier_turns or later_turns:
        result["status"] = "user_confirmation_required"
        result["confirmation"] = (
            "Review and confirm each whole-conversation deletion under "
            "Extended OpenAI → Data & Memory → Conversation history."
        )
    return result


def _local_date_bounds(
    start_date: str | None, end_date: str | None, time_zone: tzinfo
) -> tuple[datetime, datetime]:
    """Interpret calendar dates in HA's zone, including variable-length DST days."""
    try:
        for value in (start_date, end_date):
            if value is not None and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
                raise ValueError
        first = date.fromisoformat(start_date) if start_date else None
        last = date.fromisoformat(end_date) if end_date else None
        if first and last and first > last:
            raise ValueError
        start = (
            datetime.combine(first, time.min, time_zone).astimezone(dt_util.UTC)
            if first
            else datetime.min.replace(tzinfo=dt_util.UTC)
        )
        end = (
            datetime.combine(last + timedelta(days=1), time.min, time_zone).astimezone(
                dt_util.UTC
            )
            if last
            else datetime.max.replace(tzinfo=dt_util.UTC)
        )
    except (TypeError, ValueError, OverflowError) as err:
        raise ValueError("a valid start_date and end_date are required") from err
    return start, end


def _search_archive_query(
    sessions: dict[str, ArchiveSession],
    turns: dict[str, tuple[ArchiveTurn, ...]],
    _scope_id: str,
    *args: Any,
) -> dict[str, Any]:
    """Adapt the shared snapshot to the existing model search ranker."""
    snapshot = tuple((session, turns[key]) for key, session in sessions.items())
    return _search_archive_snapshot(snapshot, *args)


def _search_archive_snapshot(
    snapshot: tuple[tuple[ArchiveSession, tuple[ArchiveTurn, ...]], ...],
    query: str,
    start_date: str | None,
    end_date: str | None,
    limit: int,
    offset: int,
    time_zone: tzinfo | None = None,
) -> dict[str, Any]:
    """Rank one immutable Archive snapshot outside the event loop."""
    query_tokens = _tokens(query)
    normalized_query = _normalize(query)
    time_zone = time_zone or dt_util.DEFAULT_TIME_ZONE
    start, end = _local_date_bounds(start_date, end_date, time_zone)
    keep = offset + limit
    ranked: list[tuple[float, datetime, int, ArchiveSession, ArchiveTurn, int]] = []
    total = 0
    for session, turns in snapshot:
        for turn_index, turn in enumerate(turns):
            timestamp = _parse_time(turn.timestamp)
            if (start_date or end_date) and not (start <= timestamp < end):
                continue
            normalized_combined, tokens = _search_features(turn)
            overlap = len(query_tokens & tokens)
            literal_match = bool(
                normalized_query
                and f" {normalized_query} " in f" {normalized_combined} "
            )
            if query_tokens:
                if not overlap and not literal_match:
                    continue
            elif not literal_match:
                # Queries reduced entirely to punctuation have no searchable
                # content. Stop-word-only queries remain valid whole-word/phrase
                # searches, but must not turn into a match-all archive query.
                continue
            score = overlap / max(1, len(query_tokens))
            if literal_match:
                score += 2
            # The sequence key preserves the previous stable ordering of ties.
            candidate = (score, timestamp, -total, session, turn, turn_index)
            total += 1
            if len(ranked) < keep:
                heappush(ranked, candidate)
            elif candidate[:3] > ranked[0][:3]:
                heapreplace(ranked, candidate)
    ranked.sort(key=lambda item: item[:3], reverse=True)
    page = ranked[offset : offset + limit]
    return {
        "results": [
            {
                "session_id": session.session_id,
                "turn_id": turn.turn_id,
                "turn_index": turn_index,
                "date": timestamp.astimezone(time_zone).date().isoformat(),
                "timestamp": turn.timestamp,
                "title": session.title,
                "excerpt": _excerpt(f"{turn.user_text}\n{turn.assistant_text}", query),
            }
            for _, timestamp, _, session, turn, turn_index in page
        ],
        "offset": offset,
        "limit": limit,
        "has_more": total > offset + limit,
    }


def _clean_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("archive text must be a string")
    value = value.strip()
    if len(value) > MAX_TEXT_LENGTH:
        value = value[:MAX_TEXT_LENGTH]
    return value


def _validated_session(raw: Any, *, allow_unretained: bool = False) -> ArchiveSession:
    """Validate one record before it can enter an archive generation."""
    if not isinstance(raw, dict):
        raise ValueError("archive session must be an object")
    try:
        session = ArchiveSession(**raw)
    except TypeError as err:
        raise ValueError("archive session is invalid") from err
    string_values = (
        session.session_id,
        session.agent_subentry_id,
        session.scope_id,
        session.scope_type,
        session.scope_source,
        session.started_at,
        session.last_message_at,
        session.title,
        session.retention_state,
    )
    optional_values = (
        session.home_assistant_conversation_id,
        session.source_device_id,
        session.last_activity_at,
    )
    if not all(isinstance(value, str) for value in string_values) or not all(
        value is None or isinstance(value, str) for value in optional_values
    ):
        raise ValueError("archive session fields have invalid types")
    if (
        not session.session_id
        or len(session.session_id) > 128
        or session.retention_state
        not in (
            {"retained", "private", "unretained"}
            if allow_unretained
            else {"retained", "private"}
        )
        or not isinstance(session.turn_count, int)
        or isinstance(session.turn_count, bool)
        or session.turn_count < 0
        or len(session.title) > MAX_TITLE_LENGTH
        or _parse_time(session.started_at) == datetime.min.replace(tzinfo=dt_util.UTC)
        or _parse_time(session.last_message_at)
        == datetime.min.replace(tzinfo=dt_util.UTC)
        or (
            session.last_activity_at is not None
            and _parse_time(session.last_activity_at)
            == datetime.min.replace(tzinfo=dt_util.UTC)
        )
    ):
        raise ValueError("archive session metadata is invalid")
    # Wall clocks can move backwards; valid records must remain recoverable.
    earliest = min(
        (
            session.started_at,
            session.last_message_at,
            session.last_activity_at or session.started_at,
        ),
        key=_parse_time,
    )
    session = replace(session, started_at=earliest)
    return session


def _validated_turn(raw: Any) -> ArchiveTurn:
    """Validate fields independently of session ownership and partition layout."""
    if not isinstance(raw, dict):
        raise ValueError("archive turn must be an object")
    try:
        turn = ArchiveTurn(**raw)
    except TypeError as err:
        raise ValueError("archive turn is invalid") from err
    if (
        not all(
            isinstance(value, str)
            for value in (
                turn.turn_id,
                turn.session_id,
                turn.timestamp,
                turn.user_text,
                turn.assistant_text,
            )
        )
        or (turn.run_id is not None and not isinstance(turn.run_id, str))
        or not isinstance(turn.successful, bool)
        or (
            turn.sequence is not None
            and (
                not isinstance(turn.sequence, int)
                or isinstance(turn.sequence, bool)
                or turn.sequence < 0
            )
        )
        or not turn.turn_id
        or len(turn.turn_id) > 128
        or len(turn.user_text) > MAX_TEXT_LENGTH
        or len(turn.assistant_text) > MAX_TEXT_LENGTH
        or _parse_time(turn.timestamp) == datetime.min.replace(tzinfo=dt_util.UTC)
    ):
        raise ValueError("archive turn metadata is invalid")
    return turn


def _title(value: str) -> str:
    return _SPACE_PATTERN.sub(" ", value).strip()[:MAX_TITLE_LENGTH]


def _normalize(value: str) -> str:
    return " ".join(
        word_tokens(
            unicodedata.normalize("NFC", value).casefold().replace("\u2019", "'"),
            _TOKEN_PATTERN,
        )
    )


def _stem(token: str) -> str:
    """Share Memory's conservative stemmer without dropping legacy short -ing hits.

    Archive previously mapped five-character forms such as "going" to "go".
    Retain those historical query matches; all longer regular inflections now
    share Memory's corrected handling. Both ranking and excerpt location call
    this same function.
    """
    if len(token) == 5 and token.endswith("ing"):
        return token[:-3]
    return _shared_stem(token)


def _tokens(value: str) -> set[str]:
    return _tokens_from_normalized(_normalize(value))


def _tokens_from_normalized(value: str) -> set[str]:
    """Expand and stem each distinct word once; preserve CJK run boundaries."""
    from .retrieval_text import cjk_terms, lexical_terms_from_tokens

    words = set(value.split())
    if "'" in value or "-" in value:
        words.update(lexical_terms_from_tokens(words))
    return {_stem(token) for token in words if token not in _STOP_WORDS} | (
        set() if value.isascii() else cjk_terms(value)
    )


def _search_features(turn: ArchiveTurn) -> tuple[str, frozenset[str]]:
    """Reuse immutable lexical features within a bounded, weakly owned cache."""
    global _SEARCH_FEATURE_BYTES
    key = id(turn)
    with _SEARCH_FEATURE_LOCK:
        cached = _SEARCH_FEATURES.get(key)
        if cached is not None and cached[0]() is turn:
            return cached[1], cached[2]
        available = (
            _SEARCH_CACHE_BYTES - _SEARCH_FEATURE_BYTES
            if len(_SEARCH_FEATURES) < _SEARCH_CACHE_TURNS
            else 0
        )
    combined = f"{turn.user_text} {turn.assistant_text}"
    normalized = _normalize(combined)
    tokens = frozenset(_tokens_from_normalized(normalized))
    if available <= 0:
        return normalized, tokens
    size = sys.getsizeof(normalized) + sys.getsizeof(tokens) + 640
    if size > available:
        return normalized, tokens
    size += sum(map(sys.getsizeof, tokens))
    if size > available:
        return normalized, tokens

    def discard(reference: Any) -> None:
        global _SEARCH_FEATURE_BYTES
        with _SEARCH_FEATURE_LOCK:
            current = _SEARCH_FEATURES.get(key)
            if current is not None and current[0] is reference:
                _SEARCH_FEATURE_BYTES -= _SEARCH_FEATURES.pop(key)[3]

    with _SEARCH_FEATURE_LOCK:
        previous = _SEARCH_FEATURES.pop(key, None)
        if previous is not None:
            _SEARCH_FEATURE_BYTES -= previous[3]
        # Admit only within the budget. Evicting for every scanned turn causes
        # sequential searches larger than the cache to miss on every revisit.
        if (
            _SEARCH_FEATURE_BYTES + size > _SEARCH_CACHE_BYTES
            or len(_SEARCH_FEATURES) >= _SEARCH_CACHE_TURNS
        ):
            return normalized, tokens
        _SEARCH_FEATURES[key] = (weakref.ref(turn, discard), normalized, tokens, size)
        _SEARCH_FEATURE_BYTES += size
    return normalized, tokens


def _excerpt_match_offset(value: str, query: str) -> int:
    """Use ranking's lexical matches, retaining offsets into the display text."""
    from .retrieval_text import cjk_terms

    folded_parts: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(value):
        folded = character.casefold().replace("\u2019", "'")
        folded_parts.append(folded)
        offsets.extend([index] * len(folded))
    folded_text = "".join(folded_parts)
    words = [
        (match.start(), folded_text[match.start() : match.end()])
        for match in _TOKEN_PATTERN.finditer(word_character_projection(folded_text))
    ]
    normalized = " ".join(token for _, token in words)
    normalized_query = _normalize(query)
    # Prefer the same whole phrase bonus as the ranker, including punctuation
    # normalized to spaces. A substring inside a different word is not a match.
    phrase = f" {normalized} ".find(f" {normalized_query} ") if normalized_query else -1
    if phrase >= 0:
        position = 0
        for start, token in words:
            if position == phrase:
                return offsets[start]
            position += len(token) + 1

    terms = _tokens_from_normalized(normalized_query)
    for start, token in words:
        if not (_tokens_from_normalized(token) & terms):
            continue
        # Compounds and possessives are indexed both whole and by component.
        # Anchor a matching component, even in a compound longer than a snippet.
        stem = token[:-2] if token.endswith("'s") else token
        positions = [
            part.start()
            for part in re.finditer(r"[^-]+", stem)
            if part.group() not in _STOP_WORDS and _stem(part.group()) in terms
        ]
        if not token.isascii():
            # Continuous CJK text has no word boundary; prefer the complete
            # query when present, otherwise the character/bigram rank matches.
            cjk_matches = cjk_terms(token) & terms
            if cjk_matches:
                exact = token.find(normalized_query) if normalized_query else -1
                positions.append(
                    exact
                    if exact >= 0
                    else min(token.find(term) for term in cjk_matches)
                )
        return offsets[start + min(positions, default=0)]
    return 0


def _excerpt_with_match(value: str, query: str) -> tuple[str, int]:
    """Build a display excerpt and locate its match in the original text."""
    normalized = unicodedata.normalize("NFC", value)
    index = _excerpt_match_offset(normalized, query)
    display = _SPACE_PATTERN.sub(" ", normalized).strip()
    display_index = len(_SPACE_PATTERN.sub(" ", normalized[:index]).lstrip())
    start = max(0, display_index - MAX_EXCERPT_LENGTH // 3)
    excerpt = (
        ("…" if start else "")
        + display[start : start + MAX_EXCERPT_LENGTH]
        + ("…" if start + MAX_EXCERPT_LENGTH < len(display) else "")
    )
    if index and normalized != value:
        # NFC can compose several original code points into one. Match starts
        # are token or CJK-character boundaries. Count the equivalent
        # decomposed code points to recover the original boundary.
        remaining = len(unicodedata.normalize("NFD", normalized[:index]))
        for original_index, character in enumerate(value):
            width = (
                1
                if character.isascii()
                else len(unicodedata.normalize("NFD", character))
            )
            if remaining < width:
                return excerpt, original_index
            remaining -= width
    return excerpt, index


def _excerpt(value: str, query: str) -> str:
    return _excerpt_with_match(value, query)[0]


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=dt_util.UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    return parsed


def _session_payload(session: ArchiveSession) -> dict[str, Any]:
    """Private boundaries retain identity, never conversation-derived titles."""
    payload = asdict(session)
    if session.retention_state == "private":
        payload["title"] = ""
    return payload


def _turn_payload(turn: ArchiveTurn) -> dict[str, Any]:
    """Keep legacy snapshots unchanged until an owned write establishes order."""
    payload = asdict(turn)
    if turn.sequence is None:
        payload.pop("sequence")
    return payload


def _ordered_session_turns(turns: list[ArchiveTurn]) -> list[ArchiveTurn]:
    """Use explicit conversation order, retaining unknown legacy loader order."""
    sequenced = [turn.sequence for turn in turns if turn.sequence is not None]
    if not sequenced:
        return turns
    if len(sequenced) != len(turns) or len(set(sequenced)) != len(sequenced):
        raise ValueError("archive turn sequence is ambiguous")
    return sorted(
        turns, key=lambda turn: turn.sequence if turn.sequence is not None else -1
    )


def _replacement_turn_map(turns: list[ArchiveTurn]) -> dict[str, list[ArchiveTurn]]:
    """A replacement snapshot's per-session list order establishes legacy order."""
    grouped: dict[str, list[ArchiveTurn]] = defaultdict(list)
    for turn in turns:
        grouped[turn.session_id].append(turn)
    return {
        session_id: _ordered_session_turns(items)
        if any(turn.sequence is not None for turn in items)
        else [replace(turn, sequence=index) for index, turn in enumerate(items)]
        for session_id, items in grouped.items()
    }


def _archive_payload(
    sessions: list[ArchiveSession], turns: list[ArchiveTurn]
) -> dict[str, Any]:
    """Serialize immutable records without touching live manager collections."""
    return {
        "sessions": [_session_payload(item) for item in sessions],
        "turns": [_turn_payload(item) for item in turns],
    }
