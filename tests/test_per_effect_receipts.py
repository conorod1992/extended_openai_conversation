"""Effect provenance, settlement and failed-continuation recovery invariants."""

import asyncio
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol

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
    effect_signature,
    remember_unacknowledged_calls,
    replay_safety,
    was_unacknowledged_equivalent,
)
from homeassistant.exceptions import HomeAssistantError
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
    assert queued.outcome == (
        "rejected"
        if mode == "rejected"
        else "uncertain"
        if mode == "unreadable"
        else "completed"
    )
    assert len(persisted["calls"]) == (mode != "rejected")
    if persisted["calls"]:
        saved = persisted["calls"][0]
        assert saved["arguments"] == RAW
        assert (
            datetime.fromisoformat(saved["due_at"])
            - datetime.fromisoformat(saved["created_at"])
        ).total_seconds() == 30


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


async def test_opaque_failure_cannot_reject_previous_unknown_child():
    from custom_components.extended_openai_conversation_responses.public_errors import (
        SanitizedUnexpectedBackendError,
    )

    entity, call = SimpleNamespace(), _call("first", "effect")
    function = SimpleNamespace(
        execute=AsyncMock(side_effect=ValueError("invalid child"))
    )
    with receipts.execution_attempt(entity, call, _tool()):
        unknown = receipts.execution_started()
        with pytest.raises(
            SanitizedUnexpectedBackendError,
            match="Check the tool configuration and permissions",
        ):
            await receipts.execute_backend(function, None, NATIVE, {}, None, [])
        assert unknown.outcome == "uncertain"
    assert [r.outcome for r in entity._execution_effect_receipts[id(call)]] == [
        "uncertain",
        "uncertain",
    ]
    function.execute.assert_awaited_once()


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
    assert [r.outcome for r in effects] == ["uncertain", "completed", "uncertain"]
    assert [r.child_path for r in effects] == [(0,), (1,), (2,)]
    assert effects[0].signature == effects[1].signature == effects[2].signature
    assert len({r.ordinal for r in effects}) == 3
    call.tool_args["list"][0]["entity_id"] = "light.changed"
    assert effects[0].signature == effects[1].signature
    assert hass.services.async_call.call_args.kwargs["service_data"] == {
        "entity_id": ["light.kitchen"]
    }


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
        pytest.raises(HomeAssistantError, match="not acknowledged"),
    ):
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
    equivalent = {"amount": 1.0, "data": {"1": "string", 1: "integer"}}
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
    assert effects[-1].kind == "backend"
    assert hass.services.async_call.call_args.kwargs["service_data"] == {
        "entity_id": ["light.kitchen"]
    }
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
    from custom_components.extended_openai_conversation_responses.ha_actions import (
        _async_call_ha_action_unchecked,
    )
    from homeassistant.core import HomeAssistant

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


async def test_opaque_operations_do_not_claim_equivalence_across_wrapper_contexts(hass):
    entity, log, effects = SimpleNamespace(), _chat_log(hass), []
    config = {"type": "opaque"}

    async def execute(hass, config, arguments, context, exposed):
        effects.append(context.device_id)

    backend = SimpleNamespace(execute=execute)
    first = _call("first", "first_wrapper", {})
    _retain_calls(log, [first])
    with receipts.execution_attempt(entity, first, _tool(config)):
        await receipts.execute_backend(
            backend, None, config, {}, SimpleNamespace(device_id="kitchen"), []
        )
    remember_unacknowledged_calls(entity, log, set())
    second = _call("second", "second_wrapper", {})
    with (
        bind_effect_replay_scope(entity, log.conversation_id),
        receipts.execution_attempt(entity, second, _tool(config)),
    ):
        await receipts.execute_backend(
            backend, None, config, {}, SimpleNamespace(device_id="bedroom"), []
        )
    assert effects == ["kitchen", "bedroom"]


@pytest.mark.parametrize("missing_identity", [None, ""])
def test_anonymous_scope_does_not_inherit_an_outer_conversation(missing_identity):
    from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
        check_effect_replay,
    )

    entity = SimpleNamespace()
    replay_safety(entity).effects = {"known": {"effect"}}
    with bind_effect_replay_scope(entity, "known"):
        with bind_effect_replay_scope(entity, missing_identity):
            check_effect_replay(entity, "effect")
        with pytest.raises(HomeAssistantError, match="not acknowledged"):
            check_effect_replay(entity, "effect")


