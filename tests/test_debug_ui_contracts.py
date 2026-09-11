"""Contract tests for the administrator request-debug WebSocket boundary."""

from __future__ import annotations

from inspect import unwrap
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.debug import (
    get_debug_manager,
)
from custom_components.extended_openai_conversation_responses.debug_ui import (
    websocket_request_debug,
)


class _ConfigEntries:
    def __init__(self, entries):
        self._entries = {entry.entry_id: entry for entry in entries}

    def async_get_entry(self, entry_id):
        return self._entries.get(entry_id)

    def async_entries(self, domain=None):
        entries = list(self._entries.values())
        if domain is None:
            return entries
        return [entry for entry in entries if entry.domain == domain]


class _Connection:
    def __init__(self, *, is_admin: bool = True) -> None:
        self.user = SimpleNamespace(is_admin=is_admin)
        self.results = []
        self.errors = []

    def send_result(self, msg_id, result) -> None:
        self.results.append((msg_id, result))

    def send_error(self, msg_id, code, message) -> None:
        self.errors.append((msg_id, code, message))


def _hass():
    conversation = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Primary agent",
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        domain=DOMAIN,
        subentries={conversation.subentry_id: conversation},
    )
    return SimpleNamespace(data={}, config_entries=_ConfigEntries([entry]))


def _message(action: str, **extra):
    return {
        "id": 1,
        "type": f"{DOMAIN}/request_debug",
        "action": action,
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        **extra,
    }


async def _call_websocket(hass, connection, message) -> None:
    """Invoke the coroutine beneath Home Assistant's async_response scheduler."""
    handler = unwrap(websocket_request_debug)
    await handler(hass, connection, message)


@pytest.mark.asyncio
async def test_debug_websocket_requires_admin() -> None:
    hass = _hass()
    connection = _Connection(is_admin=False)

    await _call_websocket(hass, connection, _message("status"))

    assert connection.results == []
    assert connection.errors == [(1, "unauthorized", "Administrator required")]
    assert hass.data == {}


@pytest.mark.asyncio
async def test_debug_websocket_agents_status_configure_runs_get_and_clear() -> None:
    hass = _hass()
    connection = _Connection()

    agents_message = _message("agents")
    agents_message.pop("entry_id")
    agents_message.pop("subentry_id")
    await _call_websocket(hass, connection, agents_message)
    assert connection.results[-1][1] == {
        "agents": [
            {
                "entry_id": "entry-1",
                "subentry_id": "agent-1",
                "title": "Primary agent",
            }
        ]
    }

    await _call_websocket(hass, connection, _message("status"))
    assert connection.results[-1][1]["enabled"] is False
    assert connection.results[-1][1]["volatile"] is True

    await _call_websocket(
        hass,
        connection,
        _message("configure", enabled=True, limit=5),
    )
    assert connection.results[-1][1]["enabled"] is True
    assert connection.results[-1][1]["limit"] == 5

    manager = get_debug_manager(hass, "entry-1", "agent-1")
    trace = manager.begin(
        entry_id="entry-1",
        subentry_id="agent-1",
        user_input={"text": "hello"},
        incoming_conversation_id=None,
    )
    manager.finish(trace, successful=True, result={"response": "done"})

    await _call_websocket(hass, connection, _message("runs"))
    runs = connection.results[-1][1]
    assert runs["count"] == 1
    assert runs["runs"][0]["debug_id"] == trace.debug_id

    await _call_websocket(
        hass,
        connection,
        _message("get", debug_id=trace.debug_id),
    )
    result = connection.results[-1][1]
    assert result["trace"]["debug_id"] == trace.debug_id
    assert result["trace"]["result"] == {"response": "done"}

    await _call_websocket(
        hass,
        connection,
        _message("clear", confirm=True),
    )
    cleared = connection.results[-1][1]
    assert cleared["deleted"] == 1
    assert cleared["count"] == 0
    assert connection.errors == []


@pytest.mark.asyncio
async def test_debug_websocket_get_missing_run_is_invalid_request() -> None:
    hass = _hass()
    connection = _Connection()

    await _call_websocket(
        hass,
        connection,
        _message("get", debug_id="missing"),
    )

    assert connection.results == []
    assert connection.errors == [(1, "invalid_request", "Debug run not found")]


@pytest.mark.asyncio
async def test_debug_websocket_clear_requires_explicit_confirmation() -> None:
    hass = _hass()
    connection = _Connection()

    await _call_websocket(hass, connection, _message("clear"))

    assert connection.results == []
    assert connection.errors == [
        (1, "invalid_request", "Explicit confirmation is required")
    ]


@pytest.mark.asyncio
async def test_debug_websocket_rejects_invalid_target_and_unknown_action() -> None:
    hass = _hass()
    connection = _Connection()

    await _call_websocket(
        hass,
        connection,
        {**_message("status"), "subentry_id": "missing"},
    )
    assert connection.errors[-1] == (
        1,
        "invalid_request",
        "Conversation agent not found",
    )

    await _call_websocket(hass, connection, _message("unknown"))
    assert connection.errors[-1] == (
        1,
        "invalid_request",
        "Unknown debug action: unknown",
    )
