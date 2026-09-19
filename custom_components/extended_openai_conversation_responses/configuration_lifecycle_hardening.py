"""Live capability guards retained at memory/tool runtime boundaries."""

from __future__ import annotations

from functools import wraps
from typing import Any

from .agent_configuration import (
    _archive_runtime_required,
    sync_memory_embedding_provider,
)
from .const import CONF_TEMPORARY_MEMORY, DEFAULT_TEMPORARY_MEMORY, TEMPORARY_MEMORY_OFF
from .memory import memory_enabled

_INSTALLED = False


def _install_memory_embedding_lifecycle() -> None:
    """Synchronize the provider at the existing retrieval boundary."""
    from .conversation import ExtendedOpenAIAgentEntity

    original_retrieve = ExtendedOpenAIAgentEntity._async_retrieve_memories

    @wraps(original_retrieve)
    async def async_retrieve_memories(entity: Any, *args: Any, **kwargs: Any) -> Any:
        # Management updates replace subentry.data without requiring an entity reload.
        # Reconcile the cheap provider/model pointer before the next retrieval so
        # Lexical <-> Hybrid and embedding-model changes take effect immediately.
        sync_memory_embedding_provider(entity)
        return await original_retrieve(entity, *args, **kwargs)

    ExtendedOpenAIAgentEntity._async_retrieve_memories = async_retrieve_memories  # type: ignore[assignment]


def _install_runtime_configuration_lifecycle() -> None:
    """Retain live capability checks at retrieval and tool execution boundaries."""
    from .conversation import ExtendedOpenAIAgentEntity

    original_memory_retrieve = ExtendedOpenAIAgentEntity._async_retrieve_memories

    @wraps(original_memory_retrieve)
    async def retrieve_memories(entity: Any, *args: Any, **kwargs: Any) -> Any:
        if not memory_enabled(entity.subentry.data):
            return []
        return await original_memory_retrieve(entity, *args, **kwargs)

    ExtendedOpenAIAgentEntity._async_retrieve_memories = retrieve_memories  # type: ignore[assignment]

    original_temporary_retrieve = (
        ExtendedOpenAIAgentEntity._async_retrieve_temporary_memories
    )

    @wraps(original_temporary_retrieve)
    async def retrieve_temporary_memories(
        entity: Any, *args: Any, **kwargs: Any
    ) -> Any:
        if (
            entity.subentry.data.get(CONF_TEMPORARY_MEMORY, DEFAULT_TEMPORARY_MEMORY)
            == TEMPORARY_MEMORY_OFF
        ):
            return []
        return await original_temporary_retrieve(entity, *args, **kwargs)

    # These are deliberate runtime monkey patches. setattr avoids asking static
    # typing to reinterpret the @wraps helper as the original bound-method type.
    setattr(  # noqa: B010
        ExtendedOpenAIAgentEntity,
        "_async_retrieve_temporary_memories",
        retrieve_temporary_memories,
    )

    original_memory_execute = ExtendedOpenAIAgentEntity._async_execute_memory_tool

    @wraps(original_memory_execute)
    async def execute_memory(entity: Any, *args: Any, **kwargs: Any) -> Any:
        if not memory_enabled(entity.subentry.data):
            raise RuntimeError("persistent memory is disabled")
        return await original_memory_execute(entity, *args, **kwargs)

    ExtendedOpenAIAgentEntity._async_execute_memory_tool = execute_memory  # type: ignore[assignment]

    original_temporary_execute = (
        ExtendedOpenAIAgentEntity._async_execute_temporary_memory_tool
    )

    @wraps(original_temporary_execute)
    async def execute_temporary_memory(entity: Any, *args: Any, **kwargs: Any) -> Any:
        if (
            entity.subentry.data.get(CONF_TEMPORARY_MEMORY, DEFAULT_TEMPORARY_MEMORY)
            == TEMPORARY_MEMORY_OFF
        ):
            raise RuntimeError("temporary memory is disabled")
        return await original_temporary_execute(entity, *args, **kwargs)

    setattr(  # noqa: B010
        ExtendedOpenAIAgentEntity,
        "_async_execute_temporary_memory_tool",
        execute_temporary_memory,
    )

    original_archive_execute = ExtendedOpenAIAgentEntity._async_execute_archive_tool

    @wraps(original_archive_execute)
    async def execute_archive(entity: Any, *args: Any, **kwargs: Any) -> Any:
        if not _archive_runtime_required(entity.subentry.data):
            raise RuntimeError("conversation archive is disabled")
        return await original_archive_execute(entity, *args, **kwargs)

    ExtendedOpenAIAgentEntity._async_execute_archive_tool = execute_archive  # type: ignore[assignment]


def install_configuration_lifecycle_hardening() -> None:
    """Install configuration/runtime synchronization once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_memory_embedding_lifecycle()
    _install_runtime_configuration_lifecycle()
    _INSTALLED = True
