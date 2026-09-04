"""Keep durable Archive and Usage state consistent across persistence failures."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import CONF_ARCHIVE_RETENTION_DAYS, DEFAULT_ARCHIVE_RETENTION_DAYS

_LOGGER = logging.getLogger(__name__)
_INSTALLED = False
_ARCHIVE_RETENTION_INTERVAL = timedelta(days=1)
_USAGE_PRUNE_TASK = "_extended_openai_usage_prune_task"
_USAGE_PRUNE_ATTEMPT_DATE = "_extended_openai_usage_prune_attempt_date"


def _archive_metadata_payload(
    sessions: dict[str, Any],
    active: dict[str, str],
    partitions: set[str],
    pending_partitions: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Serialize one candidate Archive metadata state without publishing it."""
    persisted_sessions = {
        session.session_id: session
        for session in sessions.values()
        if session.retention_state != "unretained"
    }
    payload: dict[str, Any] = {
        "sessions": [asdict(session) for session in persisted_sessions.values()],
        "active": {
            key: value for key, value in active.items() if value in persisted_sessions
        },
        "partitions": sorted(partitions),
    }
    if pending_partitions is not None:
        payload["pending_partitions"] = pending_partitions
    return payload


def _archive_partition_payload(
    partition: str, turns: dict[str, list[Any]]
) -> dict[str, Any]:
    """Serialize one monthly partition from a candidate Archive turn state."""
    return {
        "turns": [
            asdict(turn)
            for session_turns in turns.values()
            for turn in session_turns
            if turn.timestamp.startswith(partition)
        ]
    }


async def _async_commit_archive_state(
    archive: Any,
    *,
    sessions: dict[str, Any],
    turns: dict[str, list[Any]],
    active: dict[str, str],
    partitions: set[str],
    changed_partitions: set[str],
) -> None:
    """Persist Archive intent before publishing the candidate state in memory.

    The metadata journal is the durable commit point. If that first write fails,
    callers continue to see the previous in-memory state. Once it succeeds, restart
    recovery is guaranteed to finish the candidate partition writes, so the live
    state is published before those writes and deliberately remains at the target if
    a later partition/final-metadata write fails.
    """
    pending_names = set(changed_partitions) | set(archive._pending_partitions)
    pending = {
        partition: _archive_partition_payload(partition, turns)
        for partition in sorted(pending_names)
    }

    await archive._storage.async_save_metadata(
        _archive_metadata_payload(sessions, active, partitions, pending)
    )

    # The write-ahead intent is durable now. Publish exactly the state restart
    # recovery will complete rather than leaving RAM ahead of durable intent.
    archive._sessions = sessions
    archive._turns = defaultdict(list, turns)
    archive._active = active
    archive._partitions = partitions
    archive._pending_partitions = set(pending_names)

    for partition, payload in pending.items():
        await archive._storage.async_save_partition(partition, payload)
    await archive._storage.async_save_metadata(
        _archive_metadata_payload(sessions, active, partitions)
    )
    archive._pending_partitions.clear()


def _changed_partitions(turns: list[Any]) -> set[str]:
    return {turn.timestamp[:7] for turn in turns}


