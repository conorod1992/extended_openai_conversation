"""Transactional and fail-open archive session persistence."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any
from uuid import uuid4
import weakref

from homeassistant.util import dt as dt_util

from .const import CONF_ARCHIVE_ENABLED, DEFAULT_ARCHIVE_ENABLED
from .conversation_archive import ArchiveSession, ConversationArchive, _parse_time
from .scope import ResolvedDataScope

_LOGGER = logging.getLogger(__name__)
_INSTALLED = False
_STATUS_HANDLER = "_extended_openai_archive_status_handler"


def _prospective_metadata(
    archive: ConversationArchive,
    session_key: str,
    session: ArchiveSession,
) -> dict[str, Any]:
    """Build metadata for a new active session without publishing it in memory."""
    sessions = {**archive._sessions, session.session_id: session}
    active = {**archive._active, session_key: session.session_id}
    persisted_sessions = {
        session_id: item
        for session_id, item in sessions.items()
        if item.retention_state != "unretained"
    }
    payload: dict[str, Any] = {
        "sessions": [
            {
                "session_id": item.session_id,
                "home_assistant_conversation_id": item.home_assistant_conversation_id,
                "agent_subentry_id": item.agent_subentry_id,
                "scope_id": item.scope_id,
                "scope_type": item.scope_type,
                "scope_source": item.scope_source,
                "source_device_id": item.source_device_id,
                "started_at": item.started_at,
                "last_message_at": item.last_message_at,
                "title": item.title,
                "turn_count": item.turn_count,
                "retention_state": item.retention_state,
            }
            for item in persisted_sessions.values()
        ],
        "active": {
            key: value for key, value in active.items() if value in persisted_sessions
        },
        "partitions": sorted(archive._partitions),
    }
    if archive._pending_partitions:
        # A failed turn/partition commit leaves a restart journal. Session metadata
        # writes must preserve that journal rather than silently clearing recovery.
        payload["pending_partitions"] = {
            partition: archive._partition_payload_locked(partition)
            for partition in sorted(archive._pending_partitions)
        }
    return payload


async def _async_begin_session_transactional(
    archive: ConversationArchive,
    session_key: str,
    scope: ResolvedDataScope,
    home_assistant_conversation_id: str | None,
    *,
    archive_enabled: bool,
    shared_archive_enabled: bool,
    inactivity_minutes: int,
) -> ArchiveSession | None:
    """Create a session only after its durable metadata write succeeds."""
    if not archive_enabled:
        return None

    async with archive._lock:
        archive._ensure_initialized()
        current = archive._sessions.get(archive._active.get(session_key, ""))
        now = dt_util.utcnow()
        may_retain = archive_enabled and scope.allows_retention
        if scope.scope_type == "shared" and not shared_archive_enabled:
            may_retain = False
        if current is not None:
            expired = (
                _parse_time(current.last_message_at)
                + timedelta(minutes=max(1, inactivity_minutes))
                < now
            )
            if (
                not expired
                and current.scope_id == scope.scope_id
                and current.home_assistant_conversation_id
                == home_assistant_conversation_id
                and current.retention_state != "closed"
            ):
                return current

        timestamp = now.isoformat()
        session = ArchiveSession(
            session_id=uuid4().hex,
            home_assistant_conversation_id=home_assistant_conversation_id,
            agent_subentry_id=archive._agent_subentry_id,
            scope_id=scope.scope_id,
            scope_type=scope.scope_type,
            scope_source=scope.source,
            source_device_id=scope.device_id,
            started_at=timestamp,
            last_message_at=timestamp,
            title="",
            turn_count=0,
            retention_state="retained" if may_retain else "unretained",
        )
        await archive._storage.async_save_metadata(
            _prospective_metadata(archive, session_key, session)
        )
        archive._sessions[session.session_id] = session
        archive._active[session_key] = session.session_id
        return session


async def _async_resume_saving_transactional(
    archive: ConversationArchive,
    session_key: str,
    previous_session_id: str,
    scope: ResolvedDataScope,
) -> ArchiveSession:
    """Publish a resumed session only after metadata persistence succeeds."""
    async with archive._lock:
        previous = archive._require_session(previous_session_id)
        timestamp = dt_util.utcnow().isoformat()
        session = ArchiveSession(
            session_id=uuid4().hex,
            home_assistant_conversation_id=previous.home_assistant_conversation_id,
            agent_subentry_id=archive._agent_subentry_id,
            scope_id=scope.scope_id,
            scope_type=scope.scope_type,
            scope_source=scope.source,
            source_device_id=scope.device_id,
            started_at=timestamp,
            last_message_at=timestamp,
            title="",
            turn_count=0,
            retention_state="retained" if scope.allows_retention else "unretained",
        )
        await archive._storage.async_save_metadata(
            _prospective_metadata(archive, session_key, session)
        )
        archive._sessions[session.session_id] = session
        archive._active[session_key] = session.session_id
        return session


def _notify_runtime_status(
    archive: ConversationArchive, error: Exception | None
) -> bool:
    """Notify the owning agent when this archive has a live runtime status hook."""
    handler = getattr(archive, _STATUS_HANDLER, None)
    if not callable(handler):
        return False
    handler(error)
    return True


def attach_archive_runtime_status(agent: Any, archive: ConversationArchive) -> None:
    """Attach a weak agent status hook without extending entity lifetime."""
    agent_ref = weakref.ref(agent)

    def update_status(error: Exception | None) -> None:
        current = agent_ref()
        if current is None:
            return
        configured = bool(
            current.subentry.data.get(CONF_ARCHIVE_ENABLED, DEFAULT_ARCHIVE_ENABLED)
        )
        current._set_subsystem_status(
            "archive",
            configured,
            error,
            healthy=error is None,
        )

    setattr(archive, _STATUS_HANDLER, update_status)


def install_archive_session_hardening() -> None:
    """Install transactional session creation and fail-open request behavior once."""
    global _INSTALLED
    if _INSTALLED:
        return

    from .conversation import ExtendedOpenAIAgentEntity

    archive_type: Any = ConversationArchive
    agent_type: Any = ExtendedOpenAIAgentEntity
    original_initialize_archive = agent_type._async_initialize_archive

    async def initialize_archive(agent: Any, configured: bool) -> None:
        await original_initialize_archive(agent, configured)
        archive = getattr(agent, "_archive", None)
        if archive is not None:
            attach_archive_runtime_status(agent, archive)

    async def begin_session(
        archive: ConversationArchive,
        session_key: str,
        scope: ResolvedDataScope,
        home_assistant_conversation_id: str | None,
        *,
        archive_enabled: bool,
        shared_archive_enabled: bool,
        inactivity_minutes: int,
    ) -> ArchiveSession | None:
        try:
            session = await _async_begin_session_transactional(
                archive,
                session_key,
                scope,
                home_assistant_conversation_id,
                archive_enabled=archive_enabled,
                shared_archive_enabled=shared_archive_enabled,
                inactivity_minutes=inactivity_minutes,
            )
        except Exception as err:
            if not _notify_runtime_status(archive, err):
                raise
            _LOGGER.exception(
                "Unable to begin conversation archive session; continuing without "
                "archiving this turn"
            )
            return None
        if session is not None:
            _notify_runtime_status(archive, None)
        return session

    async def resume_saving(
        archive: ConversationArchive,
        session_key: str,
        previous_session_id: str,
        scope: ResolvedDataScope,
    ) -> ArchiveSession:
        try:
            session = await _async_resume_saving_transactional(
                archive, session_key, previous_session_id, scope
            )
        except Exception as err:
            _notify_runtime_status(archive, err)
            raise
        _notify_runtime_status(archive, None)
        return session

    agent_type._async_initialize_archive = initialize_archive
    archive_type.async_begin_session = begin_session
    archive_type.async_resume_saving = resume_saving
    _INSTALLED = True
