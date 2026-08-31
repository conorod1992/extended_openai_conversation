"""Final regression coverage for authorization, Recorder, and Broadcast guards."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses import delayed_tools, intercom
from custom_components.extended_openai_conversation_responses.built_in_functions import (
    built_in_function_catalog,
)
from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DelayedToolCall,
    DelayedToolManager,
)
from custom_components.extended_openai_conversation_responses.functions import NativeFunction
from custom_components.extended_openai_conversation_responses.ha_permissions import (
    get_active_ha_context,
    set_active_ha_context,
)
from custom_components.extended_openai_conversation_responses.safety_hardening import (
    MAX_HISTORY_ENTITY_IDS,
    MAX_HISTORY_SPAN,
    MAX_STATISTIC_SPAN_BY_PERIOD,
    _normalized_statistics_arguments,
    _validate_history_request,
    install_safety_hardening,
)


def _delayed_record() -> DelayedToolCall:
    now = dt_util.utcnow()
    return DelayedToolCall(
        call_id="call-1",
        entry_id="entry",
        subentry_id="agent",
        tool_name="read_history",
        arguments={"entity_ids": ["sensor.allowed"]},
        due_at=(now - timedelta(seconds=1)).isoformat(),
        created_at=(now - timedelta(seconds=2)).isoformat(),
        user_id="restricted-user",
        device_id="voice-device",
    )


async def test_recovered_delayed_tool_binds_persisted_user_for_complete_execution(
    hass, monkeypatch
) -> None:
    """Restart-recovered calls use their persisted user for exposure and execution."""
    install_safety_hardening()
    manager = DelayedToolManager(hass)
    record = _delayed_record()
    manager._records = {record.call_id: record}
    manager._store = SimpleNamespace(async_save=AsyncMock())
    hass.config_entries.async_get_entry = MagicMock(
        return_value=SimpleNamespace(
            disabled_by=None,
            subentries={
                "agent": SimpleNamespace(subentry_type="conversation", data={})
            },
        )
    )
    hass.auth.async_get_user = AsyncMock(return_value=SimpleNamespace(is_active=True))
    current_tool = {
        "enabled": True,
        "spec": {"name": record.tool_name},
        "function": {"type": "native", "name": "get_history"},
    }
    monkeypatch.setattr(
        delayed_tools,
        "configured_function_tools_from_data",
        lambda _data: [current_tool],
    )

    def exposed(_hass):
        context = get_active_ha_context()
        assert context is not None
        assert context.user_id == record.user_id
        return [{"entity_id": "sensor.allowed"}]

    monkeypatch.setattr(delayed_tools, "get_exposed_entities", exposed)

    async def execute(*args):
        context = get_active_ha_context()
        assert context is not None
        assert context.user_id == record.user_id
        return object()

    agent = SimpleNamespace(_execute_function_tool=AsyncMock(side_effect=execute))
    monkeypatch.setattr(manager, "_resolve_agent", lambda *_args: agent)

    outer = Context(user_id="outer-user")
    set_active_ha_context(outer)
    try:
        assert await manager._async_execute_due(record.call_id) is False
        assert get_active_ha_context() is outer
    finally:
        set_active_ha_context(None)

    agent._execute_function_tool.assert_awaited_once()


async def test_add_automation_requires_active_admin(hass, tmp_path, monkeypatch) -> None:
    """Persistent automation creation is unavailable to anonymous/restricted users."""
    install_safety_hardening()
    function = NativeFunction()
    config = {
        "type": "native",
        "name": "add_automation",
    }
    arguments = {
        "automation_config": "alias: Test\ntrigger: []\naction: []\n"
    }

    hass.auth.async_get_user = AsyncMock(
        return_value=SimpleNamespace(is_active=True, is_admin=False)
    )
    with pytest.raises(HomeAssistantError, match="administrator"):
        await function.add_automation(
            hass,
            config,
            arguments,
            SimpleNamespace(context=Context(user_id="restricted")),
            [],
        )

    with pytest.raises(HomeAssistantError, match="authenticated"):
        await function.add_automation(hass, config, arguments, None, [])

    automation_path = tmp_path / "automations.yaml"
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.functions.native.AUTOMATION_CONFIG_PATH",
        str(automation_path),
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.functions.native.automation.config._async_validate_config_item",
        AsyncMock(return_value=None),
    )
    hass.auth.async_get_user = AsyncMock(
        return_value=SimpleNamespace(is_active=True, is_admin=True)
    )
    hass.services.async_call = AsyncMock(return_value=None)

    assert (
        await function.add_automation(
            hass,
            config,
            arguments,
            SimpleNamespace(context=Context(user_id="admin")),
            [],
        )
        == "Success"
    )
    assert automation_path.exists()


def test_recorder_guards_bound_history_and_statistics_before_query() -> None:
    """Invalid cardinality and expensive/inverted windows fail before Recorder I/O."""
    now = dt_util.utcnow()
    too_many = [f"sensor.item_{index}" for index in range(MAX_HISTORY_ENTITY_IDS + 1)]
    with pytest.raises(HomeAssistantError, match="at most"):
        _validate_history_request({"entity_ids": too_many})

    with pytest.raises(HomeAssistantError, match="after start_time"):
        _validate_history_request(
            {
                "entity_ids": ["sensor.one"],
                "start_time": now.isoformat(),
                "end_time": (now - timedelta(minutes=1)).isoformat(),
            }
        )

    with pytest.raises(HomeAssistantError, match="may not exceed"):
        _validate_history_request(
            {
                "entity_ids": ["sensor.one"],
                "start_time": now.isoformat(),
                "end_time": (now + MAX_HISTORY_SPAN + timedelta(seconds=1)).isoformat(),
            }
        )

    for period, maximum in MAX_STATISTIC_SPAN_BY_PERIOD.items():
        with pytest.raises(HomeAssistantError, match="may not exceed"):
            _normalized_statistics_arguments(
                {
                    "start_time": now.isoformat(),
                    "end_time": (now + maximum + timedelta(seconds=1)).isoformat(),
                    "period": period,
                }
            )

    with pytest.raises(HomeAssistantError, match="after start_time"):
        _normalized_statistics_arguments(
            {
                "start_time": now.isoformat(),
                "end_time": now.isoformat(),
                "period": "day",
            }
        )


def test_builtin_history_schema_advertises_runtime_cardinality_bound() -> None:
    """The model sees the same non-empty/history ID cap enforced at runtime."""
    install_safety_hardening()
    history = next(
        item for item in built_in_function_catalog() if item["implementation"] == "get_history"
    )
    schema = history["tool"]["spec"]["parameters"]["properties"]["entity_ids"]
    assert schema["minItems"] == 1
    assert schema["maxItems"] == MAX_HISTORY_ENTITY_IDS


async def test_broadcast_state_is_serialized_and_save_failure_is_transactional(hass) -> None:
    """Concurrent loads occur once and failed disables do not mutate live queues."""
    install_safety_hardening()
    manager = intercom.IntercomManager(hass)
    manager._store.async_load = AsyncMock(return_value={"enabled": True})

    await asyncio.gather(manager.async_initialize(), manager.async_initialize())

    assert manager.enabled is True
    manager._store.async_load.assert_awaited_once()

    item = intercom.BroadcastMessage(
        id="queued",
        message="Test",
        created_at=dt_util.utcnow().isoformat(),
        expires_at=intercom.datetime.now(intercom.UTC) + intercom.timedelta(seconds=30),
        source="test",
        origin_entity_id=None,
        origin_device_id=None,
        targets=["assist_satellite.kitchen"],
        deliveries={
            "assist_satellite.kitchen": intercom.Delivery(
                "assist_satellite.kitchen", "queued_busy"
            )
        },
    )
    manager._queues["assist_satellite.kitchen"] = deque([item])
    manager._store.async_save = AsyncMock(side_effect=OSError("storage unavailable"))

    with pytest.raises(OSError, match="storage unavailable"):
        await manager.async_set_enabled(False)

    assert manager.enabled is True
    assert list(manager._queues["assist_satellite.kitchen"]) == [item]
    assert item.deliveries["assist_satellite.kitchen"].status == "queued_busy"