@pytest.mark.parametrize("ai_task", [False, True])
async def test_shared_entity_boundary_owns_receipts_without_conversation_wrapper(
    hass, monkeypatch, ai_task
):
    from custom_components.extended_openai_conversation_responses.ai_task import (
        ExtendedOpenAITaskEntity,
    )
    from custom_components.extended_openai_conversation_responses.entity import (
        ExtendedOpenAIBaseLLMEntity,
    )

    _install_native_boundary(hass, monkeypatch)
    entity, call, tool = _entity(hass), _call("first", "effect", RAW), _tool()
    owner = ExtendedOpenAITaskEntity if ai_task else ExtendedOpenAIBaseLLMEntity
    await owner._execute_function_tool(entity, tool, call, None, [])
    assert entity._execution_tool_outcomes[id(call)] == "completed"
    assert len(entity._execution_effect_receipts[id(call)]) == 1


async def test_resolved_tool_copy_reuses_enclosing_invocation(hass, monkeypatch):
    from custom_components.extended_openai_conversation_responses.entity import (
        ExtendedOpenAIBaseLLMEntity,
    )

    _install_native_boundary(hass, monkeypatch)
    entity, call, tool = _entity(hass), _call("first", "effect", RAW), _tool()
    with receipts.execution_attempt(entity, call, tool):
        await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
            entity, deepcopy(tool), call, None, []
        )
    assert entity._execution_tool_outcomes[id(call)] == "completed"
    assert len(entity._execution_effect_receipts[id(call)]) == 1


async def test_background_task_does_not_retain_expired_replay_scope():
    prepared = {"target": "same"}
    entity = SimpleNamespace()
    replay_safety(entity).effects = {"request": {effect_signature("ha_service", prepared)}}
    release = asyncio.Event()
    call, effects = _call("background", "effect"), []

    async def background():
        await release.wait()
        with receipts.execution_attempt(entity, call, _tool()):
            handle = receipts.execution_started(kind="ha_service", prepared=prepared)
            effects.append(True)
            receipts.execution_completed(handle)

    with bind_effect_replay_scope(entity, "request"):
        task = asyncio.create_task(background())
    release.set()
    await task
    assert effects == [True]


async def test_late_callback_cannot_mutate_closed_invocation():
    entity, call, release = (
        SimpleNamespace(),
        _call("parent", "effect"),
        asyncio.Event(),
    )

    async def background(handle):
        await release.wait()
        receipts.execution_completed(handle)
        assert receipts.execution_started(kind="late") is None

    with receipts.execution_attempt(entity, call, _tool()):
        unknown = receipts.execution_started()
        task = asyncio.create_task(background(unknown))
    release.set()
    await task
    assert unknown.outcome == "uncertain"
    assert len(entity._execution_effect_receipts[id(call)]) == 1


async def test_admitted_delayed_work_detaches_even_while_parent_is_active(
    hass, monkeypatch
):
    from custom_components.extended_openai_conversation_responses.delayed_tools import (
        DelayedToolManager,
    )
    from custom_components.extended_openai_conversation_responses.entity import (
        ExtendedOpenAIBaseLLMEntity,
    )

    _install_native_boundary(hass, monkeypatch)
    entity, tool, seed, log = (
        _entity(hass),
        _tool(),
        _call("seed", "effect", RAW),
        _chat_log(hass),
    )
    _retain_calls(log, [seed])
    await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
        entity, tool, seed, None, []
    )
    remember_unacknowledged_calls(entity, log, set())
    manager = DelayedToolManager(hass)

    admitted = _call("admitted-job", "effect", RAW)

    async def due_worker(call_id):
        assert receipts.current_execution_receipt() is None
        assert call_id == admitted.id
        await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
            entity, tool, admitted, None, []
        )
        return False

    monkeypatch.setattr(manager, "_async_execute_due_detached", due_worker)
    manager._records["admitted-job"] = SimpleNamespace(
        status="pending", entry_id="entry", subentry_id="agent"
    )
    parent = _call("parent", "effect")
    with (
        bind_effect_replay_scope(entity, log.conversation_id),
        receipts.execution_attempt(entity, parent, tool),
    ):
        enclosing = receipts.execution_started(kind="parent")
        assert await manager._async_execute_due("admitted-job") is False
        assert enclosing.outcome == "uncertain"
    assert hass.services.async_call.await_count == 2
    assert entity._execution_tool_outcomes[id(parent)] == "uncertain"
    # Independent completion must not retain provider provenance or evict it.
    for name in (
        "_execution_tool_outcomes",
        "_execution_effect_receipts",
        "_dispatched_tool_inputs",
        "_dispatched_tool_signatures",
        "_dispatched_tool_conversations",
    ):
        assert id(admitted) not in getattr(entity, name, {})


