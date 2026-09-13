"""Residual branch coverage for Guest Mode persistence and policy helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import guest_mode


def _manager(hass, *, loaded=None) -> guest_mode.GuestModeManager:
    manager = guest_mode.GuestModeManager(hass, "entry", "agent")
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value=loaded),
        async_save=AsyncMock(),
    )
    return manager


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


async def test_initialize_restores_valid_schedule_once(hass) -> None:
    loaded = {
        "schedule": {
            "active_from": "2026-09-13T10:00:00+00:00",
            "active_until": "2026-09-13T12:00:00+00:00",
            "source": "home_assistant",
            "updated_at": "2026-09-13T09:00:00+00:00",
        }
    }
    manager = _manager(hass, loaded=loaded)

    await manager.async_initialize()
    await manager.async_initialize()

    assert manager.schedule is not None
    assert manager.schedule.source == "home_assistant"
    manager._store.async_load.assert_awaited_once()


@pytest.mark.parametrize(
    "loaded",
    [
        {"schedule": {"active_from": "not-a-date"}},
        {"schedule": {"active_from": "2026-09-13T10:00:00+00:00", "extra": True}},
    ],
)
async def test_initialize_ignores_malformed_persisted_state(hass, loaded) -> None:
    manager = _manager(hass, loaded=loaded)
    await manager.async_initialize()
    assert manager.schedule is None
    assert manager._initialized is True


async def test_restrict_and_trusted_update_reject_reverse_intervals(hass) -> None:
    manager = _manager(hass)
    manager._initialized = True
    now = _utc("2026-09-13T09:00:00")

    with pytest.raises(ValueError, match="later than active_from"):
        await manager.async_restrict(
            active_from="2026-09-13T12:00:00+00:00",
            active_until="2026-09-13T11:00:00+00:00",
            now=now,
        )

    with pytest.raises(ValueError, match="later than active_from"):
        await manager.async_update_trusted(
            active_from="2026-09-13T12:00:00+00:00",
            active_until="2026-09-13T12:00:00+00:00",
            now=now,
        )


@pytest.mark.parametrize(
    "value,message",
    [
        ({}, "backup is invalid"),
        ({"schedule": []}, "schedule backup is invalid"),
        (
            {
                "schedule": {
                    "active_from": 123,
                    "active_until": None,
                    "source": "test",
                    "updated_at": "2026-09-13T09:00:00+00:00",
                }
            },
            "active_from is invalid",
        ),
        (
            {
                "schedule": {
                    "active_from": "bad",
                    "active_until": None,
                    "source": "test",
                    "updated_at": "2026-09-13T09:00:00+00:00",
                }
            },
            "active_from is invalid",
        ),
        (
            {
                "schedule": {
                    "active_from": "2026-09-13T10:00:00+00:00",
                    "active_until": None,
                    "source": "",
                    "updated_at": "2026-09-13T09:00:00+00:00",
                }
            },
            "source is invalid",
        ),
    ],
)
def test_backup_validation_rejects_malformed_payloads(value, message) -> None:
    with pytest.raises(ValueError, match=message):
        guest_mode.GuestModeManager.validate_backup_data(value)


def test_backup_validation_accepts_none_and_optional_end() -> None:
    assert guest_mode.GuestModeManager.validate_backup_data({"schedule": None}) is None
    schedule = guest_mode.GuestModeManager.validate_backup_data(
        {
            "schedule": {
                "active_from": "2026-09-13T10:00:00+00:00",
                "active_until": None,
                "source": "test",
                "updated_at": "2026-09-13T09:00:00+00:00",
            }
        }
    )
    assert schedule is not None
    assert schedule.active_until is None


async def test_listener_can_be_removed_and_schedule_expiry_is_not_live(hass) -> None:
    manager = _manager(hass)
    manager._initialized = True
    calls: list[str] = []
    remove = manager.async_add_listener(lambda: calls.append("changed"))
    manager._notify()
    remove()
    manager._notify()
    assert calls == ["changed"]

    manager._schedule = guest_mode.GuestModeSchedule(
        active_from="2026-09-13T08:00:00+00:00",
        active_until="2026-09-13T09:00:00+00:00",
        source="test",
        updated_at="2026-09-13T08:00:00+00:00",
    )
    assert manager._live_or_future_schedule(_utc("2026-09-13T10:00:00")) is None


async def test_shared_manager_is_reused_and_loaded_lookup_matches(hass, monkeypatch) -> None:
    init = AsyncMock()
    monkeypatch.setattr(guest_mode.GuestModeManager, "async_initialize", init)

    assert guest_mode.get_loaded_guest_mode(hass, "entry", "agent") is None
    first = await guest_mode.async_get_guest_mode(hass, "entry", "agent")
    second = await guest_mode.async_get_guest_mode(hass, "entry", "agent")

    assert first is second
    assert guest_mode.get_loaded_guest_mode(hass, "entry", "agent") is first
    assert init.await_count == 2


def test_custom_function_policy_expands_selected_groups_and_filters_unknowns(
    hass, monkeypatch
) -> None:
    monkeypatch.setattr(
        guest_mode, "get_exposed_entities", lambda _hass: [{"entity_id": "light.guest"}]
    )
    monkeypatch.setattr(
        guest_mode,
        "resolve_guest_selector_entity_ids",
        lambda *_args, **_kwargs: set(),
    )
    monkeypatch.setattr(
        guest_mode, "classify_tool", lambda _tool: guest_mode.FunctionSecurity.CONTROL
    )
    manager = SimpleNamespace(is_active=lambda: True)
    tools = [
        {"spec": {"name": "direct"}},
        {"spec": {"name": "from_group"}},
    ]
    options = {
        guest_mode.CONF_GUEST_POLICY_VERSION: guest_mode.GUEST_POLICY_VERSION,
        guest_mode.CONF_GUEST_FUNCTION_POLICY: "custom",
        guest_mode.CONF_GUEST_ALLOWED_FUNCTION_NAMES: ["direct", "missing"],
        guest_mode.CONF_GUEST_ALLOWED_GROUP_IDS: ["safe"],
        guest_mode.CONF_FUNCTION_GROUPS: [
            {"id": "safe", "functions": ["from_group", 123]},
            {"id": "other", "functions": ["ignored"]},
        ],
    }

    policy = guest_mode.resolve_guest_policy(hass, options, manager, tools)
    assert policy.configured_tool_names == frozenset({"direct", "from_group"})


def test_legacy_editor_snapshot_translates_without_broadening(hass, monkeypatch) -> None:
    monkeypatch.setattr(
        guest_mode,
        "get_exposed_entities",
        lambda _hass: [
            {"entity_id": "light.one"},
            {"entity_id": "switch.two"},
            {"bad": "ignored"},
        ],
    )
    monkeypatch.setattr(
        guest_mode,
        "_resolve_legacy_entity_ids",
        lambda _hass, _options, *, control: (
            {"light.one"} if control else {"light.one", "switch.two"}
        ),
    )
    monkeypatch.setattr(
        guest_mode,
        "_resolve_legacy_policy",
        lambda *_args: guest_mode.GuestCapabilityPolicy(
            True, configured_tool_names=frozenset({"safe_tool"})
        ),
    )

    snapshot = guest_mode.guest_policy_editor_snapshot(
        hass,
        {
            guest_mode.CONF_GUEST_SHARED_MEMORY_READ: False,
            guest_mode.CONF_GUEST_SHARED_MEMORY_WRITE: True,
            guest_mode.CONF_GUEST_KNOWLEDGE_ENABLED: True,
        },
        (),
    )

    assert snapshot[guest_mode.CONF_GUEST_EXCLUDED_ENTITIES] == []
    assert snapshot[guest_mode.CONF_GUEST_CONTROL_EXCLUDED_ENTITIES] == ["switch.two"]
    assert snapshot[guest_mode.CONF_GUEST_FUNCTION_POLICY] == "custom"
    assert snapshot[guest_mode.CONF_GUEST_ALLOWED_FUNCTION_NAMES] == ["safe_tool"]
    assert snapshot[guest_mode.CONF_GUEST_SHARED_MEMORY_POLICY] == "off"


def test_string_set_and_timestamp_guards(hass, monkeypatch) -> None:
    assert guest_mode._string_set("light.one") == set()
    assert guest_mode._string_set(["light.one", "", 3, "light.two"]) == {
        "light.one",
        "light.two",
    }

    hass.config.time_zone = "UTC"

    with pytest.raises(ValueError, match="ISO 8601"):
        guest_mode._parse_timestamp(hass, "not-a-date", "active_from")

    parsed = guest_mode._parse_timestamp(hass, "2026-09-13T10:00:00", "active_from")
    assert parsed.tzinfo is not None

    monkeypatch.setattr(guest_mode.dt_util, "get_time_zone", lambda _zone: None)
    with pytest.raises(ValueError, match="timezone is unavailable"):
        guest_mode._parse_timestamp(hass, "2026-09-13T10:00:00", "active_from")

    with pytest.raises(ValueError, match="must include timezone"):
        guest_mode._as_utc(datetime(2026, 9, 13, 10, 0, 0))
