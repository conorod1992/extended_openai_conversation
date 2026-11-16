"""Reject ambiguous indirect IDs at execution, preserving dispatch contracts."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import ha_actions
from custom_components.extended_openai_conversation_responses.execution_arguments import (
    prepare_execution_arguments,
)
from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
)
from custom_components.extended_openai_conversation_responses.functions.script import (
    _AuthorizedScriptServices,
)
from custom_components.extended_openai_conversation_responses.indirect_targets import (
    validate_indirect_targets,
)
from homeassistant.exceptions import HomeAssistantError

KEYS = ("area_id", "device_id", "floor_id", "label_id")
BAD = (
    "kitchen,hall",
    " kitchen",
    "kitchen ",
    False,
    0,
    ["kitchen", "hall,room"],
    ["kitchen", " "],
    [" kitchen"],
    [None],
)
EMPTY = (None, [], "", " \t", " , ", ["", " , "])


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("bad", BAD)
@pytest.mark.parametrize("entrypoint", ["native", "action", "script"])
async def test_malformed_indirect_cannot_hide_beside_explicit_entity(
    hass, key, bad, entrypoint
):
    data = {"entity_id": ["light.kitchen"], key: bad}
    original = deepcopy(data)
    with pytest.raises(HomeAssistantError, match="list of exact IDs"):
        if entrypoint == "native":
            await NativeFunction().execute_service_single(
                hass,
                {},
                {"domain": "test", "service": "record", "data": data},
                None,
                [{"entity_id": "light.kitchen"}],
            )
        elif entrypoint == "script":
            await _AuthorizedScriptServices(hass, None, []).async_call(
                "test", "record", service_data=data
            )
        else:
            await ha_actions.async_call_ha_action(hass, "test", "record", data=data)
    assert data == original
    hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("override", [*EMPTY, "hall", ["hall", "hall", "kitchen"]])
async def test_effective_target_overrides_data_even_when_empty(
    hass, monkeypatch, key, override
):
    monkeypatch.setattr(
        ha_actions.target_helpers,
        "async_extract_referenced_entity_ids",
        lambda *_: SimpleNamespace(referenced=set(), indirectly_referenced=set()),
    )
    data = {key: "bad, data", "nested": {key: "unrelated, payload"}}
    target = {key: override}
    original = deepcopy((data, target))
    await ha_actions.async_call_ha_action(
        hass, "test", "record", data=data, target=target
    )
    assert (data, target) == original
    kwargs = hass.services.async_call.call_args.kwargs
    assert kwargs["service_data"] == data
    assert kwargs["target"] == target
    with pytest.raises(HomeAssistantError):
        validate_indirect_targets({key: "hall"}, {key: "bad, target"})


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("empty", EMPTY)
@pytest.mark.parametrize("entrypoint", ["native", "action", "script"])
async def test_empty_alternative_preserves_valid_entity_and_raw_payload(
    hass, key, empty, entrypoint
):
    data = {"entity_id": ["light.kitchen"], key: empty}
    original = deepcopy(data)
    if entrypoint == "native":
        result = await NativeFunction().execute_service_single(
            hass,
            {},
            {"domain": "test", "service": "record", "service_data": data},
            None,
            [{"entity_id": "light.kitchen"}],
        )
        assert result["success"]
    elif entrypoint == "script":
        await _AuthorizedScriptServices(hass, None, []).async_call(
            "test", "record", service_data=data
        )
    else:
        await ha_actions.async_call_ha_action(hass, "test", "record", data=data)
    assert data == original
    hass.services.async_call.assert_awaited_once()
    assert hass.services.async_call.call_args.kwargs["service_data"] == original


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("empty", EMPTY)
async def test_empty_alternative_does_not_add_presence_requirement_to_trusted_action(
    hass, key, empty
):
    data = {key: empty}
    await ha_actions.async_call_ha_action(hass, "test", "record", data=data)
    hass.services.async_call.assert_awaited_once()
    assert hass.services.async_call.call_args.kwargs["service_data"] == data


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("value", ["kitchen", ["hall", "kitchen", "hall"], [], None])
async def test_valid_native_payload_preserves_shapes_order_and_alias_precedence(
    hass, monkeypatch, key, value
):
    monkeypatch.setattr(
        ha_actions.target_helpers,
        "async_extract_referenced_entity_ids",
        lambda *_: SimpleNamespace(
            referenced={"light.kitchen"}, indirectly_referenced=set()
        ),
    )
    data = {"entity_id": ["light.kitchen"], key: value, "nested": {key: "leave, alone"}}
    result = await NativeFunction().execute_service_single(
        hass,
        {},
        {
            "domain": "test",
            "service": "record",
            "service_data": data,
            "data": {key: "ignored,invalid"},
        },
        None,
        [{"entity_id": "light.kitchen"}],
    )
    assert result["success"]
    assert hass.services.async_call.call_args.kwargs["service_data"] == data


async def test_eager_preparation_does_not_validate_later_batch_children(
    hass, monkeypatch
):
    from custom_components.extended_openai_conversation_responses import (
        execution_outcomes as receipts,
    )
    from tests.test_function_tool_error_recovery import _call

    monkeypatch.setattr(
        ha_actions.target_helpers,
        "async_extract_referenced_entity_ids",
        lambda *_: SimpleNamespace(
            referenced={"light.kitchen"}, indirectly_referenced=set()
        ),
    )
    config = {"type": "native", "name": "execute_service"}
    first = {
        "domain": "test",
        "service": "record",
        "data": {"entity_id": "light.kitchen"},
    }
    second = {
        **first,
        "data": {"entity_id": "light.kitchen", "area_id": "kitchen,hall"},
    }
    prepared = prepare_execution_arguments(config, {"list": [first, second, first]})
    entity, call = SimpleNamespace(), _call("batch", "record", prepared)
    with (
        receipts.execution_attempt(entity, call, {"function": config}),
        pytest.raises(HomeAssistantError, match="list of exact IDs"),
    ):
        await receipts.execute_backend(
            NativeFunction(),
            hass,
            config,
            prepared,
            None,
            [{"entity_id": "light.kitchen"}],
        )
    assert hass.services.async_call.await_count == 1
    effects = entity._execution_effect_receipts[id(call)]
    assert effects[0].child_path == (0,)
    assert effects[0].outcome == "completed"
    assert all(effect.outcome != "uncertain" for effect in effects)
    # A corrected retry reaches execution; failed validation created no effect.
    hass.services.async_call.reset_mock()
    corrected = {
        **second,
        "data": {"entity_id": "light.kitchen", "area_id": ["kitchen", "hall"]},
    }
    assert (
        await NativeFunction().execute_service_single(
            hass, {}, corrected, None, [{"entity_id": "light.kitchen"}]
        )
    )["success"]
    hass.services.async_call.assert_awaited_once()


async def test_composite_keeps_earlier_effect_before_invalid_batch_child(
    hass, monkeypatch
):
    from custom_components.extended_openai_conversation_responses import (
        execution_outcomes as receipts,
    )
    from custom_components.extended_openai_conversation_responses.functions.composite import (
        CompositeFunction,
    )
    from tests.test_function_tool_error_recovery import _call

    monkeypatch.setattr(
        ha_actions.target_helpers,
        "async_extract_referenced_entity_ids",
        lambda *_: SimpleNamespace(
            referenced={"light.kitchen"}, indirectly_referenced=set()
        ),
    )
    config = {
        "type": "composite",
        "sequence": [
            {"type": "native", "name": "execute_service_single"},
            {"type": "native", "name": "execute_service"},
            {"type": "native", "name": "execute_service_single"},
        ],
    }
    arguments = {
        "domain": "test",
        "service": "record",
        "entity_id": "light.kitchen",
        "list": [
            {
                "domain": "test",
                "service": "record",
                "data": {
                    "entity_id": "light.kitchen",
                    "label_id": ["good", "bad,pair"],
                },
            }
        ],
    }
    entity, call = SimpleNamespace(), _call("composite", "record", arguments)
    with (
        receipts.execution_attempt(entity, call, {"function": config}),
        pytest.raises(HomeAssistantError, match="list of exact IDs"),
    ):
        await receipts.execute_backend(
            CompositeFunction(),
            hass,
            config,
            arguments,
            None,
            [{"entity_id": "light.kitchen"}],
        )
    hass.services.async_call.assert_awaited_once()
    effects = entity._execution_effect_receipts[id(call)]
    assert [(effect.child_path, effect.outcome) for effect in effects] == [
        ((0,), "completed"),
        ((1,), "rejected"),
    ]
    assert entity._execution_tool_outcomes[id(call)] == "partial"


async def test_rejected_selector_does_not_block_corrected_replay(hass, monkeypatch):
    from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
        async_was_unacknowledged_equivalent,
        remember_unacknowledged_calls,
    )
    from tests.test_function_tool_error_recovery import _call, _chat_log, _retain_calls
    from tests.test_review_replay_execution import (
        _entity,
        _execute,
        _install_native_boundary,
        _tool,
    )

    _install_native_boundary(hass, monkeypatch)
    entity, log, tool = _entity(hass), _chat_log(hass), _tool()
    args = {
        "domain": "test",
        "service": "record",
        "data": {"entity_id": "light.kitchen", "area_id": "kitchen,hall"},
    }
    failed = _call("invalid", "effect", args)
    _retain_calls(log, [failed])
    result = await _execute(entity, tool, failed)
    assert "list of exact IDs" in str(result)
    assert entity._execution_tool_outcomes[id(failed)] == "rejected"
    assert all(
        effect.outcome == "rejected"
        for effect in entity._execution_effect_receipts[id(failed)]
    )
    hass.services.async_call.assert_not_awaited()
    remember_unacknowledged_calls(entity, log, set())
    corrected = _call(
        "corrected",
        "effect",
        {
            **args,
            "data": {"entity_id": "light.kitchen", "area_id": ["kitchen", "hall"]},
        },
    )
    assert not await async_was_unacknowledged_equivalent(entity, log, corrected)
    await _execute(entity, tool, corrected)
    hass.services.async_call.assert_awaited_once()
