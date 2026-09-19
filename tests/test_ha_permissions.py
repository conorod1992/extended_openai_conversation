"""Tests for Home Assistant permission-boundary helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.auth import EVENT_USER_REMOVED, EVENT_USER_UPDATED
from homeassistant.core import Context, Event

from custom_components.extended_openai_conversation_responses import ha_permissions


async def test_request_owner_binds_and_restores_authenticated_context(
    entry_agent, entry_input,
):
    caller_context = Context(user_id="user-1")
    prior_context = Context(user_id="prior-user")
    seen_contexts = []
    async def process(_request):
        seen_contexts.append(ha_permissions.get_active_ha_context())
        return "result"
    entry_agent._async_process_with_continuity = process
    entry_input.context = caller_context
    with ha_permissions.bind_active_ha_context(prior_context):
        result = await entry_agent.async_process(entry_input)
        assert ha_permissions.get_active_ha_context() is prior_context
    assert result == "result"
    assert seen_contexts == [caller_context]


@pytest.mark.asyncio
async def test_setup_populates_cache_and_registers_auth_listeners(hass, monkeypatch):
    """Permission setup primes the cache and subscribes once to auth changes."""
    user_a = SimpleNamespace(id="user-a")
    user_b = SimpleNamespace(id="user-b")
    monkeypatch.setattr(
        hass.auth,
        "async_get_users",
        AsyncMock(return_value=[user_a, user_b]),
    )
    listeners: dict[str, object] = {}

    def listen(event_type, callback):
        listeners[event_type] = callback
        return Mock()

    monkeypatch.setattr(hass.bus, "async_listen", listen)
    hass.data.pop(ha_permissions._USER_CACHE_KEY, None)
    hass.data.pop(ha_permissions._SETUP_KEY, None)

    await ha_permissions.async_setup_ha_permissions(hass)

    assert hass.data[ha_permissions._USER_CACHE_KEY] == {
        "user-a": user_a,
        "user-b": user_b,
    }
    assert set(listeners) == {
        "user_added",
        "user_updated",
        "user_removed",
    }

    # A second setup refreshes the initial snapshot but must not register another
    # set of listeners.
    first_listeners = dict(listeners)
    await ha_permissions.async_setup_ha_permissions(hass)
    assert listeners == first_listeners


@pytest.mark.asyncio
async def test_auth_change_fails_closed_then_refreshes_user(hass, monkeypatch):
    """Changed users disappear from the cache until their fresh object is loaded."""
    stale = SimpleNamespace(id="user-1")
    refreshed = SimpleNamespace(id="user-1")
    monkeypatch.setattr(hass.auth, "async_get_users", AsyncMock(return_value=[stale]))
    get_user = AsyncMock(return_value=refreshed)
    monkeypatch.setattr(hass.auth, "async_get_user", get_user)
    listeners: dict[str, object] = {}
    tasks: list[asyncio.Task[None]] = []

    def listen(event_type, callback):
        listeners[event_type] = callback
        return Mock()

    def create_task(coro, _name):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr(hass.bus, "async_listen", listen)
    monkeypatch.setattr(hass, "async_create_task", create_task)
    hass.data.pop(ha_permissions._USER_CACHE_KEY, None)
    hass.data.pop(ha_permissions._SETUP_KEY, None)
    await ha_permissions.async_setup_ha_permissions(hass)

    callback = listeners[EVENT_USER_UPDATED]
    callback(Event(EVENT_USER_UPDATED, {"user_id": "user-1"}))
    assert "user-1" not in hass.data[ha_permissions._USER_CACHE_KEY]
    assert len(tasks) == 1

    await tasks[0]

    get_user.assert_awaited_once_with("user-1")
    assert hass.data[ha_permissions._USER_CACHE_KEY]["user-1"] is refreshed


@pytest.mark.asyncio
async def test_auth_change_ignores_invalid_id_and_removal_does_not_refresh(
    hass, monkeypatch
):
    """Malformed auth events are ignored and removals only evict cached users."""
    user = SimpleNamespace(id="user-1")
    monkeypatch.setattr(hass.auth, "async_get_users", AsyncMock(return_value=[user]))
    get_user = AsyncMock()
    monkeypatch.setattr(hass.auth, "async_get_user", get_user)
    listeners: dict[str, object] = {}
    create_task = Mock()

    def listen(event_type, callback):
        listeners[event_type] = callback
        return Mock()

    monkeypatch.setattr(hass.bus, "async_listen", listen)
    monkeypatch.setattr(hass, "async_create_task", create_task)
    hass.data.pop(ha_permissions._USER_CACHE_KEY, None)
    hass.data.pop(ha_permissions._SETUP_KEY, None)
    await ha_permissions.async_setup_ha_permissions(hass)

    updated = listeners[EVENT_USER_UPDATED]
    updated(Event(EVENT_USER_UPDATED, {"user_id": ""}))
    updated(Event(EVENT_USER_UPDATED, {"user_id": 123}))
    assert hass.data[ha_permissions._USER_CACHE_KEY]["user-1"] is user

    removed = listeners[EVENT_USER_REMOVED]
    removed(Event(EVENT_USER_REMOVED, {"user_id": "user-1"}))

    assert "user-1" not in hass.data[ha_permissions._USER_CACHE_KEY]
    create_task.assert_not_called()
    get_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_auth_refresh_does_not_cache_missing_user(hass, monkeypatch):
    """A changed user that no longer exists remains fail-closed in the cache."""
    user = SimpleNamespace(id="user-1")
    monkeypatch.setattr(hass.auth, "async_get_users", AsyncMock(return_value=[user]))
    monkeypatch.setattr(hass.auth, "async_get_user", AsyncMock(return_value=None))
    listeners: dict[str, object] = {}
    tasks: list[asyncio.Task[None]] = []

    def listen(event_type, callback):
        listeners[event_type] = callback
        return Mock()

    def create_task(coro, _name):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr(hass.bus, "async_listen", listen)
    monkeypatch.setattr(hass, "async_create_task", create_task)
    hass.data.pop(ha_permissions._USER_CACHE_KEY, None)
    hass.data.pop(ha_permissions._SETUP_KEY, None)
    await ha_permissions.async_setup_ha_permissions(hass)

    listeners[EVENT_USER_UPDATED](
        Event(EVENT_USER_UPDATED, {"user_id": "user-1"})
    )
    assert len(tasks) == 1
    await tasks[0]

    assert "user-1" not in hass.data[ha_permissions._USER_CACHE_KEY]
