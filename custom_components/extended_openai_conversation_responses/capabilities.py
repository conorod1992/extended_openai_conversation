"""Shared model-facing capability decisions for prompt and tool assembly."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .const import (
    CONF_SHARED_MEMORY_MODE,
    DEFAULT_SHARED_MEMORY_MODE,
    SHARED_MEMORY_DISABLED,
)
from .guest_mode import GuestCapabilityPolicy
from .memory import memory_enabled
from .scope import ResolvedDataScope, memory_scope_id


@dataclass(frozen=True, slots=True)
class EffectiveCapabilities:
    """Capabilities that may be advertised to the model for one request."""

    persistent_memory: bool


def persistent_memory_scope_available(
    options: Mapping[str, Any], scope: ResolvedDataScope
) -> bool:
    """Return whether the resolved scope may use persistent memory."""
    if memory_scope_id(scope) is None:
        return False
    return not (
        scope.scope_type == "shared"
        and options.get(CONF_SHARED_MEMORY_MODE, DEFAULT_SHARED_MEMORY_MODE)
        == SHARED_MEMORY_DISABLED
    )


def resolve_effective_capabilities(
    options: Mapping[str, Any],
    *,
    memory_scope_available: bool,
    guest_policy: GuestCapabilityPolicy | None = None,
) -> EffectiveCapabilities:
    """Resolve model-facing capabilities from configuration, scope, and policy."""
    policy = guest_policy or GuestCapabilityPolicy.unrestricted()
    persistent_memory = (
        memory_enabled(options)
        and memory_scope_available
        and (
            not policy.guest_active
            or policy.shared_memory_read
            or policy.shared_memory_write
        )
    )
    return EffectiveCapabilities(persistent_memory=persistent_memory)
