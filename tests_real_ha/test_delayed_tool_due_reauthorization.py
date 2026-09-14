"""Real HA acceptance coverage for delayed Function Tool due-time authorization."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace
from typing import Any

from homeassistant.auth.models import Group
from homeassistant.auth.permissions.const import CAT_ENTITIES, POLICY_CONTROL, POLICY_READ
from homeassistant.auth.permissions.entities import ENTITY_ENTITY_IDS
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.agent_config import (
    configured_function_tools_from_data,
    validate_function_tools,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_FUNCTION_TOOLS,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DATA_DELAYED_TOOL_MANAGER,
    DelayedToolManager,
)
from tests_real_ha.test_provider_wire_e2e import _agent

_ENTITY_ID = "light.delayed_due_reauthorization"
_USER_ID = "delayed-due-reauthorization-user"
_DEVICE_ID = "delayed-due-reauthorization-device"
_TOOL_NAME = "execute_services"


def _permission_group(*, control: bool) -> Group:
    entity_policy = {POLICY_READ: True}
    if control:
        entity_policy[POLICY_CONTROL] = True
    return Group(
        id="delayed-due-reauthorization-group",
        name="Delayed due reauthorization",
        policy={CAT_ENTITIES: {ENTITY_ENTITY_IDS: {_ENTITY_ID: entity_policy}}},
    )


def _restricted_user() -> MockUser:
    return MockUser(
        id=_USER_ID,
        name="Delayed due reauthorization user",
        is_owner=False,
        groups=[_permission_group(control=True)],
    )


def _set_control_permission(user: MockUser, *, allowed: bool) -> None:
    """Change the live HA permission policy through HA's cache-invalidating setter."""
    # User.groups has Home Assistant's permissions-change on_setattr hook. Replacing
    # the list therefore invalidates the cached PolicyPermissions object; mutating the
    # existing list in place bypasses that hook and leaves stale permissions active.
    user.groups = [_permission_group(control=allowed)]


async def _schedule_delayed_call(
    agent: Any,
    manager: DelayedToolManager,
    user: MockUser,
    call_id: str,
) -> str:
    """Schedule through the integration's effective configured-tool seam."""
    raw_tools = agent.subentry.data.get(CONF_FUNCTION_TOOLS)
    configured = (
        validate_function_tools(raw_tools)
        if isinstance(raw_tools, list)
        else configured_function_tools_from_data(agent.subentry.data)
    )
    function_tool = next(
        tool for tool in configured if tool["spec"]["name"] == _TOOL_NAME
    )
    before = set(manager._records)  # noqa: SLF001 - assert durable scheduler state
    tool_input = llm.ToolInput(
        id=call_id,
        tool_name=_TOOL_NAME,
        tool_args={
            "delay": {"seconds": 30},
            "list": [
                {
                    "domain": "light",
                    "service": "turn_off",
                    "service_data": {"entity_id": [_ENTITY_ID]},
                }
            ],
        },
        external=True,
    )
    llm_context = SimpleNamespace(
        context=Context(user_id=user.id),
        device_id=_DEVICE_ID,
    )

    result = await agent._execute_function_tool(  # noqa: SLF001
        function_tool,
        tool_input,
        llm_context,
        [],
    )

    assert result.tool_result == {"result": "Scheduled"}
    created = set(manager._records) - before  # noqa: SLF001
    assert len(created) == 1
    delayed_call_id = created.pop()
    record = manager._records[delayed_call_id]  # noqa: SLF001
    assert record.tool_name == _TOOL_NAME
    assert record.user_id == user.id
    assert record.device_id == _DEVICE_ID
    assert record.status == "pending"
    return delayed_call_id


async def _execute_now(
    hass: HomeAssistant, manager: DelayedToolManager, call_id: str
) -> None:
    """Bypass only wall-clock waiting; retain the production due-time lease/seam."""
    task = manager._tasks.get(call_id)  # noqa: SLF001
    if task is not None and not task.done():
        # Give the production waiter one event-loop turn so cancellation is handled
        # inside _async_wait_and_execute(). Cancelling a task before its coroutine has
        # ever started can skip that coroutine's finally block, which owns _tasks
        # cleanup, and would make this wall-clock shortcut manufacture stale state.
        await asyncio.sleep(0)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    assert call_id not in manager._tasks  # noqa: SLF001

    record = manager._records[call_id]  # noqa: SLF001
    gate = get_agent_maintenance_gate(hass, record.entry_id, record.subentry_id)
    async with gate.shared():
        retry = await manager._async_execute_due(call_id)  # noqa: SLF001

    assert retry is False
    assert call_id not in manager._records  # noqa: SLF001


async def test_delayed_native_tool_reauthorizes_live_user_when_due(
    hass: HomeAssistant,
) -> None:
    """Scheduling authorization must not survive later HA permission/user changes."""
    user = _restricted_user()
    user.add_to_hass(hass)
    agent = await _agent(hass, API_MODE_CHAT_COMPLETIONS)
    assert agent is not None

    manager = hass.data[DOMAIN][DATA_DELAYED_TOOL_MANAGER]
    assert isinstance(manager, DelayedToolManager)

    calls: list[Any] = []

    async def turn_off(call: Any) -> None:
        calls.append(call)

    hass.services.async_register("light", "turn_off", turn_off)
    hass.states.async_set(
        _ENTITY_ID,
        "on",
        {"friendly_name": "Delayed due reauthorization light"},
    )
    async_expose_entity(hass, conversation.DOMAIN, _ENTITY_ID, True)

    assert user.permissions.check_entity(_ENTITY_ID, POLICY_READ)
    assert user.permissions.check_entity(_ENTITY_ID, POLICY_CONTROL)

    permission_call = await _schedule_delayed_call(
        agent, manager, user, "call-delayed-permission-revoked"
    )
    assert calls == []

    _set_control_permission(user, allowed=False)
    assert user.permissions.check_entity(_ENTITY_ID, POLICY_READ)
    assert not user.permissions.check_entity(_ENTITY_ID, POLICY_CONTROL)

    await _execute_now(hass, manager, permission_call)
    assert calls == []

    _set_control_permission(user, allowed=True)
    inactive_call = await _schedule_delayed_call(
        agent, manager, user, "call-delayed-user-deactivated"
    )
    await hass.auth.async_update_user(user, is_active=False)
    await hass.async_block_till_done()
    assert user.is_active is False

    await _execute_now(hass, manager, inactive_call)
    assert calls == []

    await hass.auth.async_update_user(user, is_active=True)
    await hass.async_block_till_done()
    assert user.is_active is True
    assert user.permissions.check_entity(_ENTITY_ID, POLICY_CONTROL)

    recovery_call = await _schedule_delayed_call(
        agent, manager, user, "call-delayed-reauthorization-recovered"
    )
    await _execute_now(hass, manager, recovery_call)

    assert len(calls) == 1
    assert calls[0].data["entity_id"] == [_ENTITY_ID]
    assert calls[0].context.user_id == user.id
    assert manager._records == {}  # noqa: SLF001
