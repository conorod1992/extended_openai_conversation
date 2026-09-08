"""Regression coverage for delayed Function Tool caller permission recovery."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses import delayed_tools
from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DelayedToolCall,
    DelayedToolManager,
)
from custom_components.extended_openai_conversation_responses.ha_permissions import (
    async_require_control_permission,
    get_active_ha_context,
    set_active_ha_context,
)


def _recovered_record() -> DelayedToolCall:
    """Return a persisted-style call whose original request task no longer exists."""
    now = dt_util.utcnow().isoformat()
    return DelayedToolCall(
        call_id="recovered-call",
        entry_id="entry",
        subentry_id="agent",
        tool_name="control_light",
        arguments={"entity_id": "light.secret"},
        due_at=now,
        created_at=now,
        user_id="restricted-user",
        device_id="voice-device",
    )


async def test_recovered_due_call_rebinds_originating_user_permissions(
    hass, monkeypatch
) -> None:
    """Restart recovery must not execute a delayed call with system permissions."""
    manager = DelayedToolManager(hass)
    record = _recovered_record()
    manager._records = {record.call_id: record}
    manager._store = SimpleNamespace(async_save=AsyncMock())

    latest_subentry = SimpleNamespace(subentry_type="conversation", data={})
    latest_entry = SimpleNamespace(
        disabled_by=None, subentries={"agent": latest_subentry}
    )
    hass.config_entries.async_get_entry = MagicMock(return_value=latest_entry)

    restricted_user = MagicMock(is_active=True, is_admin=False)
    restricted_user.permissions.check_entity.return_value = False
    hass.auth.async_get_user = AsyncMock(return_value=restricted_user)

    current_tool = {
        "enabled": True,
        "spec": {"name": "control_light"},
        "function": {"type": "native", "name": "execute_service_single"},
    }
    monkeypatch.setattr(
        delayed_tools,
        "configured_function_tools_from_data",
        lambda _data: [current_tool],
    )

    all_exposed = [
        {"entity_id": "light.allowed"},
        {"entity_id": "light.secret"},
    ]

    def exposed_for_current_caller(_hass):
        context = get_active_ha_context()
        if context is not None and context.user_id == record.user_id:
            return [all_exposed[0]]
        return all_exposed

    monkeypatch.setattr(delayed_tools, "get_exposed_entities", exposed_for_current_caller)

    side_effect = AsyncMock()
    observed_exposure: list[dict[str, str]] = []
    permission_denied = False

    async def execute_function_tool(
        _tool, _tool_input: llm.ToolInput, _llm_context, exposed_entities
    ):
        nonlocal permission_denied
        observed_exposure.extend(exposed_entities)
        try:
            await async_require_control_permission(hass, ["light.secret"])
        except HomeAssistantError:
            permission_denied = True
            raise
        await side_effect()

    agent = SimpleNamespace(_execute_function_tool=execute_function_tool)
    monkeypatch.setattr(manager, "_resolve_agent", lambda *_args: agent)

    # Model the post-restart scheduler task: it has no ContextVar inherited from
    # the request that originally scheduled the durable call.
    set_active_ha_context(None)
    try:
        retry = await manager._async_execute_due(record.call_id)
        assert get_active_ha_context() is None
    finally:
        set_active_ha_context(None)

    assert retry is False
    assert observed_exposure == [{"entity_id": "light.allowed"}]
    assert permission_denied is True
    side_effect.assert_not_awaited()
    restricted_user.permissions.check_entity.assert_called_with(
        "light.secret", POLICY_CONTROL
    )
    assert record.call_id not in manager._records
