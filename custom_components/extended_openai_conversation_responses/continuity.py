"""Provider-neutral continuity across separate Assist invocations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

from homeassistant.components import conversation
from homeassistant.util import dt as dt_util

from .const import (
    CONVERSATION_CONTINUITY_DEVICE,
    CONVERSATION_CONTINUITY_HA_DEFAULT,
    CONVERSATION_CONTINUITY_USER,
)
from .scope import ResolvedDataScope


@dataclass(slots=True)
class ContinuityResolution:
    """Resolved conversation and the history needed if Core recreated its log."""

    conversation_id: str | None
    key: str | None
    history: list[conversation.Content]
    resumed: bool


@dataclass(slots=True)
class ActiveConversation:
    """A bounded in-process logical conversation."""

    key: str
    conversation_id: str
    label: str
    last_active: Any
    history: list[conversation.Content]
    in_flight: bool = False


@dataclass(slots=True)
class ConversationMemoryBundle:
    """Automatically selected memory references for one logical conversation."""

    references: list[tuple[str, str]]
    selected_at: Any
    last_active: Any


class ConversationContinuity:
    """Resolve and retain active model context without relying on a provider."""

    def __init__(self, agent_id: str) -> None:
        self._agent_id = agent_id
        self._sessions: dict[str, ActiveConversation] = {}
        self._memory_bundles: dict[str, ConversationMemoryBundle] = {}
        self._lock = asyncio.Lock()
        self.resume_count = 0
        self.new_session_count = 0

    @staticmethod
    def identity_key(
        mode: str, scope: ResolvedDataScope, device_id: str | None
    ) -> tuple[str | None, str]:
        """Return the safe continuity owner and a human-readable label."""
        if mode == CONVERSATION_CONTINUITY_USER and scope.scope_type == "user":
            return scope.scope_id, "Resolved user"
        if mode in {CONVERSATION_CONTINUITY_DEVICE, CONVERSATION_CONTINUITY_USER}:
            if device_id:
                return f"device:{device_id}", "Assist device"
            return None, "Home Assistant default"
        return None, "Home Assistant default"

    async def async_resolve(
        self,
        mode: str,
        scope: ResolvedDataScope,
        device_id: str | None,
        incoming_conversation_id: str | None,
        timeout_minutes: int,
    ) -> ContinuityResolution:
        """Resolve one request using a small lock and no network I/O."""
        if mode == CONVERSATION_CONTINUITY_HA_DEFAULT:
            return ContinuityResolution(incoming_conversation_id, None, [], False)
        key, label = self.identity_key(mode, scope, device_id)
        if key is None:
            return ContinuityResolution(incoming_conversation_id, None, [], False)
        now = dt_util.utcnow()
        cutoff = now - timedelta(minutes=timeout_minutes)
        async with self._lock:
            self._prune_locked(cutoff)
            active = self._sessions.get(key)
            if active is not None:
                if active.in_flight:
                    # Never let simultaneous satellites mutate the same HA ChatLog.
                    # The overlapping request starts independently and does not take
                    # ownership of the established continuity mapping.
                    return ContinuityResolution(
                        f"extended-openai-{self._agent_id}-{uuid4().hex}",
                        None,
                        [],
                        False,
                    )
                active.in_flight = True
                self.resume_count += 1
                return ContinuityResolution(
                    active.conversation_id, key, active.history.copy(), True
                )
            # A non-ULID caller-selected ID is explicitly supported by HA's
            # chat-session helper and remains stable if Core recreates its log.
            conversation_id = f"extended-openai-{self._agent_id}-{uuid4().hex}"
            self._sessions[key] = ActiveConversation(
                key, conversation_id, label, now, [], True
            )
            self.new_session_count += 1
            return ContinuityResolution(conversation_id, key, [], False)

    async def async_record_success(
        self, key: str | None, content: list[conversation.Content]
    ) -> None:
        """Record a successful turn after the model call has completed."""
        if key is None:
            return
        async with self._lock:
            active = self._sessions.get(key)
            if active is None:
                return
            active.history = content.copy()
            active.last_active = dt_util.utcnow()
            active.in_flight = False

    async def async_release(self, key: str | None) -> None:
        """Release a request claim after failure or cancellation."""
        if key is None:
            return
        async with self._lock:
            active = self._sessions.get(key)
            if active is not None:
                active.in_flight = False

    async def async_end(self, key: str) -> bool:
        """End one active conversation."""
        async with self._lock:
            session = self._sessions.pop(key, None)
            if session is not None:
                self._memory_bundles.pop(f"continuity:{key}", None)
            return session is not None

    async def async_get_memory_bundle(
        self, session_key: str, timeout_minutes: int
    ) -> list[tuple[str, str]] | None:
        """Load and touch a bundle without changing its selected references."""
        now = dt_util.utcnow()
        cutoff = now - timedelta(minutes=timeout_minutes)
        async with self._lock:
            self._prune_memory_bundles_locked(cutoff)
            bundle = self._memory_bundles.get(session_key)
            if bundle is None:
                return None
            bundle.last_active = now
            return bundle.references.copy()

    async def async_set_memory_bundle(
        self, session_key: str, references: list[tuple[str, str]], timeout_minutes: int
    ) -> list[tuple[str, str]]:
        """Store the first selection; concurrent later callers reuse it."""
        now = dt_util.utcnow()
        cutoff = now - timedelta(minutes=timeout_minutes)
        async with self._lock:
            self._prune_memory_bundles_locked(cutoff)
            existing = self._memory_bundles.get(session_key)
            if existing is not None:
                existing.last_active = now
                return existing.references.copy()
            self._memory_bundles[session_key] = ConversationMemoryBundle(
                references.copy(), now, now
            )
            return references.copy()

    async def async_list(self, timeout_minutes: int) -> list[dict[str, Any]]:
        """List compact non-transcript management data."""
        now = dt_util.utcnow()
        cutoff = now - timedelta(minutes=timeout_minutes)
        async with self._lock:
            self._prune_locked(cutoff)
            return [
                {
                    "key": item.key,
                    "label": item.label,
                    "last_active": item.last_active.isoformat(),
                    "expires_at": (
                        item.last_active + timedelta(minutes=timeout_minutes)
                    ).isoformat(),
                }
                for item in sorted(
                    self._sessions.values(),
                    key=lambda value: value.last_active,
                    reverse=True,
                )
            ]

    def stats(self) -> dict[str, int]:
        """Return non-sensitive counters."""
        return {
            "active_continuity_sessions": len(self._sessions),
            "continuity_resume_count": self.resume_count,
            "continuity_new_session_count": self.new_session_count,
            "active_memory_bundles": len(self._memory_bundles),
        }

    def _prune_locked(self, cutoff: Any) -> None:
        for key, session in list(self._sessions.items()):
            if session.last_active < cutoff:
                del self._sessions[key]
                self._memory_bundles.pop(f"continuity:{key}", None)

    def _prune_memory_bundles_locked(self, cutoff: Any) -> None:
        for key, bundle in list(self._memory_bundles.items()):
            if bundle.last_active < cutoff:
                del self._memory_bundles[key]


_MANAGERS = "extended_openai_conversation_responses.continuity_managers"


def async_get_continuity(
    hass: Any, entry_id: str, subentry_id: str
) -> ConversationContinuity:
    """Return the shared per-agent continuity manager."""
    managers = hass.data.setdefault(_MANAGERS, {})
    key = (entry_id, subentry_id)
    if key not in managers:
        managers[key] = ConversationContinuity(subentry_id)
    return cast(ConversationContinuity, managers[key])
