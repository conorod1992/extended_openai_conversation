"""Receipt resources and normalized operations through genuine HA execution."""

import gc
import sys
import tracemalloc

import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses import (
    execution_outcomes as outcomes,
    tool_replay_guard as guard,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    EffectReplayBlocked,
)
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from tests_real_ha.test_cross_feature_acceptance import _agent, _say
from tests_real_ha.test_execution_result_lifecycle_contracts import _arguments, _tool
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _install_wire,
)
from tests_real_ha.test_service_effect_identity import _args, _service_owner


@pytest.mark.parametrize("children", [32, 100])
async def test_large_unused_arguments_are_not_retained_per_composite_child(
    hass, monkeypatch, children
):
    config = {
        "type": "composite",
        "sequence": [
            {"type": "template", "value_template": "small completed child"}
            for _ in range(children)
        ]
        + [{"type": "native", "name": "execute_service_single"}],
    }
    agent = await _agent(
        hass,
        **{CONF_FUNCTION_TOOLS: [_tool(config)]},
        function_tool_error_recovery=True,
    )
    assert await async_setup_component(
        hass, "counter", {"counter": {"receipt": {"initial": 0}}}
    )
    await hass.async_block_till_done()
    async_expose_entity(hass, "conversation", "counter.receipt", True)
    call, text = _chat_sse_tool_call, _chat_sse_text
    arguments = {
        **_arguments(),
        "unrelated": [{"value": index} for index in range(2000)],
    }
    _install_wire(monkeypatch, agent, [text("Ready")])
    seed = await _say(hass, agent, "hello")
    _install_wire(
        monkeypatch,
        agent,
        [
            call("many", "effect", arguments),
            (
                400,
                {
                    "error": {
                        "message": "continuation failed",
                        "type": "invalid_request_error",
                    }
                },
            ),
        ],
    )
    gc.collect()
    tracemalloc.start()
    try:
        first = await _say(hass, agent, "execute", seed.conversation_id)
        assert first.response.error_code is not None
        assert hass.states.get("counter.receipt").state == "1"
        gc.collect()
        retained = tracemalloc.get_traced_memory()[0]
        assert retained < 8 * 1024 * 1024, retained
        # A real successful provider turn must release the invocation records.
        _install_wire(monkeypatch, agent, [text("Acknowledged")])
        resolved = await _say(hass, agent, "acknowledge", first.conversation_id)
        assert resolved.response.error_code is None
        gc.collect()
        assert tracemalloc.get_traced_memory()[0] < 8 * 1024 * 1024
        assert not agent._execution_effect_receipts
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize("container", [set, frozenset])
async def test_equivalent_unordered_preparation_does_not_repeat_actual_effect(
    hass, container
):
    first = [1, 1 + sys.hash_info.modulus, 1 + 2 * sys.hash_info.modulus]
    retry = list(reversed(first))
    assert container(first) == container(retry)
    assert str(container(first)) != str(container(retry))  # Exercise the old fallback.
    entity, log, exchange, effects, _ = await _service_owner(
        hass,
        vol.Schema(
            {"amount": vol.All([int], vol.Coerce(container)), "entity_id": [str]}
        ),
    )
    await exchange(_args(first))
    guard.remember_unacknowledged_calls(entity, log, set())
    assert effects == [(container(first), container)]
    blocked = False
    try:
        await exchange(_args(retry), name="other")
    except EffectReplayBlocked:
        blocked = True
    assert effects == [(container(first), container)]
    assert blocked
    await exchange(_args([*first, 2]), name="other")
    assert len(effects) == 2  # A genuinely different set remains a distinct effect.


async def test_unsupported_prepared_objects_require_acknowledgment(hass):
    class Opaque:
        def __init__(self, value):
            self.value = value

        def __str__(self):
            return "identical text is not an operation identity"

    entity, log, exchange, effects, _ = await _service_owner(
        hass, vol.Schema({"amount": vol.Coerce(Opaque), "entity_id": [str]})
    )
    await exchange(_args(1))
    guard.remember_unacknowledged_calls(entity, log, set())
    with pytest.raises(EffectReplayBlocked):
        await exchange(_args(2), name="other")
    assert [item.value for item, _ in effects] == [1]
    guard.clear_unacknowledged_calls(entity, log.conversation_id)
    await exchange(_args(2), name="other")
    assert [item.value for item, _ in effects] == [1, 2]


async def test_total_receipt_record_eviction_refuses_actual_replay(hass, monkeypatch):
    from homeassistant.components import conversation

    monkeypatch.setattr(outcomes, "_MAX_RETAINED_EFFECT_RECEIPTS", 3)
    entity, _, exchange, effects, _ = await _service_owner(
        hass, vol.Schema({"amount": int, "entity_id": [str]})
    )
    logs = []
    for index in range(4):
        log = conversation.ChatLog(hass, f"receipt-budget-{index}")
        await exchange(_args(index), chat_log=log)
        guard.remember_unacknowledged_calls(entity, log, set())
        logs.append(log)
    assert len(effects) == 4
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await exchange(_args(0), name="other", chat_log=logs[0])
    assert len(effects) == 4
    # The newest precise history still protects its actual effect too.
    with pytest.raises(EffectReplayBlocked):
        await exchange(_args(3), name="other", chat_log=logs[3])
    guard.clear_unacknowledged_calls(entity, logs[0].conversation_id)
    await exchange(_args(0), name="other", chat_log=logs[0])
    assert len(effects) == 5