async def test_genuine_service_identity_preserves_handler_visible_response_mode(
    tmp_path,
):
    from custom_components.extended_openai_conversation_responses.ha_actions import (
        _async_call_ha_action_unchecked,
    )
    from homeassistant.core import HomeAssistant, SupportsResponse

    hass = HomeAssistant(str(tmp_path))
    calls = []

    async def handler(call):
        calls.append(call.return_response)
        return {"acknowledged": True}

    hass.services.async_register(
        "receipt_probe", "apply", handler, supports_response=SupportsResponse.OPTIONAL
    )
    entity, tool = SimpleNamespace(), _tool()
    log = SimpleNamespace(conversation_id="response-mode", content=[])
    first = _call("first", "effect", {})
    from homeassistant.components import conversation

    log.content.append(
        conversation.AssistantContent(agent_id="conversation.probe", tool_calls=[first])
    )
    try:
        with receipts.execution_attempt(entity, first, tool):
            await _async_call_ha_action_unchecked(
                hass, "receipt_probe", "apply", blocking=True, include_response=True
            )
        remember_unacknowledged_calls(entity, log, set())
        second = _call("different-mode", "another_wrapper", {})
        with (
            bind_effect_replay_scope(entity, log.conversation_id),
            receipts.execution_attempt(entity, second, tool),
        ):
            await _async_call_ha_action_unchecked(
                hass, "receipt_probe", "apply", blocking=True, include_response=False
            )
        repeated = _call("same-mode", "third_wrapper", {})
        with (
            bind_effect_replay_scope(entity, log.conversation_id),
            receipts.execution_attempt(entity, repeated, tool),
            pytest.raises(HomeAssistantError, match="not acknowledged"),
        ):
            await _async_call_ha_action_unchecked(
                hass, "receipt_probe", "apply", blocking=True, include_response=True
            )
        assert calls == [True, False]
    finally:
        await hass.async_stop(force=True)


