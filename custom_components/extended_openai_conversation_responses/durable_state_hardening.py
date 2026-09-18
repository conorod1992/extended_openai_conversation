"""Keep durable Archive state consistent across persistence failures."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import timedelta
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)
_INSTALLED = False


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


def _archive_partitions(turns: dict[str, list[Any]]) -> set[str]:
    """Return only monthly partitions that still contain retained turns."""
    return {
        turn.timestamp[:7] for session_turns in turns.values() for turn in session_turns
    }


async def _async_remove_archive_partition(storage: Any, partition: str) -> None:
    """Remove one obsolete partition store when the storage boundary supports it."""
    remover = getattr(storage, "async_remove_partition", None)
    if callable(remover):
        await remover(partition)
        return

    # HomeAssistantArchiveStorage predates an explicit removal method. Keep the
    # compatibility fallback local to this housekeeping boundary instead of making
    # Archive callers know about Store internals.
    store_factory = getattr(storage, "_partition_store", None)
    if not callable(store_factory):
        return
    store = store_factory(partition)
    remove = getattr(store, "async_remove", None)
    if callable(remove):
        await remove()


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
    # Surviving turns are the source of truth for the partition index. Destructive
    # operations historically passed the previous set here, leaving empty months
    # permanently referenced after their last session was removed.
    del partitions
    partitions = _archive_partitions(turns)
    removed_partitions = set(archive._partitions) - partitions
    pending_names = (
        set(changed_partitions) | set(archive._pending_partitions) | removed_partitions
    )
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

    # Removed months have already been durably overwritten with an empty payload.
    # Physically remove their Store files when possible; failure here is only
    # housekeeping and must not turn a completed privacy/deletion mutation into an
    # apparent failure. Metadata below remains the authoritative partition index.
    for partition in sorted(pending_names - partitions):
        try:
            await _async_remove_archive_partition(archive._storage, partition)
        except Exception:
            _LOGGER.warning(
                "Unable to remove obsolete conversation archive partition %s",
                partition,
                exc_info=True,
            )

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


def install_durable_state_hardening() -> None:
    """Install journal-first durable-state mutations after lifecycle optimizers."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_archive_transactions()
    _INSTALLED = True
