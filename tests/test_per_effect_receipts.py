"""Effect provenance, settlement and failed-continuation recovery invariants."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import (
    execution_outcomes as receipts,
)
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    ToolRecoveryState,
)
from custom_components.extended_openai_conversation_responses.persistence_hardening import (
    _async_settle_transactional_save,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    async_execute_tool_exchange,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    bind_effect_replay_scope,
    clear_unacknowledged_calls,
    remember_unacknowledged_calls,
    was_unacknowledged_equivalent,
    effect_signature,
)
from tests.test_function_tool_error_recovery import _call, _chat_log, _retain_calls
from tests.test_review_replay_execution import (
    ALIAS,
    NATIVE,
    RAW,
    _entity,
    _execute,
    _install_native_boundary,
    _tool,
)


@pytest.mark.parametrize(
    "mode", ["success", "committed_error", "rejected", "unreadable"]
)
async def test_queue_settlement_owns_one_receipt_and_preserves_earlier_unknown(
    hass, mode
):
    from custom_components.extended_openai_conversation_responses.delayed_tools import (
        DelayedToolManager,
    )

    manager = DelayedToolManager(hass)
    manager._setup_complete = True
    persisted = {"calls": []}

    async def save(data):
        if mode != "rejected":
            persisted.update(deepcopy(data))
        if mode != "success":
            raise OSError("lost acknowledgement")

    async def load():
        if mode == "unreadable":
            raise OSError("read failed")
        return deepcopy(persisted)

    manager._store = SimpleNamespace(async_save=save, async_load=load)
    entity, call, tool = _entity(hass), _call("first", "effect", RAW), _tool()
    with receipts.execution_attempt(entity, call, tool):
        earlier = receipts.execution_started(kind="earlier", prepared={})
        try:
            await manager.async_schedule(
                entity,
                "effect",
                RAW,
                None,
                execution_delay={"seconds": 30},
                function_tool=tool,
            )
        except OSError:
            assert mode != "success"
        assert earlier.outcome == "uncertain"
    effects = entity._execution_effect_receipts[id(call)]
    assert len(effects) == 2  # One earlier effect and exactly one queue transaction.
    queued = effects[1]
    assert queued.kind == "persistence"
    assert queued.prepared["operation"] == "schedule"
    assert queued.prepared["delay_seconds"] == 30
    assert queued.outcome == (
        "rejected"
        if mode == "rejected"
        else "uncertain"
        if mode == "unreadable"
        else "completed"
    )
    assert len(persisted["calls"]) == (mode != "rejected")


@pytest.mark.parametrize(
    "settlements,expected",
    [
        (["rejected"], "rejected"),
        (["completed"], "completed"),
        (["uncertain", "completed", "rejected"], "uncertain"),
        (["completed", "rejected"], "partial"),
    ],
)
def test_receipts_settle_independently_out_of_order(settlements, expected):
    entity, call = SimpleNamespace(), _call("first", "effect", RAW)
    with receipts.execution_attempt(entity, call, _tool()):
        handles = [
            receipts.execution_started(kind="probe", prepared={"index": i})
            for i in range(len(settlements))
        ]
        for handle, outcome in reversed(list(zip(handles, settlements, strict=True))):
            if outcome == "completed":
                receipts.execution_completed(handle)
            elif outcome == "rejected":
                receipts.execution_rejected(handle)
        assert [handle.outcome for handle in handles] == settlements
    assert entity._execution_tool_outcomes[id(call)] == expected
    assert [handle.ordinal for handle in handles] == list(range(len(handles)))
    assert len({handle.invocation_id for handle in handles}) == 1


def test_settled_and_foreign_receipts_cannot_be_changed():
    entity, first, second = (
        SimpleNamespace(),
        _call("first", "effect"),
        _call("second", "effect"),
    )
    with receipts.execution_attempt(entity, first, _tool()):
        completed = receipts.execution_started()
        receipts.execution_completed(completed)
        receipts.execution_completed(completed)
        receipts.execution_rejected(completed)
        unknown = receipts.execution_started()
    with receipts.execution_attempt(entity, second, _tool()):
        current = receipts.execution_started()
        receipts.execution_completed(unknown)
        receipts.execution_rejected(unknown)
        assert current.outcome == unknown.outcome == "uncertain"
        receipts.execution_completed(current)
    assert completed.outcome == "completed"
    assert entity._execution_tool_outcomes[id(first)] == "uncertain"
    assert entity._execution_tool_outcomes[id(second)] == "completed"


async def test_validation_failure_cannot_reject_previous_unknown_child():
    entity, call = SimpleNamespace(), _call("first", "effect")
    function = SimpleNamespace(
        execute=AsyncMock(side_effect=ValueError("invalid child"))
    )
    with receipts.execution_attempt(entity, call, _tool()):
        unknown = receipts.execution_started()
        with pytest.raises(ValueError, match="invalid child"):
            await receipts.execute_backend(function, None, NATIVE, {}, None, [])
        assert unknown.outcome == "uncertain"
    assert [r.outcome for r in entity._execution_effect_receipts[id(call)]] == [
        "uncertain",
        "rejected",
    ]


async def test_real_native_batch_records_each_effect_and_prepared_target(
    hass, monkeypatch
):
    _install_native_boundary(hass, monkeypatch)
    hass.services.async_call.side_effect = [
        HomeAssistantError("unknown"),
        None,
        vol.Invalid("reject"),
    ]
    entity, tool = _entity(hass), _tool({"type": "native", "name": "execute_service"})
    call = _call("batch", "effect", deepcopy({"list": [RAW, ALIAS, RAW]}))
    await _execute(entity, tool, call)
    effects = entity._execution_effect_receipts[id(call)]
    assert [r.outcome for r in effects] == ["uncertain", "completed", "rejected"]
    assert [r.child_path for r in effects] == [(0,), (1,), (2,)]
    assert all(r.prepared["data"] == {"entity_id": ["light.kitchen"]} for r in effects)
    assert effects[0].signature == effects[1].signature == effects[2].signature
    assert len({r.ordinal for r in effects}) == 3
    call.tool_args["list"][0]["entity_id"] = "light.changed"
    assert effects[0].prepared["data"]["entity_id"] == ["light.kitchen"]


@pytest.mark.parametrize("recovery", [False, True])
async def test_actual_effect_guard_closes_composite_raw_observer_gap(
    hass, monkeypatch, recovery
):
    _install_native_boundary(hass, monkeypatch)
    from custom_components.extended_openai_conversation_responses.functions.template import (
        TemplateFunction,
    )

    monkeypatch.setattr(
        TemplateFunction, "validate_entity_ids", lambda *args, **kwargs: None
    )
    # A later opaque observer legitimately distinguishes the raw representations,
    # so the whole-composite identity must differ. Its earlier native effect does not.
    config = {
        "type": "composite",
        "sequence": [
            NATIVE,
            {"type": "template", "value_template": "{{ service_data | default({}) }}"},
        ],
    }
    entity, log, tool = _entity(hass), _chat_log(hass), _tool(config)
    first = _call("first", "effect", RAW)
    _retain_calls(log, [first])
    await _execute(entity, tool, first)
    remember_unacknowledged_calls(entity, log, set())
    retry = _call("retry", "effect", ALIAS)
    assert not was_unacknowledged_equivalent(entity, log, retry)

    async def execute(definition, call, *_):
        return await _execute(entity, definition, call)

    entity._execute_function_tool = execute
    _retain_calls(log, [retry])
    blocked = False
    try:
        await async_execute_tool_exchange(
            entity,
            log,
            [retry],
            [tool],
            FunctionCallBudget(10),
            None,
            [],
            recovery_state=ToolRecoveryState(enabled=recovery),
        )
    except HomeAssistantError as err:
        assert "not acknowledged" in str(err)
        blocked = True
    assert hass.services.async_call.await_count == 1
    assert blocked
    # A successful provider turn deliberately resolves the recovery scope.
    clear_unacknowledged_calls(entity, log.conversation_id)
    await _execute(entity, tool, _call("new-turn", "effect", ALIAS))
    assert hass.services.async_call.await_count == 2


async def test_nested_repeated_actions_keep_distinct_occurrences(hass, monkeypatch):
    _install_native_boundary(hass, monkeypatch)
    config = {
        "type": "composite",
        "sequence": [NATIVE, {"type": "composite", "sequence": [NATIVE, NATIVE]}],
    }
    entity, call = _entity(hass), _call("first", "effect", RAW)
    await _execute(entity, _tool(config), call)
    effects = entity._execution_effect_receipts[id(call)]
    assert len(effects) == hass.services.async_call.await_count == 3
    assert [r.child_path for r in effects] == [(0,), (1, 0), (1, 1)]
    assert len({r.signature for r in effects}) == 1


async def test_effect_recovery_isolated_between_conversations_and_entities(
    hass, monkeypatch
):
    _install_native_boundary(hass, monkeypatch)
    entity, log, tool = _entity(hass), _chat_log(hass), _tool()
    first = _call("first", "effect", RAW)
    _retain_calls(log, [first])
    await _execute(entity, tool, first)
    remember_unacknowledged_calls(entity, log, set())
    other = _entity(hass)
    with bind_effect_replay_scope(entity, "another-conversation"):
        await _execute(entity, tool, _call("other-conversation", "effect", RAW))
    with bind_effect_replay_scope(entity, log.conversation_id):
        await _execute(other, tool, _call("other-entity", "effect", RAW))
    assert hass.services.async_call.await_count == 3


async def test_concurrent_invocations_do_not_share_receipt_ownership():
    entity, entered, finish = SimpleNamespace(), asyncio.Event(), asyncio.Event()
    calls = [_call("one", "effect"), _call("two", "effect")]

    async def execute(call, fail):
        with receipts.execution_attempt(entity, call, _tool()):
            handle = receipts.execution_started()
            if fail:
                entered.set()
                await finish.wait()
            else:
                await entered.wait()
                receipts.execution_completed(handle)
                finish.set()

    await asyncio.gather(execute(calls[0], True), execute(calls[1], False))
    assert [entity._execution_tool_outcomes[id(call)] for call in calls] == [
        "uncertain",
        "completed",
    ]
    assert (
        entity._execution_effect_receipts[id(calls[0])][0].invocation_id
        != entity._execution_effect_receipts[id(calls[1])][0].invocation_id
    )


async def test_persistence_replay_refusal_restores_ram_without_starting_save(hass):
    entity, log, tool = SimpleNamespace(), _chat_log(hass), _tool()
    first = _call("first", "effect", RAW)
    _retain_calls(log, [first])
    with receipts.execution_attempt(entity, first, tool):
        handle = receipts.execution_started(kind="persistence")
        receipts.execution_completed(handle)
    remember_unacknowledged_calls(entity, log, set())
    state, writes = {"generation": "candidate"}, []

    async def save():
        writes.append(True)

    def rollback():
        state["generation"] = "committed"

    retry = _call("retry", "effect", RAW)
    pending = save()
    with (
        bind_effect_replay_scope(entity, log.conversation_id),
        receipts.execution_attempt(entity, retry, tool),
    ):
        with pytest.raises(HomeAssistantError, match="not acknowledged"):
            await _async_settle_transactional_save(
                pending, rollback, lambda: None, None, lambda: None
            )
    assert pending.cr_frame is None
    assert state == {"generation": "committed"}
    assert writes == []


async def test_internal_persistence_does_not_settle_enclosing_effect():
    entity, call = SimpleNamespace(), _call("first", "effect", RAW)
    with receipts.execution_attempt(entity, call, _tool()):
        outer = receipts.execution_started(kind="outer")
        await _async_settle_transactional_save(
            AsyncMock()(),
            lambda: None,
            lambda: None,
            None,
            lambda: None,
            record_execution=False,
        )
        assert outer.outcome == "uncertain"
    assert len(entity._execution_effect_receipts[id(call)]) == 1


def test_prepared_identity_supports_typed_mapping_keys_without_mutation():
    first = {"data": {1: "integer", "1": "string"}, "amount": 1.0}
    equivalent = {"amount": 1, "data": {"1": "string", 1: "integer"}}
    original = deepcopy(first)
    assert effect_signature("ha_service", first) == effect_signature(
        "ha_service", equivalent
    )
    assert effect_signature("ha_service", {"data": {1: "x"}}) != effect_signature(
        "ha_service", {"data": {"1": "x"}}
    )
    assert effect_signature("ha_service", {"mapping": [["x", 1]]}) != effect_signature(
        "ha_service", {"x": 1}
    )
    assert first == original
    assert effect_signature("ha_service", {"data": (1, 2)}) != effect_signature(
        "ha_service", {"data": [1, 2]}
    )


async def test_response_variable_consumer_records_executed_inputs(hass, monkeypatch):
    _install_native_boundary(hass, monkeypatch)
    from custom_components.extended_openai_conversation_responses.functions.template import (
        TemplateFunction,
    )

    observed = []

    async def producer(self, hass, config, arguments, context, exposed):
        observed.append(arguments["noise"])
        return {"entity_id": [arguments["target"]]}

    monkeypatch.setattr(TemplateFunction, "execute", producer)
    config = {
        "type": "composite",
        "sequence": [
            {
                "type": "template",
                "value_template": "{{ {'entity_id': [target]} }}",
                "parse_result": True,
                "response_variable": "service_data",
            },
            NATIVE,
        ],
    }
    entity, tool, log = _entity(hass), _tool(config), _chat_log(hass)
    arguments = {
        "domain": "light",
        "service": "toggle",
        "target": "light.kitchen",
        "noise": 1,
    }
    first = _call("first", "effect", deepcopy(arguments))
    _retain_calls(log, [first])
    await _execute(entity, tool, first)
    effects = entity._execution_effect_receipts[id(first)]
    assert effects[-1].prepared["data"] == {"entity_id": ["light.kitchen"]}
    assert first.tool_args == arguments
    remember_unacknowledged_calls(entity, log, set())
    retry = _call("retry", "effect", {**arguments, "noise": 2})
    assert not was_unacknowledged_equivalent(entity, log, retry)
    with (
        bind_effect_replay_scope(entity, log.conversation_id),
        pytest.raises(HomeAssistantError, match="not acknowledged"),
    ):
        await _execute(entity, tool, retry)
    assert hass.services.async_call.await_count == 1
    assert observed == [1, 2]


def test_receipt_ceiling_refuses_effects_and_bounds_rejection_markers(monkeypatch):
    monkeypatch.setattr(receipts, "_MAX_EFFECT_RECEIPTS", 2)
    entity, call, dispatched = SimpleNamespace(), _call("first", "effect"), []
    with receipts.execution_attempt(entity, call, _tool()):
        for index in range(10):
            try:
                handle = receipts.execution_started(
                    kind="probe", prepared={"index": index}
                )
            except HomeAssistantError:
                receipts.execution_rejected(before_dispatch=True)
            else:
                dispatched.append(index)
                receipts.execution_completed(handle)
    assert dispatched == [0, 1]
    assert [r.outcome for r in entity._execution_effect_receipts[id(call)]] == [
        "completed",
        "completed",
        "rejected",
    ]
    assert entity._execution_tool_outcomes[id(call)] == "partial"


@pytest.mark.parametrize("committed", [False, True])
async def test_queue_readback_cannot_settle_an_interleaved_effect(hass, committed):
    from custom_components.extended_openai_conversation_responses.delayed_tools import (
        DelayedToolManager,
    )

    manager = DelayedToolManager(hass)
    manager._setup_complete = True
    entered, finish = asyncio.Event(), asyncio.Event()
    persisted = {"calls": []}

    async def save(data):
        entered.set()
        await finish.wait()
        if committed:
            persisted.update(deepcopy(data))
        raise OSError("lost acknowledgement")

    manager._store = SimpleNamespace(
        async_save=save, async_load=AsyncMock(side_effect=lambda: deepcopy(persisted))
    )
    entity, call, tool = _entity(hass), _call("first", "effect", RAW), _tool()
    with receipts.execution_attempt(entity, call, tool):
        task = asyncio.create_task(
            manager.async_schedule(
                entity,
                "effect",
                RAW,
                None,
                execution_delay={"seconds": 30},
                function_tool=tool,
            )
        )
        await entered.wait()
        unrelated = receipts.execution_started(
            kind="unrelated", prepared={"effect": "unknown"}
        )
        finish.set()
        with pytest.raises(OSError, match="lost acknowledgement"):
            await task
        assert unrelated.outcome == "uncertain"
    effects = entity._execution_effect_receipts[id(call)]
    assert [(r.kind, r.outcome) for r in effects] == [
        ("persistence", "completed" if committed else "rejected"),
        ("unrelated", "uncertain"),
    ]


@pytest.mark.parametrize("handler_failure", [False, True])
async def test_genuine_service_provenance_distinguishes_same_exception_type(
    tmp_path, handler_failure
):
    from homeassistant.core import HomeAssistant
    from custom_components.extended_openai_conversation_responses.ha_actions import (
        _async_call_ha_action_unchecked,
    )

    hass = HomeAssistant(str(tmp_path))
    calls = []

    async def handler(call):
        calls.append(call.data["amount"])
        raise vol.Invalid("handler failed after its effect")

    hass.services.async_register(
        "receipt_probe",
        "apply",
        handler,
        schema=vol.Schema({vol.Required("amount"): int}),
    )
    entity, call = SimpleNamespace(), _call("first", "effect", RAW)
    try:
        with (
            receipts.execution_attempt(entity, call, _tool()),
            pytest.raises(HomeAssistantError),
        ):
            await _async_call_ha_action_unchecked(
                hass,
                "receipt_probe",
                "apply",
                data={"amount": 1 if handler_failure else "invalid"},
                blocking=True,
            )
        assert calls == ([1] if handler_failure else [])
        assert entity._execution_tool_outcomes[id(call)] == (
            "uncertain" if handler_failure else "rejected"
        )
    finally:
        await hass.async_stop(force=True)