@pytest.mark.parametrize("source", ["local_rule", "delayed"])
@pytest.mark.parametrize("handler_failure", [False, True])
async def test_nested_provider_exchange_overrides_independent_ownership(
    tmp_path, monkeypatch, source, handler_failure
):
    from functools import partial

    from custom_components.extended_openai_conversation_responses.conversation import (
        ExtendedOpenAIAgentEntity,
    )
    from custom_components.extended_openai_conversation_responses.delayed_tools import (
        DelayedToolManager,
    )
    from custom_components.extended_openai_conversation_responses.entity import (
        ExtendedOpenAIBaseLLMEntity,
    )
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    hass = HomeAssistant(str(tmp_path))
    dr.async_setup(hass)
    await dr.async_load(hass, load_empty=True)
    await er.async_load(hass, load_empty=True)
    hass.states.async_set("sensor.probe", "ready")
    effects = []

    async def apply(call):
        effects.append(call.data["amount"])
        hass.states.async_set("sensor.probe", str(call.data["amount"]))
        if handler_failure:
            raise HomeAssistantError("handler failed after its effect")

    hass.services.async_register("probe", "apply", apply)
    exposed = [{"entity_id": "sensor.probe"}]
    tool = _tool()
    provider = SimpleNamespace(
        hass=hass,
        entity_id="conversation.provider",
        should_run_in_background=lambda delay: False,
    )
    provider._execute_function_tool = partial(
        ExtendedOpenAIBaseLLMEntity._execute_function_tool, provider
    )
    log = _chat_log(hass)
    arguments = {
        "domain": "probe",
        "service": "apply",
        "service_data": {"entity_id": ["sensor.probe"], "amount": 1},
    }
    first = _call("nested", "effect", arguments)
    _retain_calls(log, [first])

    async def nested_exchange(*_args):
        try:
            await async_execute_tool_exchange(
                provider, log, [first], [tool], FunctionCallBudget(10), None, exposed
            )
        except HomeAssistantError as err:
            # The nested conversation owns the error, as its request boundary
            # does when the local caller propagates Function execution failures.
            assert handler_failure
            assert (
                str(err)
                == "Function execution failed. Check the tool configuration and permissions."
            )

    try:
        if source == "local_rule":
            hass.services.async_register("bridge", "invoke", nested_exchange)
            bridge_tool = _tool()
            bridge_tool["spec"]["name"] = "bridge_function"
            local = SimpleNamespace(
                hass=hass,
                entity_id="conversation.local",
                subentry=SimpleNamespace(data={"function_groups": []}),
                should_run_in_background=lambda delay: False,
                _configured_function_tools_from_data=lambda data: [bridge_tool],
                _get_exposed_entities=lambda: exposed,
            )
            local._execute_function_tool = partial(
                ExtendedOpenAIBaseLLMEntity._execute_function_tool, local
            )
            await ExtendedOpenAIAgentEntity._async_execute_request_rule_function(
                local,
                "bridge_function",
                {"domain": "bridge", "service": "invoke", "entity_id": "sensor.probe"},
                None,
            )
            assert not getattr(local, "_execution_effect_receipts", {})
        else:
            manager = DelayedToolManager(hass)
            monkeypatch.setattr(manager, "_async_execute_due_detached", nested_exchange)
            manager._records["independent-job"] = SimpleNamespace(
                status="pending", entry_id="entry", subentry_id="agent"
            )
            await manager._async_execute_due("independent-job")

        remember_unacknowledged_calls(provider, log, set())
        retry = _call(
            "retry", "effect", {**arguments, "domain": "PROBE", "service": "APPLY"}
        )
        _retain_calls(log, [retry])
        # The later retry is an ordinary provider request, outside detached work.
        with pytest.raises(HomeAssistantError, match="not acknowledged"):
            await async_execute_tool_exchange(
                provider, log, [retry], [tool], FunctionCallBudget(10), None, exposed
            )
        assert effects == [1]
        assert hass.states.get("sensor.probe").state == "1"
        assert provider._dispatched_tool_conversations[id(first)] == log.conversation_id
        assert provider._execution_tool_outcomes[id(first)] == (
            "uncertain" if handler_failure else "completed"
        )
        clear_unacknowledged_calls(provider, log.conversation_id)
        assert not provider._execution_effect_receipts
    finally:
        await hass.async_stop(force=True)


async def test_rendered_bash_default_is_one_effect_during_recovery(tmp_path):
    from homeassistant.core import HomeAssistant
    from tests_real_ha.test_prepared_effect_recovery import exchange_owner

    hass = HomeAssistant(str(tmp_path))
    function = {
        "type": "composite",
        "sequence": [
            {
                "type": "bash",
                "command": "echo {{ value | default(1) }} >> audit-effects.txt",
                "cwd": str(tmp_path),
                "allow_unsafe_shell": True,
            }
        ],
    }
    entity, log, exchange = exchange_owner(hass, function)
    try:
        await exchange({})
        remember_unacknowledged_calls(entity, log, set())
        with pytest.raises(HomeAssistantError, match="not acknowledged"):
            await exchange({})
        with pytest.raises(HomeAssistantError, match="not acknowledged"):
            await exchange({"value": 1})
        assert [
            line.strip()
            for line in (tmp_path / "audit-effects.txt").read_text().splitlines()
        ] == ["1"]
        # A different prepared command is permitted, and acknowledgment allows repeats.
        await exchange({"value": 2})
        clear_unacknowledged_calls(entity, log.conversation_id)
        await exchange({"value": 1})
        assert [
            line.strip()
            for line in (tmp_path / "audit-effects.txt").read_text().splitlines()
        ] == ["1", "2", "1"]
    finally:
        await hass.async_stop(force=True)
