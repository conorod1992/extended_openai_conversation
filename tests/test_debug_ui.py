"""Tests for the request-debug websocket and UI setup."""

from __future__ import annotations

from inspect import unwrap
from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.extended_openai_conversation_responses import debug_ui
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from homeassistant.exceptions import HomeAssistantError


class _Connection:
    def __init__(self, *, is_admin: bool = True) -> None:
        self.user = SimpleNamespace(is_admin=is_admin)
        self.results: list[tuple[int, Any]] = []
        self.errors: list[tuple[int, str, str]] = []

    def send_result(self, msg_id: int, result: Any) -> None:
        self.results.append((msg_id, result))

    def send_error(self, msg_id: int, code: str, message: str) -> None:
        self.errors.append((msg_id, code, message))


class _Manager:
    def __init__(self) -> None:
        self.enabled = False
        self.limit = 10
        self.configure_calls: list[tuple[Any, Any]] = []
        self.clear_calls = 0

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "limit": self.limit}

    def configure(self, *, enabled: Any, limit: Any) -> None:
        self.configure_calls.append((enabled, limit))
        if enabled is not None:
            self.enabled = enabled
        if limit is not None:
            self.limit = limit

    def clear(self) -> int:
        self.clear_calls += 1
        return 3


async def _call_websocket(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    """Invoke the coroutine beneath Home Assistant's async_response scheduler."""
    handler = unwrap(debug_ui.websocket_request_debug)
    await handler(hass, connection, msg)


def _subentry(
    subentry_id: str,
    *,
    title: str,
    subentry_type: str = "conversation",
) -> SimpleNamespace:
    return SimpleNamespace(
        subentry_id=subentry_id,
        subentry_type=subentry_type,
        title=title,
    )


def _entry(
    entry_id: str,
    *subentries: SimpleNamespace,
    domain: str = DOMAIN,
) -> SimpleNamespace:
    return SimpleNamespace(
        entry_id=entry_id,
        domain=domain,
        subentries={item.subentry_id: item for item in subentries},
    )


def test_agents_filters_non_conversation_subentries_and_sorts_titles() -> None:
    entries = [
        _entry(
            "entry-1",
            _subentry("beta", title="beta"),
            _subentry("ignored", title="Ignored", subentry_type="ai_task_data"),
        ),
        _entry("entry-2", _subentry("alpha", title="Alpha")),
    ]
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda domain: entries if domain == DOMAIN else [])
    )

    assert debug_ui._agents(cast(Any, hass)) == [
        {"entry_id": "entry-2", "subentry_id": "alpha", "title": "Alpha"},
        {"entry_id": "entry-1", "subentry_id": "beta", "title": "beta"},
    ]


def test_manager_validates_entry_and_conversation_subentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid = _entry("entry", _subentry("agent", title="Agent"))
    wrong_domain = _entry(
        "wrong-domain",
        _subentry("agent", title="Agent"),
        domain="other",
    )
    wrong_type = _entry(
        "wrong-type",
        _subentry("task", title="Task", subentry_type="ai_task_data"),
    )
    entries = {
        valid.entry_id: valid,
        wrong_domain.entry_id: wrong_domain,
        wrong_type.entry_id: wrong_type,
    }
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=lambda entry_id: entries.get(entry_id))
    )

    with pytest.raises(HomeAssistantError, match="entry_id and subentry_id are required"):
        debug_ui._manager(cast(Any, hass), {"entry_id": "entry"})

    for entry_id in ("missing", "wrong-domain"):
        with pytest.raises(HomeAssistantError, match="Integration entry not found"):
            debug_ui._manager(
                cast(Any, hass),
                {"entry_id": entry_id, "subentry_id": "agent"},
            )

    for entry_id, subentry_id in (("entry", "missing"), ("wrong-type", "task")):
        with pytest.raises(HomeAssistantError, match="Conversation agent not found"):
            debug_ui._manager(
                cast(Any, hass),
                {"entry_id": entry_id, "subentry_id": subentry_id},
            )

    sentinel = object()
    monkeypatch.setattr(debug_ui, "get_debug_manager", lambda *args: sentinel)
    assert (
        debug_ui._manager(
            cast(Any, hass),
            {"entry_id": "entry", "subentry_id": "agent"},
        )
        is sentinel
    )


@pytest.mark.asyncio
async def test_websocket_rejects_non_admin_before_accessing_debug_state() -> None:
    connection = _Connection(is_admin=False)

    await _call_websocket(
        cast(Any, SimpleNamespace()),
        cast(Any, connection),
        {"id": 1, "action": "status"},
    )

    assert connection.results == []
    assert connection.errors == [(1, "unauthorized", "Administrator required")]


@pytest.mark.asyncio
async def test_websocket_agents_does_not_require_agent_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()
    monkeypatch.setattr(
        debug_ui,
        "_agents",
        lambda hass: [{"entry_id": "entry", "subentry_id": "agent", "title": "Agent"}],
    )

    await _call_websocket(
        cast(Any, SimpleNamespace()),
        cast(Any, connection),
        {"id": 2, "action": "agents"},
    )

    assert connection.errors == []
    assert connection.results == [
        (
            2,
            {
                "agents": [
                    {"entry_id": "entry", "subentry_id": "agent", "title": "Agent"}
                ]
            },
        )
    ]


