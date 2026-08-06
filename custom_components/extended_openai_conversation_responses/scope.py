"""Shared identity and data-scope resolution for memory and archives."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from .const import (
    CONF_VOICE_DEFAULT_USER_ID,
    CONF_VOICE_DEVICE_MAPPINGS,
    CONF_VOICE_SCOPE_POLICY,
    CONF_VOICE_UNMAPPED_POLICY,
    DEFAULT_VOICE_SCOPE_POLICY,
    DEFAULT_VOICE_UNMAPPED_POLICY,
    VOICE_POLICY_DEFAULT_USER,
    VOICE_POLICY_DEVICE_MAPPING,
    VOICE_POLICY_SHARED,
)

LEGACY_ANONYMOUS_SCOPE_ID = "__anonymous__"
SHARED_HOUSEHOLD_SCOPE_ID = "shared:household"
UNRETAINED_SCOPE_ID = "unretained"


@dataclass(slots=True, frozen=True)
class ResolvedDataScope:
    """Stable owner selected at the beginning of an Assist session."""

    scope_id: str
    scope_type: str
    source: str
    user_id: str | None = None
    device_id: str | None = None
    display_name: str | None = None

    @property
    def allows_retention(self) -> bool:
        """Return whether this scope may own retained data."""
        return self.scope_type != "unretained"

    def as_dict(self) -> dict[str, str | None]:
        """Return a JSON-safe representation."""
        return asdict(self)


def _context_user_id(context: Any) -> str | None:
    """Read the HA user attached to the actual request context, when present."""
    ha_context = getattr(context, "context", None)
    value = getattr(ha_context, "user_id", None)
    return value if isinstance(value, str) and value else None


def _device_id(context: Any) -> str | None:
    value = getattr(context, "device_id", None)
    return value if isinstance(value, str) and value else None


def user_scope(
    user_id: str, *, source: str, device_id: str | None = None
) -> ResolvedDataScope:
    """Build one personal scope."""
    return ResolvedDataScope(
        scope_id=f"user:{user_id}",
        scope_type="user",
        source=source,
        user_id=user_id,
        device_id=device_id,
    )


def shared_scope(*, source: str, device_id: str | None = None) -> ResolvedDataScope:
    """Build the deliberately shared household scope."""
    return ResolvedDataScope(
        scope_id=SHARED_HOUSEHOLD_SCOPE_ID,
        scope_type="shared",
        source=source,
        device_id=device_id,
        display_name="Shared household",
    )


def unretained_scope(*, device_id: str | None = None) -> ResolvedDataScope:
    """Build a scope which is forbidden from retaining transcripts or memories."""
    return ResolvedDataScope(
        scope_id=UNRETAINED_SCOPE_ID,
        scope_type="unretained",
        source="unretained_policy",
        device_id=device_id,
        display_name="Not retained",
    )


def legacy_anonymous_scope() -> ResolvedDataScope:
    """Represent preserved pre-migration anonymous memory data."""
    return ResolvedDataScope(
        scope_id=LEGACY_ANONYMOUS_SCOPE_ID,
        scope_type="anonymous_legacy",
        source="legacy_anonymous",
        display_name="Legacy anonymous",
    )


def resolve_data_scope(context: Any, options: Mapping[str, Any]) -> ResolvedDataScope:
    """Resolve an authenticated or unidentified request without identity inference.

    Home Assistant currently exposes a request context user and a source device on
    ``LLMContext``.  No presence, room, Bluetooth, camera, or other guessed identity
    is consulted here.  Callers must retain this result for the whole session.
    """
    device_id = _device_id(context)
    if user_id := _context_user_id(context):
        return user_scope(user_id, source="authenticated_user", device_id=device_id)

    policy = str(options.get(CONF_VOICE_SCOPE_POLICY, DEFAULT_VOICE_SCOPE_POLICY))
    mappings = options.get(CONF_VOICE_DEVICE_MAPPINGS, {})
    if (
        policy == VOICE_POLICY_DEVICE_MAPPING
        and device_id
        and isinstance(mappings, Mapping)
    ):
        mapped = mappings.get(device_id)
        if isinstance(mapped, str) and mapped:
            if mapped in (VOICE_POLICY_SHARED, SHARED_HOUSEHOLD_SCOPE_ID):
                return shared_scope(source="device_mapping", device_id=device_id)
            if mapped not in {"unretained", UNRETAINED_SCOPE_ID}:
                mapped_user = mapped.removeprefix("user:")
                return user_scope(
                    mapped_user, source="device_mapping", device_id=device_id
                )
        policy = str(
            options.get(CONF_VOICE_UNMAPPED_POLICY, DEFAULT_VOICE_UNMAPPED_POLICY)
        )

    if policy == VOICE_POLICY_DEFAULT_USER:
        owner = options.get(CONF_VOICE_DEFAULT_USER_ID)
        if isinstance(owner, str) and owner:
            return user_scope(owner, source="agent_default_user", device_id=device_id)
    if policy == VOICE_POLICY_SHARED:
        return shared_scope(source="shared_voice_policy", device_id=device_id)
    return unretained_scope(device_id=device_id)


def memory_scope_id(scope: ResolvedDataScope) -> str | None:
    """Return the persistent-memory owner key, or ``None`` when prohibited."""
    if scope.scope_type == "user":
        return scope.user_id
    if scope.scope_type == "shared":
        return SHARED_HOUSEHOLD_SCOPE_ID
    if scope.scope_type == "anonymous_legacy":
        return LEGACY_ANONYMOUS_SCOPE_ID
    return None
