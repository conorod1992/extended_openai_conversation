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

GUEST_CONTINUITY_NAMESPACE = "guest"
GUEST_CONVERSATION_ID_PREFIX = "extended-openai-guest-"


@dataclass(slots=True)
class ContinuityResolution:
    """Resolved conversation and the history needed if Core recreated its log."""

    conversation_id: str | None
    key: str | None
    history: list[conversation.Content]
    resumed: bool
    claim_token: str | None = None


@dataclass(slots=True)
class ActiveConversation:
    """A bounded in-process logical conversation."""

    key: str
    conversation_id: str
    label: str
    last_active: Any
    history: list[conversation.Content]
    in_flight: bool = False
    claim_token: str | None = None


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
        mode: str,
        scope: ResolvedDataScope,
        device_id: str | None,
        namespace: str | None = None,
    ) -> tuple[str | None, str]:
        """Return the safe continuity owner and a human-readable label."""
        if mode == CONVERSATION_CONTINUITY_USER and scope.scope_type == "user":
            key, label = scope.scope_id, "Resolved user"
        elif mode in {
            CONVERSATION_CONTINUITY_DEVICE,
            CONVERSATION_CONTINUITY_USER,
        }:
            if device_id:
                key, label = f"device:{device_id}", "Assist device"
            else:
                key, label = None, "Home Assistant default"
        else:
            key, label = None, "Home Assistant default"
        if key is not None and namespace is not None:
            return f"{namespace}:{key}", f"Guest {label.lower()}"
        return key, label

    async def async_resolve(
        self,
        mode: str,
        scope: ResolvedDataScope,
        device_id: str | None,
        incoming_conversation_id: str | None,
        timeout_minutes: int,
        *,
        namespace: str | None = None,
    ) -> ContinuityResolution:
        """Resolve one request using a small lock and no network I/O."""
        if namespace is None and self._is_guest_conversation_id(
            incoming_conversation_id
        ):
            # HA may return the last Guest ChatLog ID on the first owner turn after
            # Guest Mode ends. HA-default owner continuity must not reopen that log.
            incoming_conversation_id = None
        if mode == CONVERSATION_CONTINUITY_HA_DEFAULT:
            if namespace is None:
                return ContinuityResolution(incoming_conversation_id, None, [], False)
            # Preserve HA-default session behavior, but accept only a Guest-issued
            # ID. An owner ID starts a fresh, structurally marked Guest ChatLog.
            conversation_id = (
                incoming_conversation_id
                if self._is_namespaced_conversation_id(
                    incoming_conversation_id, namespace
                )
                else self._new_conversation_id(namespace)
            )
            return ContinuityResolution(conversation_id, None, [], False)
        key, label = self.identity_key(mode, scope, device_id, namespace)
        if key is None:
            if namespace is not None:
                conversation_id = (
                    incoming_conversation_id
                    if self._is_namespaced_conversation_id(
                        incoming_conversation_id, namespace
                    )
                    else self._new_conversation_id(namespace)
                )
                return ContinuityResolution(conversation_id, None, [], False)
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
                        self._new_conversation_id(namespace),
                        None,
                        [],
                        False,
                    )
                claim_token = uuid4().hex
                active.in_flight = True
                active.claim_token = claim_token
                self.resume_count += 1
                return ContinuityResolution(
                    active.conversation_id,
                    key,
                    active.history.copy(),
                    True,
                    claim_token=claim_token,
                )
            # A non-ULID caller-selected ID is explicitly supported by HA's
            # chat-session helper and remains stable if Core recreates its log.
            conversation_id = self._new_conversation_id(namespace)
            claim_token = uuid4().hex
            self._sessions[key] = ActiveConversation(
                key, conversation_id, label, now, [], True, claim_token
            )
            self.new_session_count += 1
            return ContinuityResolution(
                conversation_id, key, [], False, claim_token=claim_token
            )

    def _new_conversation_id(self, namespace: str | None) -> str:
        """Create an integration-owned ID with an inspectable privacy namespace."""
        prefix = f"extended-openai-{self._agent_id}-"
        if namespace is not None:
            prefix = f"extended-openai-{namespace}-{self._agent_id}-"
        return f"{prefix}{uuid4().hex}"

    def _is_namespaced_conversation_id(
        self, conversation_id: str | None, namespace: str
    ) -> bool:
        """Return whether an incoming ID belongs to an integration namespace."""
        return bool(
            conversation_id
            and conversation_id.startswith(
                f"extended-openai-{namespace}-{self._agent_id}-"
            )
        )

    @staticmethod
    def _is_guest_conversation_id(conversation_id: str | None) -> bool:
        """Return whether an incoming ID belongs to Guest continuity."""
        return bool(
            conversation_id and conversation_id.startswith(GUEST_CONVERSATION_ID_PREFIX)
        )

    async def async_record_success(
        self,
        key: str | None,
        claim_token: str | None,
        content: list[conversation.Content],
    ) -> None:
        """Record a successful turn only for the request that owns the claim."""
        if key is None or claim_token is None:
            return
        async with self._lock:
            active = self._sessions.get(key)
            if active is None or active.claim_token != claim_token:
                return
            active.history = content.copy()
            active.last_active = dt_util.utcnow()
            active.in_flight = False
            active.claim_token = None

    async def async_release(self, key: str | None, claim_token: str | None) -> None:
        """Release only the request claim identified by its opaque token."""
        if key is None or claim_token is None:
            return
        async with self._lock:
            active = self._sessions.get(key)
            if active is not None and active.claim_token == claim_token:
                active.in_flight = False
                active.claim_token = None

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
            if not session.in_flight and session.last_active < cutoff:
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
