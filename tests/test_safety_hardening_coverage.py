"""Focused behavioral coverage for model-facing safety hardening."""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import safety_hardening
from custom_components.extended_openai_conversation_responses.ha_permissions import (
    get_active_ha_context,
    set_active_ha_context,
)


def test_install_safety_hardening_is_idempotent(monkeypatch) -> None:
    """A repeated install must not stack another layer of global wrappers."""
    delayed = AsyncMock()
    native = AsyncMock()
    broadcast = AsyncMock()
    schema = AsyncMock()
    monkeypatch.setattr(safety_hardening, "_INSTALLED", False)
    monkeypatch.setattr(safety_hardening, "_install_delayed_permission_context", delayed)
    monkeypatch.setattr(safety_hardening, "_install_native_tool_guards", native)
    monkeypatch.setattr(safety_hardening, "_install_broadcast_state_transactions", broadcast)
    monkeypatch.setattr(safety_hardening, "_update_builtin_resource_schema", schema)

    safety_hardening.install_safety_hardening()
    safety_hardening.install_safety_hardening()

    delayed.assert_called_once()
    native.assert_called_once()
    broadcast.assert_called_once()
    schema.assert_called_once()


@pytest.mark.asyncio
async def test_delayed_permission_context_uses_durable_origin_and_restores_caller(
    monkeypatch,
) -> None:
    """Recovered delayed calls execute as their persisted origin, without leaking it."""
    from custom_components.extended_openai_conversation_responses.delayed_tools import (
        DelayedToolManager,
    )

    seen: list[tuple[str, str | None]] = []

    async def original(manager, call_id):
        context = get_active_ha_context()
        seen.append((call_id, getattr(context, "user_id", None)))
        return True

    monkeypatch.setattr(DelayedToolManager, "_async_execute_due", original)
    safety_hardening._install_delayed_permission_context()
    wrapped = DelayedToolManager._async_execute_due

    safety_hardening._install_delayed_permission_context()
    assert DelayedToolManager._async_execute_due is wrapped

    previous = get_active_ha_context()
    caller = Context(user_id="caller-user")
    set_active_ha_context(caller)
    try:
        missing = SimpleNamespace(_records={})
        assert await wrapped(missing, "missing") is True
        assert get_active_ha_context() is caller

        recovered = SimpleNamespace(
            _records={"due": SimpleNamespace(user_id="origin-user")}
        )
        assert await wrapped(recovered, "due") is True
        assert get_active_ha_context() is caller
    finally:
        set_active_ha_context(previous)

    assert seen == [("missing", "caller-user"), ("due", "origin-user")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user",
    [
        None,
        SimpleNamespace(is_active=False, is_admin=True),
        SimpleNamespace(is_active=True, is_admin=False),
    ],
)
async def test_require_admin_rejects_missing_inactive_and_non_admin_users(user) -> None:
    """Durable HA mutation fails closed for every non-active-admin identity."""
    hass = SimpleNamespace(auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user)))
    llm_context = SimpleNamespace(context=Context(user_id="user-1"))

    with pytest.raises(HomeAssistantError, match="active Home Assistant administrator"):
        await safety_hardening._async_require_admin(hass, llm_context)

    hass.auth.async_get_user.assert_awaited_once_with("user-1")


@pytest.mark.asyncio
async def test_require_admin_accepts_active_admin_from_active_context() -> None:
    """The active HA context is a valid fallback when the LLM context has none."""
    admin = SimpleNamespace(is_active=True, is_admin=True)
    hass = SimpleNamespace(auth=SimpleNamespace(async_get_user=AsyncMock(return_value=admin)))
    previous = get_active_ha_context()
    set_active_ha_context(Context(user_id="admin-user"))
    try:
        await safety_hardening._async_require_admin(hass, SimpleNamespace(context=None))
    finally:
        set_active_ha_context(previous)

    hass.auth.async_get_user.assert_awaited_once_with("admin-user")


@pytest.mark.parametrize("value", [123, "definitely-not-a-datetime"])
def test_parse_datetime_rejects_non_iso_values(value) -> None:
    """Recorder bounds reject malformed timestamps before querying Recorder."""
    with pytest.raises(HomeAssistantError, match="start_time must be an ISO 8601 datetime"):
        safety_hardening._parse_datetime(value, "start_time")


def test_history_validation_rejects_oversized_entity_fanout() -> None:
    """History requests cannot bypass the entity cardinality bound."""
    with pytest.raises(HomeAssistantError, match="entity_ids may contain at most"):
        safety_hardening._validate_history_request(
            {
                "entity_ids": [
                    f"sensor.entity_{index}"
                    for index in range(safety_hardening.MAX_HISTORY_ENTITY_IDS + 1)
                ]
            }
        )


def test_history_validation_rejects_inverted_and_excessive_windows() -> None:
    """History rejects both inverted and overlong explicit Recorder windows."""
    with pytest.raises(HomeAssistantError, match="end_time must be after start_time"):
        safety_hardening._validate_history_request(
            {
                "entity_ids": ["sensor.one"],
                "start_time": "2026-01-02T00:00:00+00:00",
                "end_time": "2026-01-01T00:00:00+00:00",
            }
        )

    with pytest.raises(HomeAssistantError, match="may not exceed 31 days"):
        safety_hardening._validate_history_request(
            {
                "entity_ids": ["sensor.one"],
                "start_time": "2026-01-01T00:00:00+00:00",
                "end_time": "2026-02-02T00:00:00+00:00",
            }
        )


