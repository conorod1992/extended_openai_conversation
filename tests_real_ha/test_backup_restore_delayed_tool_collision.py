"""Real HA coverage for backup restore colliding with due delayed Function Tools."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses.agent_config import (
    configured_function_tools_from_data,
    normalize_agent_config,
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
from homeassistant.auth.models import Group
from homeassistant.auth.permissions.const import CAT_ENTITIES, POLICY_CONTROL, POLICY_READ
from homeassistant.auth.permissions.entities import ENTITY_ENTITY_IDS
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockUser
from tests_real_ha.test_provider_wire_e2e import _agent

_ENTITY_ID = "light.delayed_restore_collision"
_USER_ID = "delayed-restore-collision-user"
_DEVICE_ID = "delayed-restore-collision-device"
_TOOL_NAME = "execute_services"
_RESTORED_TITLE = "Restored After Delayed Action"


def _authorized_user() -> MockUser:
    """Return a genuine non-owner HA user allowed to control the test entity."""
    group = Group(
        id="delayed-restore-collision-group",
        name="Delayed restore collision",
        policy={
            CAT_ENTITIES: {
                ENTITY_ENTITY_IDS: {
                    _ENTITY_ID: {
                        POLICY_READ: True,
                        POLICY_CONTROL: True,
                    }
                }
            }
        },
    )
    return MockUser(
        id=_USER_ID,
        name="Delayed restore collision user",
        is_owner=False,
        groups=[group],
    )


async def _prepare_runtime(
    hass: HomeAssistant,
) -> tuple[Any, DelayedToolManager, MockUser, list[Any]]:
    """Load one real agent, scheduler, authenticated user, and observable service."""
    user = _authorized_user()
    user.add_to_hass(hass)

    agent = await _agent(hass, API_MODE_CHAT_COMPLETIONS)
    assert agent is not None
    assert getattr(
        backup.async_restore_backup, "_extended_openai_maintenance_gate", False
    )

    # The shared provider-wire fixture deliberately stores Function Tools as a Python
    # list because its tests never cross a persistence/reload boundary. This test does:
    # delayed execution reparses the *live persisted* subentry at due time. Normalize
    # through the production config contract first so the race starts from the same
    # YAML-backed storage shape a real management/configuration save would create.
    entry_id = agent.entry.entry_id
    normalized = normalize_agent_config(dict(agent.subentry.data))
    hass.config_entries.async_update_subentry(
        agent.entry,
        agent.subentry,
        data=normalized,
    )
    live_subentry = agent.entry.subentries[agent.subentry.subentry_id]
    assert isinstance(live_subentry.data[CONF_FUNCTION_TOOLS], str)
    assert await hass.config_entries.async_reload(entry_id)
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, entry_id)
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
        {"friendly_name": "Delayed restore collision light"},
    )
    async_expose_entity(hass, conversation.DOMAIN, _ENTITY_ID, True)
    await hass.async_block_till_done()
    return agent, manager, user, calls


async def _schedule_delayed_call(
    agent: Any,
    manager: DelayedToolManager,
    user: MockUser,
    call_id: str,
) -> str:
    """Persist a delayed native action through the production Function Tool seam."""
    configured = configured_function_tools_from_data(agent.subentry.data)
    function_tool = next(
        tool for tool in configured if tool["spec"]["name"] == _TOOL_NAME
    )
    before = set(manager._records)  # noqa: SLF001 - acceptance state boundary

    result = await agent._execute_function_tool(  # noqa: SLF001
        function_tool,
        llm.ToolInput(
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
        ),
        SimpleNamespace(
            context=Context(user_id=user.id),
            device_id=_DEVICE_ID,
        ),
        [],
    )

    assert result.tool_result == {"result": "Scheduled"}
    created = set(manager._records) - before  # noqa: SLF001
    assert len(created) == 1
    delayed_call_id = created.pop()
    record = manager._records[delayed_call_id]  # noqa: SLF001
    assert record.status == "pending"
    assert record.user_id == user.id
    assert record.device_id == _DEVICE_ID
    return delayed_call_id


async def _cancel_wall_clock_waiter(
    manager: DelayedToolManager, call_id: str
) -> None:
    """Remove only the 30-second timer while preserving its durable pending record."""
    task = manager._tasks.get(call_id)  # noqa: SLF001
    if task is not None and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    assert call_id not in manager._tasks  # noqa: SLF001
    assert manager._records[call_id].status == "pending"  # noqa: SLF001


async def _execute_due_under_production_lease(
    hass: HomeAssistant,
    manager: DelayedToolManager,
    call_id: str,
) -> None:
    """Bypass wall time only; retain the scheduler's real shared maintenance lease."""
    record = manager._records[call_id]  # noqa: SLF001
    gate = get_agent_maintenance_gate(hass, record.entry_id, record.subentry_id)
    async with gate.shared():
        retry = await manager._async_execute_due(call_id)  # noqa: SLF001
    assert retry is False


