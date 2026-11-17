"""Sampling edits and damaged saved values cross genuine HA/SDK boundaries."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from tests_real_ha.test_cross_feature_acceptance import _agent
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _responses_sse_text,
)


async def _converse(hass, agent):
    return await conversation.async_converse(
        hass=hass,
        text="Check sampling",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=agent.entry.entry_id,
    )


@pytest.mark.parametrize("parameter", ["temperature", "top_p"])
@pytest.mark.parametrize("value", [True, False])
async def test_sampling_editor_rejects_boolean_and_accepts_correction(
    hass, monkeypatch, parameter, value
):
    agent = await _agent(hass, chat_model="gpt-4.1")
    owner = await hass.auth.async_create_user(
        "Sampling owner", group_ids=["system-admin"]
    )
    base = {
        "section": "configuration",
        "entry_id": agent.entry.entry_id,
        "subentry_id": agent.subentry.subentry_id,
    }
    snapshot = await async_management_command(
        hass, owner.id, True, {**base, "action": "get"}
    )
    original = deepcopy(dict(agent.subentry.data))
    update = {**base, "action": "update", "revision": snapshot["revision"]}
    with pytest.raises(AgentConfigError, match=rf"{parameter}:.*int or float"):
        await async_management_command(
            hass, owner.id, True, {**update, "config": {parameter: value}}
        )
    assert dict(agent.subentry.data) == original

    await async_management_command(
        hass, owner.id, True, {**update, "config": {parameter: "0.25"}}
    )
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, agent.entry.entry_id)
    assert agent.subentry.data[parameter] == 0.25
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text("Healthy")])
    result = await _converse(hass, agent)
    assert result.response.error_code is None
    assert len(wire.requests) == 1
    assert wire.requests[0]["body"][parameter] == 0.25
    assert type(wire.requests[0]["body"][parameter]) is float


@pytest.mark.parametrize("parameter", ["temperature", "top_p"])
@pytest.mark.parametrize("value", [True, False])
@pytest.mark.parametrize("api", ["chat_completions", "responses"])
async def test_persisted_boolean_sampling_fails_before_transport(
    hass, monkeypatch, parameter, value, api
):
    agent = await _agent(hass, chat_model="gpt-4.1", api_mode=api, **{parameter: value})
    original = deepcopy(dict(agent.subentry.data))
    wire = _install_wire(monkeypatch, agent, [])
    result = await _converse(hass, agent)
    assert result.response.error_code is not None
    speech = result.response.as_dict()["speech"]["plain"]["speech"]
    assert f"{parameter}: must be a int or float" in speech
    assert not wire.requests
    assert dict(agent.subentry.data) == original


@pytest.mark.parametrize("api", ["chat_completions", "responses"])
@pytest.mark.parametrize("value", [0, 0.0, "0.25", None])
async def test_sampling_numeric_or_null_values_reach_expected_wire_payload(
    hass, monkeypatch, value, api
):
    agent = await _agent(
        hass, chat_model="gpt-4.1", api_mode=api, temperature=value, top_p=value
    )
    reply = _chat_sse_text if api == "chat_completions" else _responses_sse_text
    wire = _install_wire(monkeypatch, agent, [reply("Healthy")])
    result = await _converse(hass, agent)
    assert result.response.error_code is None
    assert len(wire.requests) == 1
    for parameter in ("temperature", "top_p"):
        if value is None:
            assert parameter not in wire.requests[0]["body"]
        else:
            expected = float(value) if isinstance(value, str) else value
            assert wire.requests[0]["body"][parameter] == expected
            assert type(wire.requests[0]["body"][parameter]) is type(expected)
