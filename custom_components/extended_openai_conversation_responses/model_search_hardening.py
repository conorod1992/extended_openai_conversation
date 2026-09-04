"""Harden model-facing local search execution without changing retrieval semantics."""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)
_INSTALLED = False


def _require_nonblank_query(arguments: dict[str, Any]) -> str:
    """Return a model-provided search query only when it contains useful text."""
    query = arguments.get("query")
    if not isinstance(query, str):
        raise ValueError("query is required")
    if not query.strip():
        raise ValueError("query must not be blank")
    return query


def install_model_search_hardening() -> None:
    """Install bounded model-search guards on the conversation agent."""
    global _INSTALLED
    if _INSTALLED:
        return

    from .conversation import ExtendedOpenAIAgentEntity

    original_rank_memories = ExtendedOpenAIAgentEntity._async_rank_memories
    original_memory_tool = ExtendedOpenAIAgentEntity._async_execute_memory_tool
    original_knowledge_tool = ExtendedOpenAIAgentEntity._async_execute_knowledge_tool
    original_archive_tool = ExtendedOpenAIAgentEntity._async_execute_archive_tool

    async def async_rank_memories(
        agent: Any, readable_scope_ids: list[str], query: str, limit: int
    ) -> Any:
        if not query.strip():
            return []
        return await original_rank_memories(agent, readable_scope_ids, query, limit)

    async def async_execute_memory_tool(
        agent: Any,
        operation: str,
        arguments: dict[str, Any],
        llm_context: Any,
    ) -> dict[str, Any]:
        if operation == "search":
            _require_nonblank_query(arguments)
        return await original_memory_tool(agent, operation, arguments, llm_context)

    async def async_execute_knowledge_tool(
        agent: Any, operation: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if operation == "search":
            _require_nonblank_query(arguments)
        return await original_knowledge_tool(agent, operation, arguments)

    async def async_execute_archive_tool(
        agent: Any, operation: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if operation == "search":
            _require_nonblank_query(arguments)
        try:
            return await original_archive_tool(agent, operation, arguments)
        except (RuntimeError, ValueError):
            raise
        except Exception:
            _LOGGER.exception("Conversation Archive tool failed")
            return {
                "status": "unavailable",
                "error": "Conversation Archive is temporarily unavailable",
            }

    async_rank_memories._extended_openai_model_search_hardening = True  # type: ignore[attr-defined]
    async_execute_memory_tool._extended_openai_model_search_hardening = True  # type: ignore[attr-defined]
    async_execute_knowledge_tool._extended_openai_model_search_hardening = True  # type: ignore[attr-defined]
    async_execute_archive_tool._extended_openai_model_search_hardening = True  # type: ignore[attr-defined]
    ExtendedOpenAIAgentEntity._async_rank_memories = async_rank_memories  # type: ignore[method-assign,assignment]
    ExtendedOpenAIAgentEntity._async_execute_memory_tool = async_execute_memory_tool  # type: ignore[method-assign,assignment]
    ExtendedOpenAIAgentEntity._async_execute_knowledge_tool = async_execute_knowledge_tool  # type: ignore[method-assign,assignment]
    ExtendedOpenAIAgentEntity._async_execute_archive_tool = async_execute_archive_tool  # type: ignore[method-assign,assignment]
    _INSTALLED = True