def _install_archive_transactions() -> None:
    """Replace mutate-then-save Archive operations with journal-first variants."""
    from . import conversation_archive as archive_module

    archive_type: Any = archive_module.ConversationArchive
    if getattr(archive_type.async_record_turn, "_extended_openai_journal_first", False):
        return

    async def async_record_turn(
        archive: Any,
        session_id: str,
        *,
        run_id: str | None,
        user_text: str,
        assistant_text: str,
        successful: bool,
    ) -> Any:
        user_text = archive_module._clean_text(user_text)
        assistant_text = archive_module._clean_text(assistant_text)
        async with archive._lock:
            session = archive._sessions.get(session_id)
            if session is None or session.retention_state != "retained":
                return None
            timestamp = archive_module.dt_util.utcnow().isoformat()
            turn = archive_module.ArchiveTurn(
                turn_id=archive_module.uuid4().hex,
                session_id=session_id,
                run_id=run_id,
                timestamp=timestamp,
                user_text=user_text,
                assistant_text=assistant_text,
                successful=successful,
            )
            sessions = dict(archive._sessions)
            sessions[session_id] = archive_module.ArchiveSession(
                **{
                    **asdict(session),
                    "last_message_at": timestamp,
                    "title": session.title or archive_module._title(user_text),
                    "turn_count": session.turn_count + 1,
                }
            )
            turns = dict(archive._turns)
            turns[session_id] = [*archive._turns.get(session_id, ()), turn]
            partitions = set(archive._partitions)
            partition = timestamp[:7]
            partitions.add(partition)
            await _async_commit_archive_state(
                archive,
                sessions=sessions,
                turns=turns,
                active=dict(archive._active),
                partitions=partitions,
                changed_partitions={partition},
            )
            return turn

    async def async_make_private(archive: Any, session_id: str) -> dict[str, Any]:
        async with archive._lock:
            session = archive._require_session(session_id)
            turns = dict(archive._turns)
            removed = list(turns.pop(session_id, ()))
            sessions = dict(archive._sessions)
            sessions[session_id] = archive_module.ArchiveSession(
                **{**asdict(session), "turn_count": 0, "retention_state": "private"}
            )
            await _async_commit_archive_state(
                archive,
                sessions=sessions,
                turns=turns,
                active=dict(archive._active),
                partitions=set(archive._partitions),
                changed_partitions=_changed_partitions(removed),
            )
            return {
                "private_mode_enabled": True,
                "session_id": session_id,
                "deleted_turns": len(removed),
                "future_turns_retained": False,
            }

    async def async_delete_session(
        archive: Any, scope_id: str, session_id: str
    ) -> dict[str, int]:
        async with archive._lock:
            archive._require_owned_session(scope_id, session_id)
            sessions = dict(archive._sessions)
            del sessions[session_id]
            turns = dict(archive._turns)
            removed = list(turns.pop(session_id, ()))
            active = {
                key: value
                for key, value in archive._active.items()
                if value != session_id
            }
            await _async_commit_archive_state(
                archive,
                sessions=sessions,
                turns=turns,
                active=active,
                partitions=set(archive._partitions),
                changed_partitions=_changed_partitions(removed),
            )
            return {"deleted_sessions": 1, "deleted_turns": len(removed)}

    async def async_clear_scope(
        archive: Any, scope_id: str, *, confirm: bool
    ) -> dict[str, int]:
        if not confirm:
            raise ValueError("Explicit confirmation is required")
        async with archive._lock:
            targets = {
                session.session_id
                for session in archive._sessions.values()
                if session.scope_id == scope_id
            }
            if not targets:
                return {"deleted_sessions": 0, "deleted_turns": 0}
            sessions = {
                session_id: session
                for session_id, session in archive._sessions.items()
                if session_id not in targets
            }
            turns = dict(archive._turns)
            removed = [
                turn for session_id in targets for turn in turns.pop(session_id, ())
            ]
            active = {
                key: value
                for key, value in archive._active.items()
                if value not in targets
            }
            await _async_commit_archive_state(
                archive,
                sessions=sessions,
                turns=turns,
                active=active,
                partitions=set(archive._partitions),
                changed_partitions=_changed_partitions(removed),
            )
            return {
                "deleted_sessions": len(targets),
                "deleted_turns": len(removed),
            }

    async def async_delete_selected(
        archive: Any,
        scope_id: str,
        session_ids: list[str],
        *,
        confirm: bool,
    ) -> dict[str, int]:
        if not confirm:
            raise ValueError("Explicit confirmation is required")
        if not session_ids or len(session_ids) > archive_module.MAX_SEARCH_LIMIT:
            raise ValueError(
                f"session_ids must contain 1 to {archive_module.MAX_SEARCH_LIMIT} IDs"
            )
        async with archive._lock:
            targets = set(session_ids)
            for session_id in targets:
                archive._require_owned_session(scope_id, session_id)
            sessions = {
                session_id: session
                for session_id, session in archive._sessions.items()
                if session_id not in targets
            }
            turns = dict(archive._turns)
            removed = [
                turn for session_id in targets for turn in turns.pop(session_id, ())
            ]
            active = {
                key: value
                for key, value in archive._active.items()
                if value not in targets
            }
            await _async_commit_archive_state(
                archive,
                sessions=sessions,
                turns=turns,
                active=active,
                partitions=set(archive._partitions),
                changed_partitions=_changed_partitions(removed),
            )
            return {
                "deleted_sessions": len(targets),
                "deleted_turns": len(removed),
            }

    async def async_prune(archive: Any, retention_days: int) -> dict[str, int]:
        cutoff = archive_module.dt_util.utcnow() - timedelta(
            days=max(1, retention_days)
        )
        async with archive._lock:
            targets = {
                session.session_id
                for session in archive._sessions.values()
                if archive_module._parse_time(session.last_message_at) < cutoff
            }
            if not targets:
                return {"deleted_sessions": 0, "deleted_turns": 0}
            sessions = {
                session_id: session
                for session_id, session in archive._sessions.items()
                if session_id not in targets
            }
            turns = dict(archive._turns)
            removed = [
                turn for session_id in targets for turn in turns.pop(session_id, ())
            ]
            active = {
                key: value
                for key, value in archive._active.items()
                if value not in targets
            }
            await _async_commit_archive_state(
                archive,
                sessions=sessions,
                turns=turns,
                active=active,
                partitions=set(archive._partitions),
                changed_partitions=_changed_partitions(removed),
            )
            return {
                "deleted_sessions": len(targets),
                "deleted_turns": len(removed),
            }

    async def async_replace_backup(
        archive: Any, sessions_list: list[Any], turns_list: list[Any]
    ) -> None:
        async with archive._lock:
            archive._ensure_initialized()
            sessions = {session.session_id: session for session in sessions_list}
            turns: dict[str, list[Any]] = defaultdict(list)
            for turn in turns_list:
                turns[turn.session_id].append(turn)
            partitions = {turn.timestamp[:7] for turn in turns_list}
            await _async_commit_archive_state(
                archive,
                sessions=sessions,
                turns=dict(turns),
                active={},
                partitions=partitions,
                changed_partitions=set(archive._partitions) | partitions,
            )

    async_record_turn._extended_openai_journal_first = True  # type: ignore[attr-defined]
    archive_type.async_record_turn = async_record_turn
    archive_type.async_make_private = async_make_private
    archive_type.async_delete_session = async_delete_session
    archive_type.async_clear_scope = async_clear_scope
    archive_type.async_delete_selected = async_delete_selected
    archive_type.async_prune = async_prune
    archive_type.async_replace_backup = async_replace_backup


