"""Replay evidence must outlive the conversation entity that observed an effect."""

import pytest

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context
from tests_real_ha.entity_service_probe import register_entity_service_probe
from tests_real_ha.test_provider_wire_e2e import (
    _agent,
    _chat_sse_text,
    _chat_sse_tool_call,
    _install_wire,
    _responses_sse_text,
    _responses_sse_tool_call,
)


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_failed_effect_cannot_replay_after_genuine_reload(
    hass, monkeypatch, mode, failure_kind="lost_ack"
):
    agent = await _agent(hass, mode)
    entry = agent.entry
    entity_id = "light.reload_replay"
    effects = []

    async def turn_off(call):
        effects.append(call.data["entity_id"])
        hass.states.async_set(entity_id, "off")

    register_entity_service_probe(hass, "light", "turn_off", turn_off)
    hass.states.async_set(entity_id, "on")
    async_expose_entity(hass, conversation.DOMAIN, entity_id, True)
    arguments = {
        "list": [
            {
                "domain": "light",
                "service": "turn_off",
                "service_data": {"entity_id": [entity_id]},
            }
        ]
    }

    def tool(call_id):
        return (
            _responses_sse_tool_call(call_id=call_id, tool_arguments=arguments)
            if mode == "responses"
            else _chat_sse_tool_call(call_id=call_id, arguments=arguments)
        )

    final = _responses_sse_text if mode == "responses" else _chat_sse_text
    if failure_kind == "output_budget":
        from custom_components.extended_openai_conversation_responses import (
            conversation as owner,
            provider_output_budget as limits,
        )
        from custom_components.extended_openai_conversation_responses.provider_errors import (
            ProviderStreamError,
        )

        failures = []
        render_error = owner._conversation_error_result

        def observe_error(*args, **kwargs):
            failures.append(args[3])
            return render_error(*args, **kwargs)

        monkeypatch.setattr(owner, "_conversation_error_result", observe_error)
        monkeypatch.setattr(limits, "MAX_ARGUMENT_CHARACTERS", 256)
        oversized = {"padding": "x" * 1024}
        continuation = (
            _responses_sse_tool_call(call_id="oversized", tool_arguments=oversized)
            if mode == "responses"
            else _chat_sse_tool_call(call_id="oversized", arguments=oversized)
        )
    else:
        continuation = (
            400,
            {
                "error": {
                    "message": "continuation failed",
                    "type": "invalid_request_error",
                }
            },
        )
    _install_wire(monkeypatch, agent, [tool("first-effect"), continuation])

    async def say(conversation_id=None):
        return await conversation.async_converse(
            hass=hass,
            text="Turn off the reload test light",
            conversation_id=conversation_id,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )

    failed = await say()
    assert failed.response.error_code is not None
    if failure_kind == "output_budget":
        assert len(failures) == 1
        assert isinstance(failures[0], ProviderStreamError)
        assert failures[0].type == "output_limit_exceeded"
    assert failed.conversation_id
    assert len(effects) == 1
    assert hass.states.get(entity_id).state == "off"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    replacement = conversation.async_get_agent(hass, entry.entry_id)
    assert replacement is not agent
    wire = _install_wire(
        monkeypatch, replacement, [tool("new-provider-call-id"), final("Checked")]
    )
    resumed = await say(failed.conversation_id)
    assert resumed.conversation_id == failed.conversation_id
    assert wire.requests
    assert len(effects) == 1, "reload must not permit a second external effect"
    assert hass.states.get(entity_id).state == "off"

    # A deliberate successful turn retires ambiguity, including after another reload.
    _install_wire(monkeypatch, replacement, [final("Reviewed")])
    acknowledged = await say(failed.conversation_id)
    assert acknowledged.response.error_code is None
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    replacement = conversation.async_get_agent(hass, entry.entry_id)
    _install_wire(monkeypatch, replacement, [tool("legitimate-repeat"), final("Done")])
    assert (await say(failed.conversation_id)).response.error_code is None
    assert len(effects) == 2
    _install_wire(monkeypatch, replacement, [tool("new-conversation"), final("Done")])
    assert (await say()).response.error_code is None
    assert len(effects) == 3


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_reload_oracle_detects_entity_owned_state(hass, monkeypatch, mode):
    from custom_components.extended_openai_conversation_responses import (
        conversation as owner,
    )
    from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
        ReplaySafetyState,
    )

    monkeypatch.setattr(
        owner, "get_agent_replay_safety", lambda *_: ReplaySafetyState()
    )
    with pytest.raises(
        AssertionError, match="reload must not permit a second external effect"
    ):
        await test_failed_effect_cannot_replay_after_genuine_reload(
            hass, monkeypatch, mode
        )


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_budget_failure_preserves_effect_through_retry_and_reload(
    hass, monkeypatch, mode
):
    await test_failed_effect_cannot_replay_after_genuine_reload(
        hass, monkeypatch, mode, failure_kind="output_budget"
    )
