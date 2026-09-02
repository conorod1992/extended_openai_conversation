"""Request-scoped runtime cleanup for conversation tool handling.

Keep provider-facing tool assembly stable within a request when its dynamic inputs
have not changed, while preserving the live safety checks that can tighten Guest
Mode or expose newly loaded Function Groups between provider rounds.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any

_INTEGRATION_TOOL_TYPES = {
    "guest_mode",
    "memory",
    "temporary_memory",
    "knowledge",
    "archive",
}

_INSTALLED = False


@dataclass(frozen=True, slots=True)
class FunctionToolsSnapshot:
    """One request-local effective Function Tool assembly."""

    key: tuple[int, int, tuple[str, ...]]
    tools: tuple[dict[str, Any], ...]


_ACTIVE_FUNCTION_TOOLS_SNAPSHOT: ContextVar[FunctionToolsSnapshot | None] = ContextVar(
    "extended_openai_function_tools_snapshot", default=None
)


def _snapshot_key(
    agent: Any, conversation_module: Any
) -> tuple[int, int, tuple[str, ...]]:
    """Return the dynamic inputs that may change tool exposure mid-request."""
    policy = agent._effective_guest_policy()
    session = conversation_module._ACTIVE_FUNCTION_GROUP_SESSION.get()
    loaded_groups = (
        tuple(sorted(session.loaded_group_ids)) if session is not None else ()
    )
    return (id(agent), id(policy), loaded_groups)


def _request_function_tools(
    agent: Any,
    conversation_module: Any,
    factory: Callable[[], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Reuse one assembly until Guest policy or loaded Function Groups change."""
    # Outside a live request there is no request-stable Guest policy, so keep the
    # original behavior for previews/tests/management callers.
    if conversation_module._ACTIVE_GUEST_POLICY.get() is None:
        return factory()

    key = _snapshot_key(agent, conversation_module)
    snapshot = _ACTIVE_FUNCTION_TOOLS_SNAPSHOT.get()
    if snapshot is not None and snapshot.key == key:
        # The provider adapters only read tool dictionaries. Return a fresh list to
        # preserve the historical caller contract without rebuilding every schema.
        return list(snapshot.tools)

    tools = factory()
    # Guest Mode may have tightened between the cheap key read and the full factory.
    # Resolve the final policy once more so a restricted assembly is never cached
    # under the earlier unrestricted key.
    final_key = _snapshot_key(agent, conversation_module)
    _ACTIVE_FUNCTION_TOOLS_SNAPSHOT.set(FunctionToolsSnapshot(final_key, tuple(tools)))
    return tools


def latest_configured_function_tool(
    agent: Any, function_tool: dict[str, Any]
) -> dict[str, Any]:
    """Return the latest persisted definition for a configured Function Tool.

    Integration-owned tools are request-internal definitions and are returned
    unchanged. If a configured tool was deleted, leave the request-time object in
    place so the existing executor performs its authoritative fail-closed lookup.
    """
    function_type = function_tool.get("function", {}).get("type")
    if function_type in _INTEGRATION_TOOL_TYPES:
        return function_tool

    tool_name = function_tool.get("spec", {}).get("name")
    if not isinstance(tool_name, str) or not tool_name:
        return function_tool

    latest_entry = agent.hass.config_entries.async_get_entry(agent.entry.entry_id)
    latest_subentry = (
        latest_entry.subentries.get(agent.subentry.subentry_id)
        if latest_entry is not None
        else None
    )
    latest_data = (
        latest_subentry.data if latest_subentry is not None else agent.subentry.data
    )
    current_configured: list[dict[str, Any]] = (
        agent._configured_function_tools_from_data(latest_data)
    )
    current_tool = next(
        (
            tool
            for tool in current_configured
            if tool.get("spec", {}).get("name") == tool_name
        ),
        None,
    )
    return current_tool if current_tool is not None else function_tool


def install_runtime_cleanup() -> None:
    """Install request-local tool reuse and current-definition execution once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from . import conversation

    entity_class = conversation.ExtendedOpenAIAgentEntity
    original_process = entity_class._async_process
    original_get_function_tools = entity_class._get_function_tools
    original_execute_function_tool = entity_class._execute_function_tool

    @wraps(original_process)
    async def process_with_fresh_tool_snapshot(self: Any, user_input: Any) -> Any:
        token = _ACTIVE_FUNCTION_TOOLS_SNAPSHOT.set(None)
        try:
            return await original_process(self, user_input)
        finally:
            _ACTIVE_FUNCTION_TOOLS_SNAPSHOT.reset(token)

    @wraps(original_get_function_tools)
    def get_function_tools_cached(self: Any) -> list[dict[str, Any]]:
        return _request_function_tools(
            self,
            conversation,
            lambda: original_get_function_tools(self),
        )

    @wraps(original_execute_function_tool)
    async def execute_latest_function_tool(
        self: Any,
        function_tool: dict[str, Any],
        tool_input: Any,
        llm_context: Any,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        current_tool = latest_configured_function_tool(self, function_tool)
        return await original_execute_function_tool(
            self,
            current_tool,
            tool_input,
            llm_context,
            exposed_entities,
        )

    entity_class._async_process = process_with_fresh_tool_snapshot  # type: ignore[method-assign]
    entity_class._get_function_tools = get_function_tools_cached  # type: ignore[method-assign]
    entity_class._execute_function_tool = execute_latest_function_tool  # type: ignore[method-assign]
