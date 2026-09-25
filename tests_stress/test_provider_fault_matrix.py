"""Nightly provider transport and status failures through genuine HA Assist."""

from __future__ import annotations

from copy import deepcopy
import random

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_ARCHIVE_ENABLED,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
    DEFAULT_CONF_FUNCTION_TOOLS,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _responses_sse_text
from tests_stress.conftest import record
from tests_stress.provider_fault_transport import ProviderFaultTransport, WireStep

_OWNER = "provider-fault-owner"
_FAULTS = (
    "dns",
    "refused",
    "connect_timeout",
    "read_timeout",
    "tls",
    "before_headers",
    "401",
    "403",
    "429",
    "500",
    "502",
    "503",
)


async def _agent(hass: HomeAssistant, api_mode: str, *, tools: bool = False):
    options = {
        CONF_API_MODE: api_mode,
        CONF_CHAT_MODEL: "gpt-5.6",
        CONF_ARCHIVE_ENABLED: True,
    }
    if tools:
        options[CONF_FUNCTION_TOOLS] = [deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])]
    entry = _make_entry(
        f"Nightly provider fault {api_mode}",
        include_ai_task=False,
        conversation_options=options,
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    assert agent._usage is not None
    assert agent._archive is not None
    return agent


async def _say(hass: HomeAssistant, agent, text: str):
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(user_id=_OWNER),
        language="en",
        agent_id=agent.entry.entry_id,
    )


@pytest.mark.parametrize("api_mode", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
@pytest.mark.parametrize("fault", _FAULTS)
async def test_provider_failure_accounts_once_and_next_assist_turn_recovers(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_seed: int,
    stress_trace: list[dict],
    api_mode: str,
    fault: str,
) -> None:
    """A failed wire request cannot poison the loaded agent or private archive."""
    MockUser(id=_OWNER, name="Provider fault owner", is_owner=True).add_to_hass(hass)
    agent = await _agent(hass, api_mode)
    reply = (
        _chat_sse_text("Recovered after provider fault.")
        if api_mode == API_MODE_CHAT_COMPLETIONS
        else _responses_sse_text("Recovered after provider fault.")
    )
    rng = random.Random(f"{stress_seed}:{api_mode}:{fault}")
    delay = rng.choice((0.0, 0.02, 0.05))
    first = (
        WireStep("http", status=int(fault), delay=delay)
        if fault.isdigit()
        else WireStep("transport", error=fault, delay=delay)
    )
    wire = ProviderFaultTransport([first, WireStep("sse", body=reply)])
    wire.install(monkeypatch, agent)

    failed = await _say(hass, agent, f"Fault case {fault}")
    assert failed.response.error_code is not None
    assert failed.response.as_dict()["speech"]["plain"]["speech"]
    assert agent._usage.totals.conversation_count == 1
    assert agent._usage.totals.failed_request_count == 1
    assert agent._usage.runs[0].successful is False
    assert agent._archive.stats()["turn_count"] == 0

    recovered = await _say(hass, agent, f"Recovery case {fault}")
    assert recovered.response.error_code is None
    assert (
        recovered.response.as_dict()["speech"]["plain"]["speech"]
        == "Recovered after provider fault."
    )
    assert agent._usage.totals.conversation_count == 2
    assert agent._usage.totals.api_request_count == 2
    assert agent._usage.totals.successful_request_count == 1
    assert agent._usage.totals.failed_request_count == 1
    assert agent._archive.stats()["turn_count"] == 1
    assert len(wire.requests) == 2
    assert not wire.steps
    record(
        stress_trace,
        "provider_fault",
        mode=api_mode,
        fault=fault,
        delay=delay,
        wire_paths=[request["path"] for request in wire.requests],
    )
