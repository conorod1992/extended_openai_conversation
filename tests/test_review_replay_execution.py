"""Replay decisions at real scheduler, composite and HA action boundaries."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses import (
    delayed_tools,
    ha_actions,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.execution_arguments import (
    execution_identity,
)
from custom_components.extended_openai_conversation_responses.execution_outcomes import (
    execution_attempt,
)
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    async_execute_tool_exchange,
)
from custom_components.extended_openai_conversation_responses.functions import native
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    remember_unacknowledged_calls,
    was_unacknowledged_equivalent,
)
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from tests.test_function_tool_error_recovery import _call, _chat_log, _retain_calls


RAW = {"domain": "light", "service": "toggle", "entity_id": "light.kitchen"}
ALIAS = {
    "domain": "light",
    "service": "toggle",
    "service_data": {"entity_id": ["light.kitchen"]},
}
NATIVE = {"type": "native", "name": "execute_service_single"}


def _tool(config=NATIVE):
    return {
        "spec": {
            "name": "effect",
            "parameters": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "delay": {
                        "type": "object",
                        "properties": {"seconds": {"type": "number"}},
                    }
                },
            },
        },
        "function": deepcopy(config),
    }


def _entity(hass):
    return SimpleNamespace(
        hass=hass,
        entity_id="conversation.test",
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent"),
        should_run_in_background=lambda delay: delay is not None,
    )


async def _execute(entity, tool, call):
    with execution_attempt(entity, call, tool):
        return await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
            entity, tool, call, None, []
        )


def _install_native_boundary(hass, monkeypatch):
    # Replace authorization discovery and device effects, keeping the native
    # executor and its real service receipt boundary intact.
    monkeypatch.setattr(
        native.NativeFunction, "validate_service_targets", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        native, "async_call_ha_action", ha_actions._async_call_ha_action_unchecked
    )
    monkeypatch.setattr(ha_actions, "_capture_previous_state", lambda *a: {})
    hass.services.has_service.return_value = True
    hass.services.supports_response.return_value = SupportsResponse.NONE
    hass.services.async_call = AsyncMock(return_value=None)


@pytest.mark.parametrize(
    "failure,expected",
    [
        (vol.Invalid("schema rejected"), "rejected"),
        (TimeoutError("acknowledgement lost"), "uncertain"),
        (RuntimeError("handler failed"), "uncertain"),
    ],
)
async def test_native_boundary_distinguishes_rejection_from_unknown(
    hass, monkeypatch, failure, expected
):
    _install_native_boundary(hass, monkeypatch)
    hass.services.async_call.side_effect = failure
    entity, log, tool = _entity(hass), _chat_log(hass), _tool()
    call = _call("first", "effect", RAW)
    _retain_calls(log, [call])
    try:
        await _execute(entity, tool, call)
    except HomeAssistantError, RuntimeError, TimeoutError:
        pass
    assert entity._execution_tool_outcomes[id(call)] == expected
    remember_unacknowledged_calls(entity, log, set())
    retry = _call("corrected", "effect", {**RAW, "entity_id": "light.other"})
    assert was_unacknowledged_equivalent(entity, log, retry) is (
        expected == "uncertain"
    )
    if expected == "rejected":
        hass.services.async_call.side_effect = None
        await _execute(entity, tool, retry)
        assert hass.services.async_call.await_count == 2


@pytest.mark.parametrize(
    "admission", ["quarantined", "unavailable", "capacity", "budget"]
)
async def test_scheduler_admission_has_no_effect_receipt(hass, monkeypatch, admission):
    manager = delayed_tools.DelayedToolManager(hass)
    manager._setup_complete = admission != "unavailable"
    manager._quarantined = admission == "quarantined"
    manager._store = SimpleNamespace(async_save=AsyncMock())
    if admission == "capacity":
        monkeypatch.setattr(delayed_tools, "MAX_DELAYED_CALLS", 0)
    if admission == "budget":
        monkeypatch.setattr(delayed_tools, "MAX_DELAYED_ARGUMENT_BYTES", 1)
    hass.data.setdefault(DOMAIN, {})[delayed_tools.DATA_DELAYED_TOOL_MANAGER] = manager
    entity, log, tool = _entity(hass), _chat_log(hass), _tool()
    call = _call("first", "effect", {**RAW, "delay": {"seconds": 30}})
    _retain_calls(log, [call])
    with pytest.raises(HomeAssistantError):
        await _execute(entity, tool, call)
    assert entity._execution_tool_outcomes[id(call)] == "rejected"
    manager._store.async_save.assert_not_awaited()
    assert manager._records == {}
    remember_unacknowledged_calls(entity, log, set())
    assert not was_unacknowledged_equivalent(
        entity, log, _call("corrected", "effect", RAW)
    )


@pytest.mark.parametrize(
    "first_error", [None, HomeAssistantError("first outcome unknown")]
)
async def test_native_batch_keeps_prior_effects_when_next_child_is_rejected(
    hass, monkeypatch, first_error
):
    _install_native_boundary(hass, monkeypatch)
    hass.services.async_call.side_effect = [first_error, vol.Invalid("second rejected")]
    entity, log, tool = (
        _entity(hass),
        _chat_log(hass),
        _tool({"type": "native", "name": "execute_service"}),
    )
    call = _call(
        "first", "effect", {"list": [RAW, {**RAW, "entity_id": "light.other"}]}
    )
    _retain_calls(log, [call])
    await _execute(entity, tool, call)
    assert hass.services.async_call.await_count == 2
    assert entity._execution_tool_outcomes[id(call)] == "uncertain"
    remember_unacknowledged_calls(entity, log, set())
    assert was_unacknowledged_equivalent(
        entity, log, _call("retry", "effect", {"list": [RAW]})
    )


@pytest.mark.parametrize(
    "mode", ["success", "committed_error", "rejected_write", "unreadable"]
)
async def test_scheduler_receipts_follow_authoritative_queue_generation(hass, mode):
    manager = delayed_tools.DelayedToolManager(hass)
    manager._setup_complete = True
    persisted = {"calls": []}

    async def save(data):
        if mode != "rejected_write":
            persisted.update(deepcopy(data))
        if mode != "success":
            raise OSError("write acknowledgement failed")

    async def load():
        if mode == "unreadable":
            raise OSError("read failed")
        return deepcopy(persisted)

    manager._store = SimpleNamespace(async_save=save, async_load=load)
    hass.data.setdefault(DOMAIN, {})[delayed_tools.DATA_DELAYED_TOOL_MANAGER] = manager
    entity, log, tool = _entity(hass), _chat_log(hass), _tool()
    call = _call("first", "effect", {**RAW, "delay": {"seconds": 30}})
    _retain_calls(log, [call])
    if mode == "success":
        await _execute(entity, tool, call)
    else:
        with pytest.raises(OSError, match="write acknowledgement"):
            await _execute(entity, tool, call)
    expected = (
        "rejected"
        if mode == "rejected_write"
        else "uncertain"
        if mode == "unreadable"
        else "completed"
    )
    assert entity._execution_tool_outcomes[id(call)] == expected
    assert len(persisted["calls"]) == (mode != "rejected_write")
    remember_unacknowledged_calls(entity, log, set())
    equivalent = _call("retry", "effect", {**ALIAS, "delay": {"seconds": 30}})
    different = _call(
        "different", "effect", {**call.tool_args, "entity_id": "light.other"}
    )
    assert was_unacknowledged_equivalent(entity, log, equivalent) is (
        mode != "rejected_write"
    )
    assert was_unacknowledged_equivalent(entity, log, different) is (
        mode == "unreadable"
    )
    if mode in {"success", "committed_error"}:
        # The provider recovery guard stops the duplicate before it can queue.
        assert len(manager._records) == 1


@pytest.mark.parametrize("depth", [0, 1, 3])
@pytest.mark.parametrize("variable", ["previous", "list", "data"])
async def test_equivalent_multi_child_composite_cannot_repeat_effects(
    hass, monkeypatch, depth, variable
):
    _install_native_boundary(hass, monkeypatch)
    config = {
        "type": "composite",
        "sequence": [{**NATIVE, "response_variable": variable}, NATIVE],
    }
    for _ in range(depth):
        config = {"type": "composite", "sequence": [config, NATIVE]}
    entity, log, tool = _entity(hass), _chat_log(hass), _tool(config)
    # Concrete service_data wins over a later response named data. Both forms
    # dispatch the same effective target, despite their raw representation.
    raw = RAW if variable != "data" else {**RAW, "service_data": {}}
    call = _call("first", "effect", raw)
    _retain_calls(log, [call])
    await _execute(entity, tool, call)
    count = hass.services.async_call.await_count
    assert count == 2 + depth
    remember_unacknowledged_calls(entity, log, set())
    retry = _call("retry", "effect", ALIAS)
    assert was_unacknowledged_equivalent(entity, log, retry)

    async def execute(definition, tool_input, *_args):
        return await _execute(entity, definition, tool_input)

    entity._execute_function_tool = execute
    _retain_calls(log, [retry])
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await async_execute_tool_exchange(
            entity, log, [retry], [tool], FunctionCallBudget(10), None, []
        )
    assert not was_unacknowledged_equivalent(
        entity, log, _call("other", "effect", {**RAW, "entity_id": "light.other"})
    )
    assert hass.services.async_call.await_count == count


@pytest.mark.parametrize(
    "native_config,variable,arguments",
    [
        (NATIVE, "service_data", RAW),
        (NATIVE, "data", RAW),
        ({"type": "native", "name": "execute_service"}, "list", {"list": [RAW]}),
    ],
)
def test_composite_unknown_consumed_mapping_retains_raw_inputs(
    native_config, variable, arguments
):
    config = {
        "type": "composite",
        "sequence": [{**NATIVE, "response_variable": variable}, native_config],
    }
    before = deepcopy(arguments)
    # An unknown response mapping must not be converted into an invented empty
    # mapping or batch. Symbolic values stay confined to replay identity.
    result = execution_identity(config, arguments)
    child = result["composite"][1]
    assert child[variable].path == (0,)
    assert arguments == before


@pytest.mark.parametrize("variable", ["previous", "entity_id", "service_data"])
def test_composite_identity_preserves_raw_observers_and_response_scope(variable):
    config = {
        "type": "composite",
        "sequence": [
            {**NATIVE, "response_variable": variable},
            {"type": "template", "value_template": "{{ entity_id }}"},
        ],
    }
    before = deepcopy(RAW)
    assert execution_identity(config, RAW) != execution_identity(config, ALIAS)
    assert RAW == before
    # An inner variable never overwrites the parent's shared input.
    nested = {
        "type": "composite",
        "sequence": [
            {
                "type": "composite",
                "sequence": [{**NATIVE, "response_variable": "entity_id"}],
            },
            NATIVE,
        ],
    }
    assert execution_identity(nested, RAW) == execution_identity(nested, ALIAS)


async def test_cancellation_waits_for_queue_commit_and_retains_receipt(hass):
    manager = delayed_tools.DelayedToolManager(hass)
    manager._setup_complete = True
    entered, finish = asyncio.Event(), asyncio.Event()
    persisted = {}

    async def save(data):
        entered.set()
        await finish.wait()
        persisted.update(deepcopy(data))

    manager._store = SimpleNamespace(async_save=save)
    hass.data.setdefault(DOMAIN, {})[delayed_tools.DATA_DELAYED_TOOL_MANAGER] = manager
    entity, tool = _entity(hass), _tool()
    call = _call("first", "effect", {**RAW, "delay": {"seconds": 30}})
    task = asyncio.create_task(_execute(entity, tool, call))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(persisted["calls"]) == 1
    assert entity._execution_tool_outcomes[id(call)] == "completed"