def _usage_pruned_state(manager: Any) -> tuple[list[Any], list[Any], dict[str, int]]:
    from .usage import _parse_time

    now = dt_util.utcnow()
    request_cutoff = now - timedelta(days=max(0, manager.request_retention_days))
    run_cutoff = now - timedelta(days=max(0, manager.run_retention_days))
    requests = [
        request
        for request in manager.requests
        if manager.request_retention_days > 0
        and _parse_time(request.timestamp) >= request_cutoff
    ]
    runs = [
        run
        for run in manager.runs
        if manager.run_retention_days > 0 and _parse_time(run.started_at) >= run_cutoff
    ]
    return (
        requests,
        runs,
        {
            "deleted_requests": len(manager.requests) - len(requests),
            "deleted_runs": len(manager.runs) - len(runs),
        },
    )


async def _async_persist_usage_details(
    manager: Any, requests: list[Any], runs: list[Any]
) -> None:
    if manager._detail_storage is None:
        return
    await manager._detail_storage.async_save(
        {
            "requests": [asdict(request) for request in requests],
            "runs": [asdict(run) for run in runs],
        }
    )


def _install_usage_transactions() -> None:
    """Persist candidate Usage detail state before changing the live lists."""
    from . import lifecycle_optimizations as lifecycle
    from .usage import UsageManager

    manager_type: Any = UsageManager
    if getattr(
        manager_type.async_prune_details, "_extended_openai_persist_first", False
    ):
        return

    async def async_prune_details(manager: Any, *, save: bool = True) -> dict[str, int]:
        async with manager._lock:
            requests, runs, result = _usage_pruned_state(manager)
            if save:
                await _async_persist_usage_details(manager, requests, runs)
            manager.requests = requests
            manager.runs = runs
            setattr(
                manager,
                lifecycle._LAST_USAGE_PRUNE_DATE,
                dt_util.utcnow().date().isoformat(),
            )
            return result

    async def async_clear_details(manager: Any, *, confirm: bool) -> dict[str, int]:
        if not confirm:
            raise ValueError("Explicit confirmation is required")
        async with manager._lock:
            result = {
                "deleted_requests": len(manager.requests),
                "deleted_runs": len(manager.runs),
            }
            await _async_persist_usage_details(manager, [], [])
            manager.requests = []
            manager.runs = []
            return result

    async def async_prune_usage_if_due(manager: Any) -> None:
        """Run once-daily transactional Usage retention outside the user turn."""
        today = dt_util.utcnow().date().isoformat()
        if (
            getattr(manager, lifecycle._LAST_USAGE_PRUNE_DATE, None) == today
            or getattr(manager, _USAGE_PRUNE_ATTEMPT_DATE, None) == today
        ):
            return
        current = getattr(manager, _USAGE_PRUNE_TASK, None)
        if isinstance(current, asyncio.Task) and not current.done():
            return
        setattr(manager, _USAGE_PRUNE_ATTEMPT_DATE, today)

        async def run() -> None:
            try:
                await manager.async_prune_details(save=True)
            except Exception:
                _LOGGER.exception("Background usage retention maintenance failed")

        task = asyncio.create_task(
            run(), name="extended_openai_usage_retention_maintenance"
        )
        setattr(manager, _USAGE_PRUNE_TASK, task)

        def done(completed: asyncio.Task[Any]) -> None:
            if getattr(manager, _USAGE_PRUNE_TASK, None) is completed:
                setattr(manager, _USAGE_PRUNE_TASK, None)

        task.add_done_callback(done)

    async_prune_details._extended_openai_persist_first = True  # type: ignore[attr-defined]
    manager_type.async_prune_details = async_prune_details
    manager_type.async_clear_details = async_clear_details
    # Lifecycle finalization resolves this module global at runtime. Installing this
    # after the hot-path wrapper keeps pruning off-path while replacing its unsafe
    # mutate-then-schedule implementation.
    lifecycle._async_prune_usage_if_due = async_prune_usage_if_due


