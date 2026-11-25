"""Scheduled Actions share one authorization, storage and effect boundary."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import voluptuous as vol
import yaml

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DATA_DELAYED_TOOL_MANAGER,
    MAX_DELAYED_CALLS_PER_AGENT,
    DelayedToolManager,
    ScheduledActionOwner,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    EffectOutcomeUnknown,
)
from custom_components.extended_openai_conversation_responses.execution_outcomes import (
    execution_attempt,
)
from custom_components.extended_openai_conversation_responses.function_tool_policy import (
    is_integration_owned_read_only_tool,
)
from custom_components.extended_openai_conversation_responses.function_tool_quarantine import (
    _runtime_configured_function_tools,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    LiveTool,
    ToolSnapshot,
    reference_key,
)
from custom_components.extended_openai_conversation_responses.local_intents import (
    CONF_LOCAL_INTENT_DELAYED_COMMANDS_TO_AI,
    should_handle_locally,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from custom_components.extended_openai_conversation_responses.mutation_outcomes import (
    mutation_failure,
)
from custom_components.extended_openai_conversation_responses.request import (
    assemble_integration_function_tools,
    format_function_tools,
)
from custom_components.extended_openai_conversation_responses.request_origin import (
    bind_request_satellite,
)
from custom_components.extended_openai_conversation_responses.scheduled_action_tools import (
    async_execute_scheduled_action_tool,
    conversation_scheduled_action_owner,
    fit_scheduled_action_tools,
    scheduled_action_tools,
)
from homeassistant.core import Context, CoreState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent, llm
from homeassistant.util import dt as dt_util
from tests.test_delayed_tools import _valid_tool


@pytest.fixture
def agent(hass, monkeypatch):
    subentry = SimpleNamespace(
        subentry_id="agent", subentry_type="conversation", data={}
    )
    entry = SimpleNamespace(
        entry_id="provider", domain=DOMAIN, subentries={"agent": subentry}
    )
    monkeypatch.setattr(
        hass.config_entries,
        "async_get_entry",
        lambda key: entry if key == entry.entry_id else None,
    )
    monkeypatch.setattr(hass.config_entries, "async_entries", lambda *_args: [entry])
    return SimpleNamespace(
        hass=hass,
        entry=entry,
        subentry=subentry,
        _effective_guest_policy=Mock(return_value=SimpleNamespace(guest_active=False)),
    )


@pytest.fixture
def owner(agent):
    return ScheduledActionOwner(
        agent.entry.entry_id, agent.subentry.subentry_id, user_id="alice"
    )


@pytest.fixture
async def manager(hass):
    hass.state = CoreState.running
    value = DelayedToolManager(hass)
    await value.async_setup()
    hass.data.setdefault(DOMAIN, {})[DATA_DELAYED_TOOL_MANAGER] = value
    yield value
    value._handle_stop()
    for task in value._tasks.values():
        task.cancel()
    await asyncio.gather(*value._tasks.values(), return_exceptions=True)


async def _schedule(
    manager, agent, *, user="alice", device=None, seconds=60, tool=None, arguments=None
):
    return await manager.async_schedule(
        agent,
        tool["spec"]["name"] if tool else "custom_function",
        arguments or {"private": "message or secret"},
        SimpleNamespace(context=Context(user_id=user), device_id=device),
        function_tool=tool,
        execution_delay={"seconds": seconds},
    )


async def test_scheduled_actions_list_cancel_and_restart(manager, owner, agent, hass):
    slow = await _schedule(manager, agent, seconds=120)
    early = await _schedule(manager, agent)
    assert (await manager.async_list_scheduled_actions(owner))["actions"] == [
        {
            "action_id": record.call_id,
            "scheduled_at": record.due_at,
            "description": "Function Tool: custom_function",
        }
        for record in (early, slow)
    ]
    assert await manager.async_cancel_scheduled_action(owner, early.call_id) == {
        "status": "cancelled",
        "action_id": early.call_id,
    }
    assert await manager.async_cancel_scheduled_action(owner, early.call_id) == {
        "status": "not_pending"
    }
    assert early.call_id not in manager._tasks
    manager._handle_stop()
    await asyncio.gather(*manager._tasks.values(), return_exceptions=True)
    recovered = DelayedToolManager(hass)
    await recovered.async_setup()
    try:
        assert [
            item["action_id"]
            for item in (await recovered.async_list_scheduled_actions(owner))["actions"]
        ] == [slow.call_id]
    finally:
        recovered._handle_stop()
        await asyncio.gather(*recovered._tasks.values(), return_exceptions=True)


@pytest.mark.parametrize("fault", ["before_commit", "after_commit", "unreadable"])
async def test_cancel_uses_authoritative_readback(
    manager, owner, agent, monkeypatch, fault
):
    job = await _schedule(manager, agent)
    store = manager._store
    save = store.async_save

    async def fail(payload):
        if fault == "after_commit":
            await save(payload)
        raise OSError("private write error")

    monkeypatch.setattr(store, "async_save", fail)
    if fault == "unreadable":
        monkeypatch.setattr(
            store, "async_load", AsyncMock(side_effect=OSError("private path"))
        )
    _, definition = scheduled_action_tools()
    call = llm.ToolInput(
        id="cancel",
        tool_name="cancel_scheduled_action",
        tool_args={"action_id": job.call_id},
    )
    with execution_attempt(agent, call, definition):
        if fault == "after_commit":
            assert (await manager.async_cancel_scheduled_action(owner, job.call_id))[
                "status"
            ] == "cancelled"
        elif fault == "before_commit":
            with pytest.raises(HomeAssistantError) as error:
                await manager.async_cancel_scheduled_action(owner, job.call_id)
            assert mutation_failure(error.value).outcome == "rejected"
            assert "private" not in str(error.value)
        else:
            with pytest.raises(EffectOutcomeUnknown):
                await manager.async_cancel_scheduled_action(owner, job.call_id)
    if fault == "unreadable":
        assert await manager.async_list_scheduled_actions(owner) == {
            "status": "unavailable"
        }
    else:
        assert bool((await manager.async_list_scheduled_actions(owner))["actions"]) is (
            fault == "before_commit"
        )


@pytest.mark.parametrize("claim_first", [False, True])
async def test_cancel_and_claim_share_the_execution_boundary(
    manager, owner, agent, monkeypatch, claim_first
):
    job = await _schedule(manager, agent)
    waiter = manager._tasks[job.call_id]
    await asyncio.sleep(0)
    monkeypatch.setattr(dt_util, "utcnow", lambda: datetime.fromisoformat(job.due_at))
    write = manager._store.async_save
    entered, release = asyncio.Event(), asyncio.Event()

    async def gated_save(payload):
        entered.set()
        await release.wait()
        await write(payload)

    monkeypatch.setattr(manager._store, "async_save", gated_save)

    async def cancel():
        return (await manager.async_cancel_scheduled_action(owner, job.call_id))[
            "status"
        ]

    async def claim():
        return await manager._async_claim_due(job.call_id)

    first, second = (claim, cancel) if claim_first else (cancel, claim)
    task_one = asyncio.create_task(first())
    await entered.wait()
    task_two = asyncio.create_task(second())
    release.set()
    results = await asyncio.gather(task_one, task_two)
    assert results == (
        [(True, False), "not_pending"] if claim_first else ["cancelled", (False, False)]
    )
    assert waiter.cancelling() == int(not claim_first)
    assert (await manager.async_list_scheduled_actions(owner))["actions"] == []


async def test_scope_checks_capacity_expiry_and_legacy_ids(
    manager, owner, agent, monkeypatch
):
    with bind_request_satellite("assist_satellite.kitchen"):
        voice_job = await _schedule(manager, agent, user=None, device="kitchen-device")
    user_job = await _schedule(manager, agent)
    for wrong_owner in (
        replace(owner, user_id="bob"),
        replace(owner, subentry_id="other"),
        replace(owner, user_id=None),
        replace(
            owner,
            user_id=None,
            device_id="kitchen-device",
            satellite_id="assist_satellite.office",
        ),
    ):
        assert (await manager.async_list_scheduled_actions(wrong_owner))[
            "actions"
        ] == []
        assert (
            await manager.async_cancel_scheduled_action(wrong_owner, voice_job.call_id)
        )["status"] == "not_pending"
    voice = replace(
        owner,
        user_id=None,
        device_id="kitchen-device",
        satellite_id="assist_satellite.kitchen",
    )
    assert (await manager.async_cancel_scheduled_action(voice, user_job.call_id))[
        "status"
    ] == "not_pending"
    assert (await manager.async_cancel_scheduled_action(voice, voice_job.call_id))[
        "status"
    ] == "cancelled"

    legacy = replace(
        user_job,
        call_id="legacy private ID",
        expires_at=None,
        _expiry_metadata_present=False,
    )
    await manager._async_save_records_transactionally({legacy.call_id: legacy})
    fixed_now = datetime.fromisoformat(user_job.expires_at)
    monkeypatch.setattr(dt_util, "utcnow", lambda: fixed_now)
    rows = (await manager.async_list_scheduled_actions(owner))["actions"]
    assert len(rows) == 1 and rows[0]["action_id"].startswith("legacy_")
    assert "private" not in str(rows)
    assert (await manager.async_cancel_scheduled_action(owner, rows[0]["action_id"]))[
        "status"
    ] == "cancelled"
    for _ in range(MAX_DELAYED_CALLS_PER_AGENT):
        last = await _schedule(manager, agent)
    with pytest.raises(HomeAssistantError, match="queue capacity"):
        await _schedule(manager, agent)
    assert (await manager.async_cancel_scheduled_action(owner, last.call_id))[
        "status"
    ] == "cancelled"
    await _schedule(manager, agent)


async def test_native_summary_is_bounded_and_omits_private_arguments(
    manager, hass, owner, agent
):
    tool = deepcopy(_valid_tool())
    tool["function"] = {"type": "native", "name": "execute_service_single"}
    agent.subentry.data = {"functions": yaml.safe_dump([tool], sort_keys=False)}
    hass.services.async_register("light", "turn_off", lambda _call: None)
    hass.states.get.side_effect = lambda entity_id: (
        object() if entity_id == "light.living_room" else None
    )
    await _schedule(
        manager,
        agent,
        tool=tool,
        arguments={
            "domain": "light",
            "service": "turn_off",
            "service_data": {
                "entity_id": ["light.living_room", "secret.private_key"],
                "message": "password",
            },
        },
    )
    result = await manager.async_list_scheduled_actions(
        replace(owner, administrator=True)
    )
    assert result["actions"][0]["description"] == "light.turn_off (light.living_room)"
    assert "password" not in str(result) and "private_key" not in str(result)
    agent.subentry.data = {"functions": "[]"}
    assert (await manager.async_list_scheduled_actions(owner))["actions"][0][
        "description"
    ].startswith("Function Tool")


async def test_management_list_cancel_requires_admin_without_loaded_entity(
    manager, owner, agent, hass
):
    job = await _schedule(manager, agent, user="another-user")
    request = {
        "section": "scheduled_actions",
        "entry_id": owner.entry_id,
        "subentry_id": owner.subentry_id,
    }
    for action in ("list", "cancel"):
        with pytest.raises(HomeAssistantError, match=r"[Aa]dmin"):
            await async_management_command(
                hass,
                "viewer",
                False,
                {**request, "action": action, "action_id": job.call_id},
            )
    listed = await async_management_command(
        hass, "admin", True, {**request, "action": "list"}
    )
    assert listed["actions"][0]["action_id"] == job.call_id
    result = await async_management_command(
        hass, "admin", True, {**request, "action": "cancel", "action_id": job.call_id}
    )
    assert result["status"] == "cancelled"


async def test_execution_rechecks_guest_and_uses_physical_origin(
    manager, owner, agent, monkeypatch
):
    auth = AsyncMock(return_value=SimpleNamespace(is_active=True))
    monkeypatch.setattr(agent.hass.auth, "async_get_user", auth)
    job = await _schedule(manager, agent)
    voice = SimpleNamespace(
        context=Context(), device_id="kitchen", voice_user_id="alice"
    )
    assert (
        conversation_scheduled_action_owner(
            agent, SimpleNamespace(context=Context(), device_id=None)
        )
        is None
    )
    with bind_request_satellite("assist_satellite.kitchen"):
        source = conversation_scheduled_action_owner(agent, voice)
    assert source.user_id is None
    assert (await async_execute_scheduled_action_tool(agent, source, "list", {}))[
        "actions"
    ] == []
    auth.assert_not_awaited()
    with pytest.raises(HomeAssistantError, match="exact"):
        await async_execute_scheduled_action_tool(
            agent, owner, "cancel", {"action_id": job.call_id, "user_id": "bob"}
        )

    async with manager._lock:
        cancel = asyncio.create_task(
            async_execute_scheduled_action_tool(
                agent, owner, "cancel", {"action_id": job.call_id}
            )
        )
        await asyncio.sleep(0)
        agent._effective_guest_policy.return_value.guest_active = True
    assert (await cancel)["status"] == "denied"
    assert job.call_id in manager._records


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
def test_runtime_identity_tool_schema_capacity_and_reserved_collision(mode):
    tools = assemble_integration_function_tools(
        {},
        set(),
        memory_scope_available=False,
        temporary_scope_available=False,
        knowledge_available=False,
        lifecycle_active=False,
        scheduled_action_owner_available=True,
    )
    assert [is_integration_owned_read_only_tool(tool) for tool in tools] == [
        True,
        False,
    ]
    assert len(format_function_tools(tools, mode)) == 2
    assert fit_scheduled_action_tools(tools, 1, extra_tool_count=0) == []
    assert fit_scheduled_action_tools(tools, 2, extra_tool_count=0) == tools
    valid = deepcopy(_valid_tool())
    collision = {**valid, "spec": {**valid["spec"], "name": "cancel_scheduled_action"}}
    assert _runtime_configured_function_tools(
        {"functions": yaml.safe_dump([valid, collision])}
    ) == [valid]


@pytest.mark.parametrize("new_identity", [False, True])
async def test_native_timer_restriction_preserves_schema_and_authoritative_validation(
    new_identity, hass
):
    handler = SimpleNamespace(
        description="Start a timer or delayed command",
        slot_schema={
            vol.Required("seconds"): int,
            vol.Optional("conversation_command"): str,
        },
        intent_type=intent.INTENT_START_TIMER,
    )
    tool = llm.IntentTool(intent.INTENT_START_TIMER, handler)
    # Exercise both native identity contracts even if this test's HA only
    # implements one of them. Real discovery is tested by the pinned HA jobs.
    if new_identity:
        tool.name = "intent__HassStartTimer"
        tool.intent_type = intent.INTENT_START_TIMER
    elif hasattr(tool, "intent_type"):
        del tool.intent_type
    tool.parameters = vol.Schema(handler.slot_schema)
    original = {
        "description": handler.description,
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {"type": "integer"},
                "conversation_command": {"type": "string"},
            },
            "required": ["seconds"],
        },
    }
    ref = {
        "type": "ha_llm",
        "source_type": "platform",
        "source_id": "intent",
        "api_id": "assist",
        "tool_name": tool.name,
    }
    instance = SimpleNamespace(
        tools=[tool], async_call_tool=AsyncMock(return_value={"timer": "started"})
    )
    live = LiveTool(ref, tool, instance, "HA", "Always use intent tools.", original)
    key = reference_key(ref)
    source = ToolSnapshot(tools={key: live})
    alias = {"spec": {"name": "my_timer"}, "function": ref}
    assert (
        "conversation_command"
        in source.with_delayed_action_preference({}).project([alias])[0]["spec"][
            "parameters"
        ]["properties"]
    )
    restricted = source.with_delayed_action_preference(
        {CONF_LOCAL_INTENT_DELAYED_COMMANDS_TO_AI: True}
    )
    projected = restricted.project([alias])
    assert projected[0]["spec"]["name"] == "my_timer"
    assert (
        "conversation_command" not in projected[0]["spec"]["parameters"]["properties"]
    )
    assert "conversation_command" in original["parameters"]["properties"]
    assert "Always use intent tools." in restricted.prompt_for(projected)
    assert "No delay-capable EOAI Function Tool" in restricted.prompt_for(projected)

    def timer_call(**arguments):
        return llm.ToolInput(tool_name="my_timer", id="timer", tool_args=arguments)

    with pytest.raises(HomeAssistantError, match="ordinary timers"):
        await restricted.tools[key].async_call(
            timer_call(seconds=60, conversation_command="later")
        )
    with pytest.raises(HomeAssistantError, match="requested schema"):
        await restricted.tools[key].async_call(timer_call(seconds="invalid"))
    instance.async_call_tool.assert_not_awaited()
    assert await restricted.tools[key].async_call(timer_call(seconds=60)) == {
        "timer": "started"
    }
    assert instance.async_call_tool.await_args.args[0].tool_name == tool.name
    await live.async_call(timer_call(seconds=60, conversation_command="later"))

    match = SimpleNamespace(
        intent=SimpleNamespace(name=intent.INTENT_START_TIMER),
        entities={"conversation_command": object()},
    )
    assert not should_handle_locally(match, (), True)
    assert should_handle_locally(match, (), False)
    match.entities.clear()
    assert should_handle_locally(match, (), True)


@pytest.mark.parametrize("is_native", [True, False])
async def test_dedicated_ha_delayed_command_uses_native_identity(is_native):
    tool_type = type(
        "ScheduleCommandTool",
        (llm.Tool,),
        {
            "__module__": "homeassistant.components.intent.llm" if is_native else "custom",
            "name": "intent__schedule_command",
            "parameters": vol.Schema({vol.Required("command"): str}),
        },
    )
    tool = tool_type()
    reference = {
        "type": "ha_llm",
        "source_type": "platform",
        "source_id": "intent",
        "api_id": llm.LLM_API_ASSIST,
        "tool_name": tool.name,
    }
    instance = SimpleNamespace(
        tools=[tool], async_call_tool=AsyncMock(return_value={"success": True})
    )
    live = LiveTool(reference, tool, instance, "HA", "", {})
    key = reference_key(reference)
    source = ToolSnapshot(tools={key: live})
    configured = {"spec": {"name": "custom_alias"}, "function": reference}
    restricted = source.with_delayed_action_preference(
        {CONF_LOCAL_INTENT_DELAYED_COMMANDS_TO_AI: True}
    )
    assert restricted.project([configured])[0]["ha_available"] is not is_native
    call = llm.ToolInput(
        tool_name="custom_alias", id="command", tool_args={"command": "later"}
    )
    if is_native:
        with pytest.raises(HomeAssistantError, match="Use an EOAI Function Tool"):
            await live.restrict_delayed_commands().async_call(call)
        instance.async_call_tool.assert_not_awaited()
    assert await source.tools[key].async_call(call) == {"success": True}


async def test_unavailable_expiry_and_invalid_id(
    hass, manager, owner, agent, monkeypatch
):
    assert await DelayedToolManager(hass).async_list_scheduled_actions(owner) == {
        "status": "unavailable"
    }
    job = await _schedule(manager, agent)
    monkeypatch.setattr(
        dt_util, "utcnow", lambda: datetime.fromisoformat(job.expires_at)
    )
    assert (await manager.async_list_scheduled_actions(owner))["actions"] == []
    assert (await manager.async_cancel_scheduled_action(owner, job.call_id))[
        "status"
    ] == "not_pending"
    with pytest.raises(HomeAssistantError, match="complete"):
        await manager.async_cancel_scheduled_action(owner, "prefix")
    assert job.call_id in manager._records


@pytest.mark.parametrize("local_enabled", [False, True])
async def test_recognized_delayed_commands_can_be_routed_without_local_execution(
    hass, monkeypatch, local_enabled
):
    from custom_components.extended_openai_conversation_responses.local_intents import (
        CONF_LOCAL_INTENTS_ENABLED,
        async_try_handle_local_intent,
    )
    from homeassistant.components import conversation

    recognition = SimpleNamespace(
        intent=SimpleNamespace(name=intent.INTENT_START_TIMER),
        entities={"conversation_command": object()},
    )
    picked = Mock()

    async def recognize(_hass, _input, _log, *, intent_filter):
        # Recognition runs even when local execution is off. Returning True
        # is the Core filter contract for rejecting the selected native intent.
        assert intent_filter(recognition) is True
        return None

    monkeypatch.setattr(conversation, "async_handle_intents", recognize)
    result = await async_try_handle_local_intent(
        hass,
        SimpleNamespace(context=Context()),
        SimpleNamespace(content=[]),
        {
            CONF_LOCAL_INTENTS_ENABLED: local_enabled,
            CONF_LOCAL_INTENT_DELAYED_COMMANDS_TO_AI: True,
        },
        guest_active=False,
        on_delayed_action=picked,
    )
    assert result is None
    picked.assert_called_once_with()
    snapshot = ToolSnapshot(require_positive_delay=True).with_delayed_action_preference(
        {CONF_LOCAL_INTENT_DELAYED_COMMANDS_TO_AI: True}
    )
    immediate = {"spec": {"name": "immediate"}, "function": {"type": "template"}}
    suitable = {
        "spec": {
            "name": "delayed",
            "parameters": {
                "properties": {
                    "delay": {
                        "type": "object",
                        "properties": {"seconds": {"type": "integer"}},
                    },
                }
            },
        },
        "function": {"type": "template"},
    }
    assert snapshot.admit_delayed_action_tools([immediate, suitable]) == [suitable]
    assert "No delay-capable EOAI Function Tool" in snapshot.prompt_for([])


async def test_unknown_cancel_does_not_interrupt_another_executing_waiter(
    manager, owner, agent, monkeypatch
):
    job = await _schedule(manager, agent, seconds=120)
    other = await _schedule(manager, agent, seconds=60)
    waiter = manager._tasks[other.call_id]
    await asyncio.sleep(0)
    monkeypatch.setattr(dt_util, "utcnow", lambda: datetime.fromisoformat(other.due_at))
    assert await manager._async_claim_due(other.call_id) == (True, False)
    monkeypatch.setattr(manager._store, "async_save", AsyncMock(side_effect=OSError))
    monkeypatch.setattr(manager._store, "async_load", AsyncMock(side_effect=OSError))
    try:
        with pytest.raises(HomeAssistantError) as error:
            await manager.async_cancel_scheduled_action(owner, job.call_id)
        assert mutation_failure(error.value).outcome == "uncertain"
        assert not waiter.cancelling()
        assert await manager.async_list_scheduled_actions(owner) == {
            "status": "unavailable"
        }
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
