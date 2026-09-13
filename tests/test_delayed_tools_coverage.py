"""Focused branch coverage for durable delayed Function Tools."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DATA_DELAYED_TOOL_MANAGER,
    DelayedToolCall,
    DelayedToolManager,
    _DELAYED_EXECUTION_MARKER,
    _EXECUTING,
    _MAX_AGENT_RETRIES,
    _delay_as_timedelta,
    _install_execution_hook,
    async_setup_delayed_tools,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)


def _record(
    *,
    call_id: str = "call-1",
    status: str = "pending",
    retry_count: int = 0,
    user_id: str | None = "user-1",
    due_at: str | None = None,
) -> DelayedToolCall:
    now = dt_util.utcnow()
    return DelayedToolCall(
        call_id=call_id,
        entry_id="entry",
        subentry_id="agent",
        tool_name="control_light",
        arguments={"delay": {"seconds": 5}, "value": 1},
        due_at=due_at or (now - timedelta(seconds=1)).isoformat(),
        created_at=(now - timedelta(seconds=6)).isoformat(),
        user_id=user_id,
        device_id="device-1",
        status=status,
        retry_count=retry_count,
    )


def _valid_tool(*, function_type: str = "native") -> dict[str, Any]:
    return {
        "enabled": True,
        "spec": {"name": "control_light"},
        "function": {"type": function_type, "name": "execute_service_single"},
    }


def _live_entry() -> SimpleNamespace:
    return SimpleNamespace(
        disabled_by=None,
        subentries={
            "agent": SimpleNamespace(subentry_type="conversation", data={})
        },
    )


def _stored_call() -> dict[str, Any]:
    return _record().as_dict()


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda raw: "not-an-object", "not an object"),
        (lambda raw: {**raw, "call_id": ""}, "invalid call_id"),
        (lambda raw: {**raw, "arguments": []}, "invalid arguments"),
        (lambda raw: {**raw, "status": "done"}, "invalid status"),
        (lambda raw: {**raw, "retry_count": -1}, "invalid retry_count"),
        (lambda raw: {**raw, "due_at": "not-a-date"}, "invalid due_at timestamp"),
        (
            lambda raw: {**raw, "created_at": "not-a-date"},
            "invalid created_at timestamp",
        ),
        (lambda raw: {**raw, "user_id": 123}, "invalid user_id"),
        (lambda raw: {**raw, "device_id": 123}, "invalid device_id"),
    ],
)
def test_persisted_call_validation_rejects_corrupt_shapes(mutate, match: str) -> None:
    """Every persisted field used for later authorization must be validated."""
    with pytest.raises(ValueError, match=match):
        DelayedToolCall.from_dict(mutate(_stored_call()))


def test_negative_delay_is_rejected() -> None:
    """A delayed tool can never be scheduled backwards in time."""
    with pytest.raises(HomeAssistantError, match="cannot be negative"):
        _delay_as_timedelta({"seconds": -1})


async def test_setup_cleans_invalid_persisted_records_and_is_idempotent(hass) -> None:
    """Corrupt records are removed durably and a second setup is a no-op."""
    valid = _record(call_id="valid")
    manager = DelayedToolManager(hass)
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value={"calls": [valid.as_dict(), {"bad": True}]}),
        async_save=AsyncMock(),
    )

    await manager.async_setup()
    await manager.async_setup()

    assert manager._records == {"valid": valid}
    manager._store.async_load.assert_awaited_once()
    manager._store.async_save.assert_awaited_once_with({"calls": [valid.as_dict()]})


async def test_schedule_requires_setup_and_arms_when_already_started(
    hass, monkeypatch
) -> None:
    """Scheduling is unavailable before recovery and arms immediately after start."""
    manager = DelayedToolManager(hass)
    entity = SimpleNamespace(
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent"),
    )

    with pytest.raises(HomeAssistantError, match="scheduler is unavailable"):
        await manager.async_schedule(
            entity,
            "control_light",
            {"delay": {"seconds": 1}},
            None,
        )

    manager._setup_complete = True
    manager._started = True
    manager._store = SimpleNamespace(async_save=AsyncMock())
    arm = MagicMock()
    monkeypatch.setattr(manager, "_arm", arm)

    record = await manager.async_schedule(
        entity,
        "control_light",
        {"delay": {"seconds": 1}},
        None,
    )

    arm.assert_called_once_with(record.call_id)
    assert record.user_id is None
    assert record.device_id is None


def test_start_stop_lifecycle_arms_once_and_preserves_executing_task(
    hass, monkeypatch
) -> None:
    """Startup arms records once; shutdown cancels only replay-safe pending work."""
    manager = DelayedToolManager(hass)
    manager._records = {
        "pending": _record(call_id="pending"),
        "executing": _record(call_id="executing", status=_EXECUTING),
    }
    arm = MagicMock()
    monkeypatch.setattr(manager, "_arm", arm)

    manager._handle_started()
    manager._handle_started()

    assert manager._started is True
    assert [item.args[0] for item in arm.call_args_list] == ["pending", "executing"]

    pending_task = MagicMock()
    executing_task = MagicMock()
    orphan_task = MagicMock()
    manager._tasks = {
        "pending": pending_task,
        "executing": executing_task,
        "missing": orphan_task,
    }

    manager._handle_stop()

    assert manager._started is False
    pending_task.cancel.assert_called_once_with()
    orphan_task.cancel.assert_called_once_with()
    executing_task.cancel.assert_not_called()


def test_arm_ignores_live_waiter_missing_record_and_nonpending_record(hass) -> None:
    """Arming cannot duplicate waiters or resurrect absent/completed records."""
    manager = DelayedToolManager(hass)
    live_task = MagicMock()
    live_task.done.return_value = False
    manager._tasks["call-1"] = live_task
    manager._records["call-1"] = _record()

    manager._arm("call-1")
    assert manager._tasks["call-1"] is live_task

    manager._tasks.clear()
    manager._arm("missing")
    assert manager._tasks == {}

    manager._records["executing"] = _record(call_id="executing", status=_EXECUTING)
    manager._arm("executing")
    assert manager._tasks == {}


async def test_waiter_discards_invalid_due_timestamp_and_cleans_task(
    hass, monkeypatch
) -> None:
    """A corrupt in-memory due timestamp is discarded and cannot strand a waiter."""
    manager = DelayedToolManager(hass)
    manager._started = True
    record = _record(due_at="not-a-date")
    manager._records = {record.call_id: record}
    manager._tasks = {record.call_id: MagicMock()}
    discard = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "_async_discard", discard)

    await manager._async_wait_and_execute(record.call_id)

    discard.assert_awaited_once_with(record.call_id, "invalid due timestamp")
    assert record.call_id not in manager._tasks


async def test_waiter_uses_maintenance_gate_and_stops_after_execution(
    hass, monkeypatch
) -> None:
    """Due execution is serialized through the ordinary-agent maintenance gate."""
    manager = DelayedToolManager(hass)
    manager._started = True
    record = _record()
    manager._records = {record.call_id: record}
    manager._tasks = {record.call_id: MagicMock()}

    class Gate:
        def shared(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    gate = Gate()
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.get_agent_maintenance_gate",
        lambda *_args: gate,
    )
    sleep = AsyncMock()
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.asyncio.sleep",
        sleep,
    )
    execute_due = AsyncMock(return_value=False)
    monkeypatch.setattr(manager, "_async_execute_due", execute_due)

    await manager._async_wait_and_execute(record.call_id)

    execute_due.assert_awaited_once_with(record.call_id)
    assert record.call_id not in manager._tasks


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        (None, "config entry is unavailable"),
        (
            SimpleNamespace(disabled_by="user", subentries={}),
            "config entry is unavailable",
        ),
        (
            SimpleNamespace(disabled_by=None, subentries={}),
            "conversation agent is unavailable",
        ),
        (
            SimpleNamespace(
                disabled_by=None,
                subentries={"agent": SimpleNamespace(subentry_type="other", data={})},
            ),
            "conversation agent is unavailable",
        ),
    ],
)
async def test_due_call_discards_when_live_agent_configuration_disappears(
    hass, monkeypatch, entry, reason: str
) -> None:
    """Persisted work is re-authorized against the live config-entry topology."""
    manager = DelayedToolManager(hass)
    record = _record()
    manager._records = {record.call_id: record}
    hass.config_entries.async_get_entry = MagicMock(return_value=entry)
    discard = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "_async_discard", discard)

    assert await manager._async_execute_due(record.call_id) is False
    discard.assert_awaited_once_with(record.call_id, reason)


async def test_due_call_discards_invalid_live_tool_configuration(hass, monkeypatch) -> None:
    """A now-invalid Function Tool config cannot execute stale persisted arguments."""
    manager = DelayedToolManager(hass)
    record = _record()
    manager._records = {record.call_id: record}
    hass.config_entries.async_get_entry = MagicMock(return_value=_live_entry())
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.configured_function_tools_from_data",
        MagicMock(side_effect=ValueError("bad config")),
    )
    discard = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "_async_discard", discard)

    assert await manager._async_execute_due(record.call_id) is False
    discard.assert_awaited_once_with(
        record.call_id, "live Function Tool configuration is invalid"
    )


async def test_due_call_discards_tool_that_is_no_longer_delay_eligible(
    hass, monkeypatch
) -> None:
    """A delayed call cannot become an HA-owned LLM tool after scheduling."""
    manager = DelayedToolManager(hass)
    record = _record()
    manager._records = {record.call_id: record}
    hass.config_entries.async_get_entry = MagicMock(return_value=_live_entry())
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.configured_function_tools_from_data",
        lambda _data: [_valid_tool(function_type="ha_llm")],
    )
    discard = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "_async_discard", discard)

    assert await manager._async_execute_due(record.call_id) is False
    discard.assert_awaited_once_with(
        record.call_id, "Function Tool was removed or disabled"
    )


async def test_due_call_discards_inactive_originating_user(hass, monkeypatch) -> None:
    """A revoked user identity invalidates a delayed action before agent resolution."""
    manager = DelayedToolManager(hass)
    record = _record()
    manager._records = {record.call_id: record}
    hass.config_entries.async_get_entry = MagicMock(return_value=_live_entry())
    hass.auth.async_get_user = AsyncMock(return_value=SimpleNamespace(is_active=False))
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.configured_function_tools_from_data",
        lambda _data: [_valid_tool()],
    )
    resolve_agent = MagicMock()
    monkeypatch.setattr(manager, "_resolve_agent", resolve_agent)
    discard = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "_async_discard", discard)

    assert await manager._async_execute_due(record.call_id) is False
    discard.assert_awaited_once_with(
        record.call_id, "originating user is no longer active"
    )
    resolve_agent.assert_not_called()


async def test_due_call_retries_when_agent_is_temporarily_missing(hass, monkeypatch) -> None:
    """A transient entity reload persists a retry count instead of losing the call."""
    manager = DelayedToolManager(hass)
    record = _record(user_id=None)
    manager._records = {record.call_id: record}
    manager._store = SimpleNamespace(async_save=AsyncMock())
    hass.config_entries.async_get_entry = MagicMock(return_value=_live_entry())
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.configured_function_tools_from_data",
        lambda _data: [_valid_tool()],
    )
    monkeypatch.setattr(manager, "_resolve_agent", lambda *_args: None)

    assert await manager._async_execute_due(record.call_id) is True
    assert manager._records[record.call_id].retry_count == 1
    manager._store.async_save.assert_awaited_once()


async def test_retry_limit_discards_and_retry_save_failure_still_retries(
    hass, monkeypatch
) -> None:
    """Retry exhaustion cancels; a transient save failure remains retryable."""
    manager = DelayedToolManager(hass)
    exhausted = _record(retry_count=_MAX_AGENT_RETRIES)
    discard = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "_async_discard", discard)

    assert await manager._async_retry_agent(exhausted) is False
    discard.assert_awaited_once_with(
        exhausted.call_id, "conversation agent did not become available"
    )

    replace_record = AsyncMock(side_effect=OSError("storage unavailable"))
    monkeypatch.setattr(manager, "_async_replace_record", replace_record)
    assert await manager._async_retry_agent(_record()) is True
    replace_record.assert_awaited_once()


async def test_due_call_discards_when_live_tool_resolution_fails(hass, monkeypatch) -> None:
    """Runtime tool-resolution errors cancel stale work instead of executing it."""
    manager = DelayedToolManager(hass)
    record = _record(user_id=None)
    manager._records = {record.call_id: record}
    hass.config_entries.async_get_entry = MagicMock(return_value=_live_entry())
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.configured_function_tools_from_data",
        lambda _data: [_valid_tool()],
    )
    monkeypatch.setattr(manager, "_resolve_agent", lambda *_args: object())
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.latest_function_tool_for_execution",
        MagicMock(side_effect=HomeAssistantError("gone")),
    )
    discard = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "_async_discard", discard)

    assert await manager._async_execute_due(record.call_id) is False
    discard.assert_awaited_once_with(
        record.call_id, "Function Tool is unavailable: gone"
    )


def test_resolve_agent_filters_registry_and_returns_live_agent(hass, monkeypatch) -> None:
    """Agent lookup ignores unrelated registry entries and returns only the live match."""
    manager = DelayedToolManager(hass)
    unrelated = SimpleNamespace(
        platform="other",
        domain=conversation.DOMAIN,
        config_entry_id="entry",
        config_subentry_id="agent",
        entity_id="conversation.other",
    )
    matching = SimpleNamespace(
        platform=DOMAIN,
        domain=conversation.DOMAIN,
        config_entry_id="entry",
        config_subentry_id="agent",
        entity_id="conversation.agent",
    )
    registry = SimpleNamespace(entities={"one": unrelated, "two": matching})
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.er.async_get",
        lambda _hass: registry,
    )
    agent = object()
    get_agent = MagicMock(return_value=agent)
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.conversation.async_get_agent",
        get_agent,
    )

    assert manager._resolve_agent("entry", "agent") is agent
    get_agent.assert_called_once_with(hass, "conversation.agent")

    get_agent.return_value = None
    assert manager._resolve_agent("entry", "agent") is None


async def test_record_storage_helpers_preserve_durability_on_failures(hass) -> None:
    """Replace/discard/finalize handle absent records and failed persistence safely."""
    manager = DelayedToolManager(hass)
    manager._store = SimpleNamespace(async_save=AsyncMock())

    await manager._async_replace_record(_record(call_id="missing"))
    manager._store.async_save.assert_not_awaited()
    assert await manager._async_discard("missing", "gone") is True
    await manager._async_finalize("missing")

    record = _record()
    manager._records = {record.call_id: record}
    manager._store.async_save.side_effect = OSError("storage unavailable")

    assert await manager._async_discard(record.call_id, "cancel") is False
    assert manager._records[record.call_id] == record

    executing = DelayedToolCall.from_dict({**record.as_dict(), "status": _EXECUTING})
    manager._records = {record.call_id: executing}
    await manager._async_finalize(record.call_id)
    assert record.call_id not in manager._records


async def test_shared_setup_creates_or_reuses_manager_and_installs_hook(
    hass, monkeypatch
) -> None:
    """Integration-global setup owns one manager and always ensures the hook exists."""
    setup = AsyncMock()
    monkeypatch.setattr(DelayedToolManager, "async_setup", setup)
    install = MagicMock()
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools._install_execution_hook",
        install,
    )

    hass.data.setdefault(DOMAIN, {}).pop(DATA_DELAYED_TOOL_MANAGER, None)
    created = await async_setup_delayed_tools(hass)
    assert hass.data[DOMAIN][DATA_DELAYED_TOOL_MANAGER] is created
    setup.assert_awaited_once_with()
    install.assert_called_once_with()

    setup.reset_mock()
    install.reset_mock()
    reused = await async_setup_delayed_tools(hass)
    assert reused is created
    setup.assert_awaited_once_with()
    install.assert_called_once_with()


async def test_delayed_hook_blocks_ha_llm_replay_and_delegates_live_ha_llm(
    hass, monkeypatch
) -> None:
    """HA-owned tools delegate normally but are forbidden inside delayed replay."""
    original_spy = AsyncMock(return_value="delegated")

    async def original(*args):
        return await original_spy(*args)

    monkeypatch.setattr(ExtendedOpenAIBaseLLMEntity, "_execute_function_tool", original)
    _install_execution_hook()
    wrapper = ExtendedOpenAIBaseLLMEntity._execute_function_tool
    _install_execution_hook()
    assert ExtendedOpenAIBaseLLMEntity._execute_function_tool is wrapper

    entity = SimpleNamespace(hass=hass)
    function_tool = {"function": {"type": "ha_llm"}, "spec": {"name": "ha"}}
    tool_input = llm.ToolInput(
        id="call", tool_name="ha", tool_args={}, external=True
    )

    assert await wrapper(entity, function_tool, tool_input, None, []) == "delegated"
    original_spy.assert_awaited_once()

    delayed_context = SimpleNamespace(**{_DELAYED_EXECUTION_MARKER: True})
    with pytest.raises(HomeAssistantError, match="cannot execute in the delayed scheduler"):
        await wrapper(entity, function_tool, tool_input, delayed_context, [])


async def test_delayed_hook_executes_native_replay_directly(hass, monkeypatch) -> None:
    """A replay-marked native call executes once without scheduling itself again."""
    original_spy = AsyncMock()

    async def original(*args):
        return await original_spy(*args)

    monkeypatch.setattr(ExtendedOpenAIBaseLLMEntity, "_execute_function_tool", original)
    validate = AsyncMock(return_value={"delay": {"seconds": 10}, "value": 7})
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.async_validate_function_arguments",
        validate,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.split_legacy_execution_delay",
        lambda _spec, _args: ({"value": 7}, timedelta(seconds=10)),
    )
    function = SimpleNamespace(execute=AsyncMock(return_value="done"))
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.get_function",
        lambda _type: function,
    )
    _install_execution_hook()
    wrapper = ExtendedOpenAIBaseLLMEntity._execute_function_tool
    entity = SimpleNamespace(hass=hass, entity_id="conversation.agent")
    tool = {
        "spec": {"name": "control_light"},
        "function": {"type": "native", "name": "execute_service_single"},
    }
    tool_input = llm.ToolInput(
        id="call", tool_name="control_light", tool_args={"value": 7}, external=True
    )
    context = SimpleNamespace(**{_DELAYED_EXECUTION_MARKER: True})

    result = await wrapper(entity, tool, tool_input, context, [])

    original_spy.assert_not_awaited()
    function.execute.assert_awaited_once_with(
        hass,
        tool["function"],
        {"value": 7},
        context,
        [],
    )
    assert result.tool_result == {"result": "done"}


async def test_delayed_hook_requires_scheduler_for_background_call(
    hass, monkeypatch
) -> None:
    """A background-eligible call fails closed if the durable manager is absent."""
    original_spy = AsyncMock()

    async def original(*args):
        return await original_spy(*args)

    monkeypatch.setattr(ExtendedOpenAIBaseLLMEntity, "_execute_function_tool", original)
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.async_validate_function_arguments",
        AsyncMock(return_value={"delay": {"seconds": 10}}),
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.split_legacy_execution_delay",
        lambda _spec, _args: ({}, timedelta(seconds=10)),
    )
    _install_execution_hook()
    wrapper = ExtendedOpenAIBaseLLMEntity._execute_function_tool
    hass.data.setdefault(DOMAIN, {}).pop(DATA_DELAYED_TOOL_MANAGER, None)
    entity = SimpleNamespace(
        hass=hass,
        entity_id="conversation.agent",
        should_run_in_background=lambda _delay: True,
    )
    tool = {
        "spec": {"name": "control_light"},
        "function": {"type": "native", "name": "execute_service_single"},
    }
    tool_input = llm.ToolInput(
        id="call", tool_name="control_light", tool_args={}, external=True
    )

    with pytest.raises(HomeAssistantError, match="scheduler is unavailable"):
        await wrapper(entity, tool, tool_input, None, [])
    original_spy.assert_not_awaited()


async def test_delayed_hook_schedules_background_call_and_returns_receipt(
    hass, monkeypatch
) -> None:
    """Background routing persists the validated call and returns a scheduled receipt."""
    original_spy = AsyncMock()

    async def original(*args):
        return await original_spy(*args)

    monkeypatch.setattr(ExtendedOpenAIBaseLLMEntity, "_execute_function_tool", original)
    arguments = {"delay": {"seconds": 10}, "value": 7}
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.async_validate_function_arguments",
        AsyncMock(return_value=arguments),
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.split_legacy_execution_delay",
        lambda _spec, _args: ({"value": 7}, timedelta(seconds=10)),
    )
    _install_execution_hook()
    wrapper = ExtendedOpenAIBaseLLMEntity._execute_function_tool
    manager = DelayedToolManager(hass)
    manager.async_schedule = AsyncMock(return_value=_record())
    hass.data.setdefault(DOMAIN, {})[DATA_DELAYED_TOOL_MANAGER] = manager
    entity = SimpleNamespace(
        hass=hass,
        entity_id="conversation.agent",
        should_run_in_background=lambda _delay: True,
    )
    tool = {
        "spec": {"name": "control_light"},
        "function": {"type": "native", "name": "execute_service_single"},
    }
    tool_input = llm.ToolInput(
        id="call", tool_name="control_light", tool_args={"value": 7}, external=True
    )
    context = SimpleNamespace(context=Context(user_id="user-1"), device_id="device-1")

    result = await wrapper(entity, tool, tool_input, context, [])

    manager.async_schedule.assert_awaited_once_with(
        entity, "control_light", arguments, context
    )
    original_spy.assert_not_awaited()
    assert result.tool_result == {"result": "Scheduled"}