async def async_prune_archive_retention(
    agent: Any, _now: datetime | None = None
) -> None:
    """Enforce the current Archive retention setting during long HA uptimes."""
    archive = getattr(agent, "_archive", None)
    if archive is None:
        return
    try:
        await archive.async_prune(
            int(
                agent.subentry.data.get(
                    CONF_ARCHIVE_RETENTION_DAYS, DEFAULT_ARCHIVE_RETENTION_DAYS
                )
            )
        )
    except Exception:
        _LOGGER.exception(
            "Background conversation archive retention maintenance failed"
        )


def _install_archive_retention_schedule() -> None:
    """Schedule low-frequency Archive retention independently of conversation traffic."""
    from .conversation import ExtendedOpenAIAgentEntity

    agent_type: Any = ExtendedOpenAIAgentEntity
    current = agent_type.async_added_to_hass
    if getattr(current, "_extended_openai_archive_retention", False):
        return
    original = current

    async def async_added_to_hass(agent: Any) -> None:
        await original(agent)

        async def prune(now: datetime) -> None:
            await async_prune_archive_retention(agent, now)

        agent.async_on_remove(
            async_track_time_interval(
                agent.hass,
                prune,
                _ARCHIVE_RETENTION_INTERVAL,
            )
        )

    async_added_to_hass._extended_openai_archive_retention = True  # type: ignore[attr-defined]
    agent_type.async_added_to_hass = async_added_to_hass


def install_durable_state_hardening() -> None:
    """Install journal-first durable-state mutations after lifecycle optimizers."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_archive_transactions()
    _install_usage_transactions()
    _install_archive_retention_schedule()
    _INSTALLED = True