@pytest.mark.asyncio
async def test_websocket_status_configure_runs_and_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _Manager()
    monkeypatch.setattr(debug_ui, "_manager", lambda hass, msg: manager)
    monkeypatch.setattr(
        debug_ui,
        "debug_run_summaries",
        lambda current: [{"debug_id": "run-1"}] if current is manager else [],
    )
    hass = cast(Any, SimpleNamespace())

    connection = _Connection()
    await _call_websocket(
        hass,
        cast(Any, connection),
        {"id": 3, "action": "status"},
    )
    assert connection.results[-1] == (3, {"enabled": False, "limit": 10})

    await _call_websocket(
        hass,
        cast(Any, connection),
        {"id": 4, "action": "configure", "enabled": True, "limit": 25},
    )
    assert manager.configure_calls == [(True, 25)]
    assert connection.results[-1] == (4, {"enabled": True, "limit": 25})

    await _call_websocket(
        hass,
        cast(Any, connection),
        {"id": 5, "action": "runs"},
    )
    assert connection.results[-1] == (
        5,
        {"runs": [{"debug_id": "run-1"}], "enabled": True, "limit": 25},
    )

    await _call_websocket(
        hass,
        cast(Any, connection),
        {"id": 6, "action": "clear", "confirm": True},
    )
    assert manager.clear_calls == 1
    assert connection.results[-1] == (
        6,
        {"deleted": 3, "enabled": True, "limit": 25},
    )


@pytest.mark.asyncio
async def test_websocket_get_bounds_provider_page_and_reports_missing_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _Manager()
    calls: list[tuple[str, int, int]] = []

    def trace_page(
        current: object,
        debug_id: str,
        *,
        provider_offset: int,
        provider_limit: int,
    ) -> dict[str, Any] | None:
        assert current is manager
        calls.append((debug_id, provider_offset, provider_limit))
        if debug_id == "missing":
            return None
        return {"debug_id": debug_id, "provider_requests": []}

    monkeypatch.setattr(debug_ui, "_manager", lambda hass, msg: manager)
    monkeypatch.setattr(debug_ui, "debug_trace_page", trace_page)
    hass = cast(Any, SimpleNamespace())
    connection = _Connection()

    await _call_websocket(
        hass,
        cast(Any, connection),
        {
            "id": 7,
            "action": "get",
            "debug_id": "run-1",
            "provider_offset": -50,
            "provider_limit": debug_ui.MANAGEMENT_DEBUG_PROVIDER_PAGE_MAX + 100,
        },
    )
    assert calls[-1] == (
        "run-1",
        0,
        debug_ui.MANAGEMENT_DEBUG_PROVIDER_PAGE_MAX,
    )
    assert connection.results[-1] == (
        7,
        {"trace": {"debug_id": "run-1", "provider_requests": []}},
    )

    await _call_websocket(
        hass,
        cast(Any, connection),
        {"id": 8, "action": "get", "debug_id": "missing", "provider_limit": 0},
    )
    assert calls[-1] == ("missing", 0, 1)
    assert connection.errors[-1] == (8, "invalid_request", "Debug run not found")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("msg", "message"),
    [
        ({"id": 9, "action": "get"}, "debug_id is required"),
        (
            {"id": 10, "action": "clear", "confirm": False},
            "Explicit confirmation is required",
        ),
        ({"id": 11, "action": "unknown"}, "Unknown debug action: unknown"),
    ],
)
async def test_websocket_translates_request_errors(
    monkeypatch: pytest.MonkeyPatch,
    msg: dict[str, Any],
    message: str,
) -> None:
    monkeypatch.setattr(debug_ui, "_manager", lambda hass, current: _Manager())
    connection = _Connection()

    await _call_websocket(
        cast(Any, SimpleNamespace()), cast(Any, connection), msg
    )

    assert connection.results == []
    assert connection.errors == [(msg["id"], "invalid_request", message)]


@pytest.mark.asyncio
async def test_websocket_translates_runtime_and_value_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()

    def fail_manager(hass: object, msg: object) -> object:
        raise RuntimeError("manager unavailable")

    monkeypatch.setattr(debug_ui, "_manager", fail_manager)
    await _call_websocket(
        cast(Any, SimpleNamespace()),
        cast(Any, connection),
        {"id": 12, "action": "status"},
    )
    assert connection.errors[-1] == (12, "invalid_request", "manager unavailable")

    manager = _Manager()
    monkeypatch.setattr(debug_ui, "_manager", lambda hass, msg: manager)
    await _call_websocket(
        cast(Any, SimpleNamespace()),
        cast(Any, connection),
        {
            "id": 13,
            "action": "get",
            "debug_id": "run",
            "provider_offset": "not-an-integer",
        },
    )
    assert connection.errors[-1][0:2] == (13, "invalid_request")


@pytest.mark.asyncio
async def test_setup_debug_ui_registers_assets_and_command_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostics_calls = 0
    command_calls: list[object] = []
    static_calls: list[list[Any]] = []

    def install_diagnostics() -> None:
        nonlocal diagnostics_calls
        diagnostics_calls += 1

    async def register_static_paths(paths: list[Any]) -> None:
        static_calls.append(paths)

    monkeypatch.setattr(
        debug_ui, "install_payload_latency_diagnostics", install_diagnostics
    )
    monkeypatch.setattr(
        debug_ui.websocket_api,
        "async_register_command",
        lambda hass, command: command_calls.append(command),
    )
    hass = SimpleNamespace(
        data={},
        http=SimpleNamespace(async_register_static_paths=register_static_paths),
    )

    await debug_ui.async_setup_debug_ui(cast(Any, hass))
    await debug_ui.async_setup_debug_ui(cast(Any, hass))

    assert diagnostics_calls == 2
    assert len(static_calls) == 1
    assert len(static_calls[0]) == 2
    assert [path.url_path for path in static_calls[0]] == [
        f"/{DOMAIN}/debug-panel.js",
        f"/{DOMAIN}/debug-management.js",
    ]
    assert all(path.cache_headers is False for path in static_calls[0])
    assert command_calls == [debug_ui.websocket_request_debug]
