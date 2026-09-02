"""Request-scoped runtime cleanup for conversation tool handling.

Keep provider-facing tool assembly stable within a request when its dynamic inputs
have not changed, while preserving the live safety checks that can tighten Guest
Mode or expose newly loaded Function Groups between provider rounds.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any, cast

from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
)

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


@dataclass(slots=True)
class FunctionCallBudget:
    """Request-local ceiling for model-requested function executions."""

    limit: int
    used: int = 0

    @property
    def exhausted(self) -> bool:
        """Return whether no further function execution is permitted."""
        return self.limit >= 0 and self.used >= self.limit

    def claim(self, tool_name: str) -> None:
        """Claim one execution slot or fail before an over-budget tool can run."""
        if self.exhausted:
            raise HomeAssistantError(
                f"Function call limit of {self.limit} reached; refusing to execute "
                f"additional tool `{tool_name}`"
            )
        self.used += 1


_ACTIVE_FUNCTION_TOOLS_SNAPSHOT: ContextVar[FunctionToolsSnapshot | None] = ContextVar(
    "extended_openai_function_tools_snapshot", default=None
)
_ACTIVE_FUNCTION_CALL_BUDGET: ContextVar[FunctionCallBudget | None] = ContextVar(
    "extended_openai_function_call_budget", default=None
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


def _budgeted_function_tools(
    budget: FunctionCallBudget,
    factory: Callable[[], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Stop advertising ordinary functions once the actual execution cap is used."""
    if budget.exhausted:
        return []
    return factory()


def _request_options(
    agent: Any, positional_tail: list[Any], kwargs: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Resolve request options using the base handler's existing fallback rules."""
    options = kwargs.get("request_options")
    if options is None and len(positional_tail) > 6:
        options = positional_tail[6]
    return cast(Mapping[str, Any], options or agent.subentry.data)


def _assert_tool_loop_completed(chat_log: Any, max_iterations: int) -> None:
    """Turn hard-loop exhaustion into an explicit failure instead of fall-through."""
    outstanding = chat_log.unresponded_tool_results
    if not outstanding:
        return
    try:
        count = len(outstanding)
    except TypeError:
        count = 1
    raise HomeAssistantError(
        "Provider tool loop exceeded the safety limit of "
        f"{max_iterations} requests with {count} unresolved tool result(s)"
    )


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
    """Install request-local tool reuse and execution hardening once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from . import conversation
    from .entity import MAX_TOOL_ITERATIONS

    entity_class = conversation.ExtendedOpenAIAgentEntity
    original_process = entity_class._async_process
    original_handle_chat_log = entity_class._async_handle_chat_log
    original_get_function_tools = entity_class._get_function_tools
    original_execute_function_tool = entity_class._execute_function_tool

    @wraps(original_process)
    async def process_with_fresh_tool_snapshot(self: Any, user_input: Any) -> Any:
        token = _ACTIVE_FUNCTION_TOOLS_SNAPSHOT.set(None)
        try:
            return await original_process(self, user_input)
        finally:
            _ACTIVE_FUNCTION_TOOLS_SNAPSHOT.reset(token)

    @wraps(original_handle_chat_log)
    async def handle_chat_log_with_function_budget(
        self: Any,
        chat_log: Any,
        function_tools: list[dict[str, Any]],
        exposed_entities: list[dict[str, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        positional_tail = list(args)
        call_kwargs = dict(kwargs)
        options = _request_options(self, positional_tail, call_kwargs)
        budget = FunctionCallBudget(
            int(
                options.get(
                    CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
                    DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
                )
            )
        )

        if "function_tools_factory" in call_kwargs:
            original_factory = call_kwargs["function_tools_factory"]
        elif len(positional_tail) > 4:
            original_factory = positional_tail[4]
        else:
            original_factory = None

        def base_factory() -> list[dict[str, Any]]:
            if original_factory is None:
                return list(function_tools)
            return cast(list[dict[str, Any]], original_factory())

        def budgeted_factory() -> list[dict[str, Any]]:
            return _budgeted_function_tools(budget, base_factory)

        if "function_tools_factory" in call_kwargs:
            call_kwargs["function_tools_factory"] = budgeted_factory
        elif len(positional_tail) > 4:
            positional_tail[4] = budgeted_factory
        else:
            call_kwargs["function_tools_factory"] = budgeted_factory

        token = _ACTIVE_FUNCTION_CALL_BUDGET.set(budget)
        try:
            result = await original_handle_chat_log(
                self,
                chat_log,
                function_tools,
                exposed_entities,
                *positional_tail,
                **call_kwargs,
            )
            _assert_tool_loop_completed(chat_log, MAX_TOOL_ITERATIONS)
            return result
        finally:
            _ACTIVE_FUNCTION_CALL_BUDGET.reset(token)

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
        budget = _ACTIVE_FUNCTION_CALL_BUDGET.get()
        if budget is not None:
            # Loader/finalizer calls are removed by _async_handle_chat_log before
            # this execution seam, so only real model-requested functions consume
            # the configured conversation budget.
            budget.claim(str(tool_input.tool_name))
        current_tool = latest_configured_function_tool(self, function_tool)
        return await original_execute_function_tool(
            self,
            current_tool,
            tool_input,
            llm_context,
            exposed_entities,
        )

    entity_class._async_process = process_with_fresh_tool_snapshot  # type: ignore[method-assign]
    entity_class._async_handle_chat_log = handle_chat_log_with_function_budget  # type: ignore[method-assign]
    entity_class._get_function_tools = get_function_tools_cached  # type: ignore[method-assign]
    entity_class._execute_function_tool = execute_latest_function_tool  # type: ignore[method-assign]