def test_execute_service_validation_rejects_invalid_and_oversized_batches() -> None:
    """Native service execution is bounded before Home Assistant actions begin."""
    with pytest.raises(HomeAssistantError, match="list must be an array"):
        safety_hardening._validate_execute_service_request({"list": "not-a-list"})

    with pytest.raises(HomeAssistantError, match="may contain at most"):
        safety_hardening._validate_execute_service_request(
            {
                "list": [
                    {"domain": "light", "service": "turn_on"}
                    for _ in range(safety_hardening.MAX_NATIVE_SERVICE_ACTIONS + 1)
                ]
            }
        )


def test_statistics_validation_defaults_period_and_rejects_bad_period_or_window() -> None:
    """Statistics normalizes a safe default and enforces period-specific windows."""
    normalized = safety_hardening._normalized_statistics_arguments(
        {
            "start_time": "2026-01-01T00:00:00+00:00",
            "end_time": "2026-01-02T00:00:00+00:00",
        }
    )
    assert normalized["period"] == "day"

    with pytest.raises(HomeAssistantError, match="Unsupported statistics period"):
        safety_hardening._normalized_statistics_arguments(
            {
                "period": "second",
                "start_time": "2026-01-01T00:00:00+00:00",
                "end_time": "2026-01-02T00:00:00+00:00",
            }
        )

    with pytest.raises(HomeAssistantError, match="may not exceed 7 days"):
        safety_hardening._normalized_statistics_arguments(
            {
                "period": "5minute",
                "start_time": "2026-01-01T00:00:00+00:00",
                "end_time": "2026-01-09T00:00:00+00:00",
            }
        )


class _Delivery:
    def __init__(self, status: str, events: list[object] | None = None) -> None:
        self.status = status
        self.events = events

    def set(self, status: str, reason: str) -> None:
        self.status = status
        if self.events is not None:
            self.events.append((status, reason))


def test_expire_pending_broadcasts_preserves_only_in_flight_delivery() -> None:
    """Disabling Broadcast expires queued work but never rewrites terminal/in-flight work."""
    entity_id = "assist_satellite.kitchen"
    pending = _Delivery("pending")
    delivering = _Delivery("delivering")
    delivered = _Delivery("delivered")
    missing = SimpleNamespace(deliveries={})
    pending_item = SimpleNamespace(deliveries={entity_id: pending})
    delivering_item = SimpleNamespace(deliveries={entity_id: delivering})
    delivered_item = SimpleNamespace(deliveries={entity_id: delivered})
    manager = SimpleNamespace(
        _queues={
            entity_id: deque([missing, pending_item, delivering_item, delivered_item]),
            "assist_satellite.empty": deque(),
        }
    )

    safety_hardening._expire_pending_broadcasts(manager)

    assert pending.status == "expired"
    assert delivered.status == "delivered"
    assert list(manager._queues) == [entity_id]
    assert list(manager._queues[entity_id]) == [delivering_item]


@pytest.mark.asyncio
async def test_broadcast_state_transactions_load_once_and_persist_before_expiry(
    monkeypatch,
) -> None:
    """Broadcast publication is serialized and durable before live queue mutation."""
    from custom_components.extended_openai_conversation_responses.intercom import (
        IntercomManager,
    )

    async def placeholder(*args, **kwargs):
        return None

    monkeypatch.setattr(IntercomManager, "async_initialize", placeholder)
    monkeypatch.setattr(IntercomManager, "async_set_enabled", placeholder)
    safety_hardening._install_broadcast_state_transactions()
    initialize = IntercomManager.async_initialize
    set_enabled = IntercomManager.async_set_enabled

    load = AsyncMock(return_value={"enabled": True})
    events: list[object] = []

    async def save(value):
        events.append(("saved", value["enabled"]))

    entity_id = "assist_satellite.office"
    delivery = _Delivery("pending", events)
    item = SimpleNamespace(deliveries={entity_id: delivery})
    manager = SimpleNamespace(
        _loaded=False,
        _enabled=False,
        _store=SimpleNamespace(async_load=load, async_save=save),
        _queues={entity_id: deque([item])},
    )

    await initialize(manager)
    assert manager._enabled is True
    assert manager._loaded is True
    load.assert_awaited_once()

    await initialize(manager)
    load.assert_awaited_once()

    await set_enabled(manager, False)
    assert events == [
        ("saved", False),
        ("expired", "broadcast_disabled"),
    ]
    assert manager._enabled is False
    assert manager._queues == {}

    events.clear()
    await set_enabled(manager, True)
    assert events == [("saved", True)]
    assert manager._enabled is True

    safety_hardening._install_broadcast_state_transactions()
    assert IntercomManager.async_initialize is initialize
    assert IntercomManager.async_set_enabled is set_enabled
