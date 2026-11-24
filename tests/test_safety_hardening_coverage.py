"""Focused behavioral coverage for model-facing safety hardening."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from custom_components.extended_openai_conversation_responses import safety_hardening
from custom_components.extended_openai_conversation_responses.ha_permissions import (
    get_active_ha_context,
    set_active_ha_context,
)
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util


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
    hass = SimpleNamespace(
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user))
    )
    llm_context = SimpleNamespace(context=Context(user_id="user-1"))

    with pytest.raises(HomeAssistantError, match="active Home Assistant administrator"):
        await safety_hardening._async_require_admin(hass, llm_context)

    hass.auth.async_get_user.assert_awaited_once_with("user-1")


@pytest.mark.asyncio
async def test_require_admin_accepts_active_admin_from_active_context() -> None:
    """The active HA context is a valid fallback when the LLM context has none."""
    admin = SimpleNamespace(is_active=True, is_admin=True)
    hass = SimpleNamespace(
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=admin))
    )
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
    with pytest.raises(
        HomeAssistantError, match="start_time must be an ISO 8601 datetime"
    ):
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


def test_statistics_validation_defaults_period_and_rejects_bad_period_or_window() -> (
    None
):
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


@pytest.mark.parametrize(
    ("period", "first_invalid_end"),
    [
        ("day", "9999-12-31T00:00:00+00:00"),
        ("week", "9999-12-27T00:00:00+00:00"),
        ("month", "9999-12-01T00:00:00+00:00"),
        ("year", "9999-01-01T00:00:00+00:00"),
    ],
)
def test_statistics_rejects_time_windows_whose_local_period_overflows(
    monkeypatch, period, first_invalid_end
) -> None:
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", UTC)
    end = datetime.fromisoformat(first_invalid_end)
    safe_end = end - timedelta(microseconds=1)
    arguments = {
        "period": period,
        "start_time": (end - timedelta(hours=1)).isoformat(),
        "end_time": first_invalid_end,
    }

    with pytest.raises(HomeAssistantError, match=f"outside.*{period} statistics"):
        safety_hardening._normalized_statistics_arguments(arguments)
    arguments["end_time"] = safe_end.isoformat()
    assert (
        safety_hardening._normalized_statistics_arguments(arguments)["end_time"]
        == safe_end
    )


def test_statistics_edges_use_home_assistant_time_zone_for_calendar_periods(
    monkeypatch,
) -> None:
    with (
        monkeypatch.context() as patch,
        pytest.raises(HomeAssistantError, match=r"outside.*year statistics"),
    ):
        patch.setattr(dt_util, "DEFAULT_TIME_ZONE", ZoneInfo("Pacific/Kiritimati"))
        safety_hardening._normalized_statistics_arguments(
            {
                "period": "year",
                "start_time": "9998-12-31T20:00:00Z",
                "end_time": "9998-12-31T23:00:00Z",
            }
        )

    arguments = {
        "start_time": "0001-01-01T00:00:00Z",
        "end_time": "0001-01-01T13:00:00Z",
    }
    with monkeypatch.context() as patch:
        patch.setattr(dt_util, "DEFAULT_TIME_ZONE", ZoneInfo("Etc/GMT+12"))
        with pytest.raises(HomeAssistantError, match="Home Assistant's timezone"):
            safety_hardening._normalized_statistics_arguments(arguments)
        arguments["period"] = "hour"
        assert safety_hardening._normalized_statistics_arguments(arguments)[
            "start_time"
        ] == datetime.min.replace(tzinfo=UTC)


class _Delivery:
    def __init__(self, status: str, events: list[object] | None = None) -> None:
        self.status = status
        self.events = events

    def set(self, status: str, reason: str) -> None:
        self.status = status
        if self.events is not None:
            self.events.append((status, reason))


async def test_expire_pending_broadcasts_preserves_only_in_flight_delivery(
    hass,
) -> None:
    """Disabling Broadcast expires queued work but never rewrites terminal/in-flight work."""
    entity_id = "assist_satellite.kitchen"
    pending = _Delivery("pending")
    delivering = _Delivery("delivering")
    delivered = _Delivery("delivered")
    missing = SimpleNamespace(deliveries={})
    pending_item = SimpleNamespace(id="pending", deliveries={entity_id: pending})
    delivering_item = SimpleNamespace(
        id="delivering", deliveries={entity_id: delivering}
    )
    delivered_item = SimpleNamespace(id="delivered", deliveries={entity_id: delivered})
    from custom_components.extended_openai_conversation_responses.intercom import (
        IntercomManager,
    )

    manager = IntercomManager(hass)
    manager._queues = {
        entity_id: deque([missing, pending_item, delivering_item, delivered_item]),
        "assist_satellite.empty": deque(),
    }
    manager._store = SimpleNamespace(async_save=AsyncMock())
    await IntercomManager.async_set_enabled(manager, False)

    assert pending.status == "expired"
    assert delivered.status == "delivered"
    assert list(manager._queues) == [entity_id]
    assert list(manager._queues[entity_id]) == [delivering_item]


@pytest.mark.asyncio
async def test_broadcast_state_transactions_load_once_and_persist_before_expiry(
    monkeypatch,
    hass,
) -> None:
    """Broadcast publication is serialized and durable before live queue mutation."""
    from custom_components.extended_openai_conversation_responses.intercom import (
        IntercomManager,
    )

    async def placeholder(*args, **kwargs):
        return None

    initialize = IntercomManager.async_initialize
    set_enabled = IntercomManager.async_set_enabled

    load = AsyncMock(return_value={"enabled": True})
    events: list[object] = []

    async def save(value):
        events.append(("saved", value["enabled"]))

    entity_id = "assist_satellite.office"
    delivery = _Delivery("pending", events)
    item = SimpleNamespace(id="pending", deliveries={entity_id: delivery})
    manager = IntercomManager(hass)
    manager._store = SimpleNamespace(async_load=load, async_save=save)
    manager._queues = {entity_id: deque([item])}

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

    assert IntercomManager.async_initialize is initialize
    assert IntercomManager.async_set_enabled is set_enabled
