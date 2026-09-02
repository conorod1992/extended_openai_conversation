"""Tests for request-scoped Function Tool runtime cleanup."""

from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.extended_openai_conversation_responses.runtime_cleanup import (
    _ACTIVE_FUNCTION_TOOLS_SNAPSHOT,
    _request_function_tools,
    latest_configured_function_tool,
)


class _ConversationState:
    def __init__(self) -> None:
        self._ACTIVE_GUEST_POLICY = SimpleNamespace(get=lambda: self.policy)
        self._ACTIVE_FUNCTION_GROUP_SESSION = SimpleNamespace(get=lambda: self.session)
        self.policy = object()
        self.session = SimpleNamespace(loaded_group_ids=set())


class _Agent:
    def __init__(self, state: _ConversationState) -> None:
        self.state = state

    def _effective_guest_policy(self):
        return self.state.policy


def test_request_function_tools_reuses_unchanged_assembly() -> None:
    """The duplicate first-round tool lookup reuses the initial request assembly."""
    state = _ConversationState()
    agent = _Agent(state)
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        return [{"spec": {"name": "light_state"}}]

    token = _ACTIVE_FUNCTION_TOOLS_SNAPSHOT.set(None)
    try:
        first = _request_function_tools(agent, state, factory)
        second = _request_function_tools(agent, state, factory)
    finally:
        _ACTIVE_FUNCTION_TOOLS_SNAPSHOT.reset(token)

    assert calls == 1
    assert first == second
    assert first is not second


def test_request_function_tools_refreshes_after_group_load() -> None:
    """Loading an on-demand Function Group invalidates the request-local snapshot."""
    state = _ConversationState()
    agent = _Agent(state)
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        return [{"spec": {"name": f"tool_{calls}"}}]

    token = _ACTIVE_FUNCTION_TOOLS_SNAPSHOT.set(None)
    try:
        first = _request_function_tools(agent, state, factory)
        state.session.loaded_group_ids.add("reminders")
        second = _request_function_tools(agent, state, factory)
    finally:
        _ACTIVE_FUNCTION_TOOLS_SNAPSHOT.reset(token)

    assert calls == 2
    assert first != second


def test_request_function_tools_refreshes_after_guest_tightening() -> None:
    """A new pinned Guest policy invalidates the unrestricted tool snapshot."""
    state = _ConversationState()
    agent = _Agent(state)
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        return [{"spec": {"name": f"tool_{calls}"}}]

    token = _ACTIVE_FUNCTION_TOOLS_SNAPSHOT.set(None)
    try:
        _request_function_tools(agent, state, factory)
        state.policy = object()
        _request_function_tools(agent, state, factory)
    finally:
        _ACTIVE_FUNCTION_TOOLS_SNAPSHOT.reset(token)

    assert calls == 2


def test_latest_configured_function_tool_returns_current_definition() -> None:
    """Execution uses the latest persisted schema/config rather than the stale request copy."""
    stale = {
        "spec": {"name": "notify", "parameters": {"type": "object"}},
        "function": {"type": "service", "service": "notify.old"},
    }
    current = {
        "spec": {"name": "notify", "parameters": {"type": "object"}},
        "function": {"type": "service", "service": "notify.current"},
    }
    latest_subentry = SimpleNamespace(data={"revision": 2})
    latest_entry = SimpleNamespace(subentries={"agent": latest_subentry})
    agent = SimpleNamespace(
        hass=SimpleNamespace(
            config_entries=SimpleNamespace(
                async_get_entry=Mock(return_value=latest_entry)
            )
        ),
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent", data={"revision": 1}),
        _configured_function_tools_from_data=Mock(return_value=[current]),
    )

    result = latest_configured_function_tool(agent, stale)

    assert result is current
    agent._configured_function_tools_from_data.assert_called_once_with(
        latest_subentry.data
    )


def test_latest_configured_function_tool_leaves_integration_tools_unchanged() -> None:
    """Integration-owned runtime tools are not looked up in configured Function Tools."""
    tool = {
        "spec": {"name": "memory_search"},
        "function": {"type": "memory", "operation": "search"},
    }
    agent = SimpleNamespace()

    assert latest_configured_function_tool(agent, tool) is tool