async def _wait_for_writer(gate: Any) -> None:
    """Yield until the restore task is genuinely queued behind an active reader."""
    for _ in range(100):
        if gate._waiting_writers:  # noqa: SLF001 - acceptance synchronization point
            return
        await asyncio.sleep(0)
    pytest.fail("backup restore never queued for the agent maintenance gate")


@pytest.mark.asyncio
async def test_restore_wins_collision_and_due_tool_uses_restored_configuration(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A due call blocked by restore must revalidate against restored configuration."""
    agent, manager, user, calls = await _prepare_runtime(hass)
    subentry = agent.subentry

    # Build a valid restore target from the genuinely loaded runtime, changing only
    # the delayed tool's authority. If execution used its scheduling-time snapshot,
    # it would still call the service after this restore; live revalidation must not.
    target_snapshot = await backup.async_collect_backup_snapshot(
        hass, agent.entry, subentry
    )
    restored_tools = target_snapshot["agent"]["config"][CONF_FUNCTION_TOOLS]
    restored_tool = next(
        tool for tool in restored_tools if tool["spec"]["name"] == _TOOL_NAME
    )
    restored_tool["enabled"] = False

    call_id = await _schedule_delayed_call(
        agent,
        manager,
        user,
        "call-restore-wins-collision",
    )
    await _cancel_wall_clock_waiter(manager, call_id)
    record = manager._records[call_id]  # noqa: SLF001
    gate = get_agent_maintenance_gate(hass, record.entry_id, record.subentry_id)

    restore_entered = asyncio.Event()
    release_restore = asyncio.Event()
    original_apply_restore = backup._apply_restore  # noqa: SLF001
    apply_count = 0

    async def blocking_apply_restore(managers: Any, prepared: Any) -> None:
        nonlocal apply_count
        apply_count += 1
        if apply_count == 1:
            # The guarded public restore cannot reach this point until it owns the
            # exclusive maintenance lease. Hold it here while the due call queues.
            assert gate._writer_active is True  # noqa: SLF001
            restore_entered.set()
            await release_restore.wait()
        await original_apply_restore(managers, prepared)

    monkeypatch.setattr(backup, "_apply_restore", blocking_apply_restore)

    restore_task = asyncio.create_task(
        backup.async_restore_backup(hass, agent.entry, subentry, target_snapshot)
    )
    await restore_entered.wait()
    assert calls == []

    due_task = asyncio.create_task(
        _execute_due_under_production_lease(hass, manager, call_id)
    )
    await asyncio.sleep(0)

    # The exclusive restore lease must keep the already-due action completely out;
    # neither service dispatch nor its durable execution boundary may begin early.
    assert not due_task.done()
    assert manager._records[call_id].status == "pending"  # noqa: SLF001
    assert calls == []

    release_restore.set()
    restored = await restore_task
    assert restored["status"] == "restored"
    await due_task
    await hass.async_block_till_done()

    assert calls == []
    assert call_id not in manager._records  # noqa: SLF001

    live_entry = hass.config_entries.async_get_entry(agent.entry.entry_id)
    assert live_entry is not None
    live_subentry = live_entry.subentries[subentry.subentry_id]
    live_tools = configured_function_tools_from_data(live_subentry.data)
    live_tool = next(tool for tool in live_tools if tool["spec"]["name"] == _TOOL_NAME)
    assert live_tool["enabled"] is False


@pytest.mark.asyncio
async def test_due_tool_wins_collision_and_restore_waits_for_exactly_once_completion(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore queued behind an executing delayed call must wait through finalization."""
    agent, manager, user, calls = await _prepare_runtime(hass)
    subentry = agent.subentry

    target_snapshot = await backup.async_collect_backup_snapshot(
        hass, agent.entry, subentry
    )
    original_title = subentry.title
    target_snapshot["agent"]["title"] = _RESTORED_TITLE

    call_id = await _schedule_delayed_call(
        agent,
        manager,
        user,
        "call-delayed-wins-collision",
    )
    await _cancel_wall_clock_waiter(manager, call_id)
    record = manager._records[call_id]  # noqa: SLF001
    gate = get_agent_maintenance_gate(hass, record.entry_id, record.subentry_id)

    delayed_entered = asyncio.Event()
    release_delayed = asyncio.Event()
    original_execute = agent._execute_function_tool  # noqa: SLF001

    async def blocking_delayed_execute(
        function_tool: dict[str, Any],
        tool_input: llm.ToolInput,
        llm_context: Any,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        if getattr(llm_context, "_extended_openai_delayed_execution", False):
            # _async_execute_due is called only inside the scheduler's shared lease.
            assert gate._active_readers > 0  # noqa: SLF001
            delayed_entered.set()
            await release_delayed.wait()
        return await original_execute(
            function_tool,
            tool_input,
            llm_context,
            exposed_entities,
        )

    monkeypatch.setattr(agent, "_execute_function_tool", blocking_delayed_execute)

    restore_apply_entered = asyncio.Event()
    original_apply_restore = backup._apply_restore  # noqa: SLF001

    async def observed_apply_restore(managers: Any, prepared: Any) -> None:
        restore_apply_entered.set()
        await original_apply_restore(managers, prepared)

    monkeypatch.setattr(backup, "_apply_restore", observed_apply_restore)

    due_task = asyncio.create_task(
        _execute_due_under_production_lease(hass, manager, call_id)
    )
    await delayed_entered.wait()
    assert manager._records[call_id].status == "executing"  # noqa: SLF001
    assert calls == []

    restore_task = asyncio.create_task(
        backup.async_restore_backup(hass, agent.entry, subentry, target_snapshot)
    )
    await _wait_for_writer(gate)

    # Restore is now definitely trying to acquire exclusivity, but cannot enter the
    # mutable restore body while the delayed side effect/finalization lease is live.
    assert not restore_task.done()
    assert not restore_apply_entered.is_set()
    live_entry = hass.config_entries.async_get_entry(agent.entry.entry_id)
    assert live_entry is not None
    assert live_entry.subentries[subentry.subentry_id].title == original_title

    release_delayed.set()
    await due_task
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == [_ENTITY_ID]
    assert calls[0].context.user_id == user.id
    assert call_id not in manager._records  # noqa: SLF001

    restored = await restore_task
    assert restored["status"] == "restored"
    assert restore_apply_entered.is_set()
    await hass.async_block_till_done()

    # The restore may refresh/reload the live entity, but it cannot retroactively
    # replay the delayed record or duplicate the already-completed external action.
    assert len(calls) == 1
    assert manager._records == {}  # noqa: SLF001
    live_entry = hass.config_entries.async_get_entry(agent.entry.entry_id)
    assert live_entry is not None
    assert live_entry.subentries[subentry.subentry_id].title == _RESTORED_TITLE
