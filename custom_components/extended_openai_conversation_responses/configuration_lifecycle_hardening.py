"""Keep live agent configuration and runtime helpers in sync.

This module closes two narrow gaps where the management UI already exposed valid
configuration but runtime state did not follow it correctly:

* Conversation timeout presets are suggestions, while custom values from 1 to 1440
  minutes are valid too.
* Hybrid-memory embedding providers must follow live retrieval-mode/model changes and
  must not remain bound to a previous conversation entity instance.
"""

from __future__ import annotations

from functools import wraps
import re
from typing import Any

from . import agent_config, const
from .const import (
    CONF_CONVERSATION_TIMEOUT_MINUTES,
    CONF_MEMORY_EMBEDDING_MODEL,
    CONF_MEMORY_RETRIEVAL_MODE,
    DEFAULT_MEMORY_EMBEDDING_MODEL,
    DEFAULT_MEMORY_RETRIEVAL_MODE,
    MEMORY_RETRIEVAL_HYBRID,
)

_INSTALLED = False
_TIMEOUT_MINUTES_MIN = 1
_TIMEOUT_MINUTES_MAX = 1440
_INTEGER_STRING = re.compile(r"[+-]?\d+(?:\.0+)?")


class _ConversationTimeoutOptions(list[int]):
    """Iterate presets while accepting the full supported custom range."""

    def __contains__(self, value: object) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and _TIMEOUT_MINUTES_MIN <= value <= _TIMEOUT_MINUTES_MAX
        )


def _install_conversation_timeout_validation() -> None:
    """Make backend validation match the UI's bounded Custom timeout control."""
    presets = list(const.CONVERSATION_TIMEOUT_OPTIONS)
    options = _ConversationTimeoutOptions(presets)
    # agent_config imported the constant by value, so update both module references.
    # Iteration still returns only the five friendly presets used by the frontend.
    const.CONVERSATION_TIMEOUT_OPTIONS = options
    agent_config.CONVERSATION_TIMEOUT_OPTIONS = options

    original_coerce = agent_config._coerce_legacy_numbers

    @wraps(original_coerce)
    def coerce_legacy_numbers(config: dict[str, Any]) -> None:
        original_coerce(config)
        value = config.get(CONF_CONVERSATION_TIMEOUT_MINUTES)
        if isinstance(value, str):
            stripped = value.strip()
            if _INTEGER_STRING.fullmatch(stripped):
                config[CONF_CONVERSATION_TIMEOUT_MINUTES] = int(
                    stripped.split(".", maxsplit=1)[0]
                )
        elif isinstance(value, float) and value.is_integer():
            config[CONF_CONVERSATION_TIMEOUT_MINUTES] = int(value)

    agent_config._coerce_legacy_numbers = coerce_legacy_numbers  # type: ignore[method-assign]


def sync_memory_embedding_provider(entity: Any) -> None:
    """Apply the entity's current Hybrid-memory settings to its shared manager."""
    memory = getattr(entity, "_memory", None)
    setter = getattr(memory, "set_embedding_provider", None)
    if not callable(setter):
        # Some callers/tests use a minimal search-only memory implementation. Provider
        # synchronization applies only to the real PersistentMemory capability.
        return
    options = entity.subentry.data
    model = str(
        options.get(CONF_MEMORY_EMBEDDING_MODEL, DEFAULT_MEMORY_EMBEDDING_MODEL)
    )
    if (
        options.get(CONF_MEMORY_RETRIEVAL_MODE, DEFAULT_MEMORY_RETRIEVAL_MODE)
        == MEMORY_RETRIEVAL_HYBRID
    ):
        setter(entity._async_create_embeddings, model)
    else:
        # The manager is shared per agent and can outlive one entity instance. Clear
        # a provider left by a previous Hybrid configuration/entity when Lexical is
        # now selected.
        setter(None, model)


def _install_memory_embedding_lifecycle() -> None:
    """Refresh Hybrid-memory provider state on startup and before each retrieval."""
    from .conversation import ExtendedOpenAIAgentEntity
    from .memory import PersistentMemory

    original_set_provider = PersistentMemory.set_embedding_provider

    @wraps(original_set_provider)
    def set_embedding_provider(
        memory: PersistentMemory, provider: Any, model: str = "default"
    ) -> None:
        # Re-reading unchanged live config happens on every automatic retrieval. Avoid
        # spawning redundant prewarm tasks while still replacing a bound method from
        # an older entity instance or a newly selected model.
        if memory._embedding_provider == provider and memory._embedding_model == model:
            return
        original_set_provider(memory, provider, model)
        if provider is None:
            memory._embedding_maintenance_requested = False

    PersistentMemory.set_embedding_provider = set_embedding_provider  # type: ignore[method-assign]

    original_added = ExtendedOpenAIAgentEntity.async_added_to_hass

    @wraps(original_added)
    async def async_added_to_hass(entity: Any) -> None:
        await original_added(entity)
        # The base startup path enables Hybrid but historically did not clear a
        # provider retained by the shared manager when the new entity is Lexical.
        sync_memory_embedding_provider(entity)

    ExtendedOpenAIAgentEntity.async_added_to_hass = async_added_to_hass  # type: ignore[method-assign]

    original_retrieve = ExtendedOpenAIAgentEntity._async_retrieve_memories

    @wraps(original_retrieve)
    async def async_retrieve_memories(entity: Any, *args: Any, **kwargs: Any) -> Any:
        # Management updates replace subentry.data without requiring an entity reload.
        # Reconcile the cheap provider/model pointer before the next retrieval so
        # Lexical <-> Hybrid and embedding-model changes take effect immediately.
        sync_memory_embedding_provider(entity)
        return await original_retrieve(entity, *args, **kwargs)

    ExtendedOpenAIAgentEntity._async_retrieve_memories = async_retrieve_memories  # type: ignore[method-assign]


def install_configuration_lifecycle_hardening() -> None:
    """Install configuration/runtime synchronization once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_conversation_timeout_validation()
    _install_memory_embedding_lifecycle()
    _INSTALLED = True
