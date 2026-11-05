"""Real-HA usage accounting across failed turns and subsequent recovery."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import usage
from homeassistant.components import conversation
from tests_real_ha.test_entry_point_contract_matrix import _management

from custom_components.extended_openai_conversation_responses.const import (
    CONF_CONVERSATION_CONTINUITY,
    CONVERSATION_CONTINUITY_DEVICE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.intent import IntentResponseErrorCode

from tests_real_ha.test_cross_feature_acceptance import (
    _agent,
    _provider,
    _say,
    _speech,
)

_DEVICE_ID = "usage-accounting-failure-device"


def _assert_recovered_totals(
    usage: Any,
    *,
    successful_requests: int,
    failed_requests: int,
) -> None:
    """Assert exact aggregate counts after one failed and one recovered turn."""
    assert usage.totals.conversation_count == 2
    assert usage.totals.api_request_count == 2
    assert usage.totals.successful_request_count == successful_requests
    assert usage.totals.failed_request_count == failed_requests
    assert len(usage.requests) == 2
    assert len(usage.runs) == 2


async def test_provider_transport_failure_accounts_once_then_next_turn_recovers(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """A transport failure must finalize one failed request/run without poisoning the next turn."""
    agent = await _agent(
        hass,
        **{CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_DEVICE},
    )
    usage = agent._usage
    assert usage is not None
    assert usage.totals.conversation_count == 0
    assert usage.totals.api_request_count == 0
    assert usage.requests == []
    assert usage.runs == []

    failed_provider_calls: list[dict[str, Any]] = []

    async def fail_create(**kwargs: Any) -> Any:
        failed_provider_calls.append(deepcopy(kwargs))
        raise ConnectionError("acceptance transport failure")

    monkeypatch.setattr(agent._client.chat.completions, "create", fail_create)

    failed = await _say(
        hass,
        agent,
        "This provider request should fail once.",
        device_id=_DEVICE_ID,
    )
    assert failed.response.error_code is IntentResponseErrorCode.UNKNOWN
    assert len(failed_provider_calls) == 1

    # The provider-request boundary records the raw ConnectionError exactly once.
    # The enclosing conversation boundary deliberately normalizes it to the
    # OpenAI-compatible ProviderTransportError before marking the run failed.
    assert usage.totals.conversation_count == 1
    assert usage.totals.api_request_count == 1
    assert usage.totals.successful_request_count == 0
    assert usage.totals.failed_request_count == 1
    assert len(usage.requests) == 1
    assert len(usage.runs) == 1

    failed_request = usage.requests[0]
    failed_run = usage.runs[0]
    assert failed_request.run_id == failed_run.run_id
    assert failed_request.successful is False
    assert failed_request.error_type == "ConnectionError"
    assert failed_request.request_stage == "initial"
    assert failed_request.tool_calls_requested == 0
    assert failed_run.successful is False
    assert failed_run.error_type == "ProviderTransportError"
    assert failed_run.request_count == 1
    assert failed_run.successful_request_count == 0
    assert failed_run.failed_request_count == 1
    assert failed_run.tool_call_count == 0
    assert failed_run.completed_at is not None

    sent = _provider(monkeypatch, agent, ["Usage accounting recovered cleanly."])
    recovered = await _say(
        hass,
        agent,
        "Retry after the provider failure.",
        conversation_id=failed.conversation_id,
        device_id=_DEVICE_ID,
    )
    assert _speech(recovered) == "Usage accounting recovered cleanly."
    assert len(sent) == 1

    _assert_recovered_totals(
        usage,
        successful_requests=1,
        failed_requests=1,
    )
    recovered_request = usage.requests[1]
    recovered_run = usage.runs[1]
    assert recovered_run.run_id != failed_run.run_id
    assert recovered_request.run_id == recovered_run.run_id
    assert recovered_request.successful is True
    assert recovered_request.error_type is None
    assert recovered_request.request_stage == "initial"
    assert recovered_run.successful is True
    assert recovered_run.error_type is None
    assert recovered_run.request_count == 1
    assert recovered_run.successful_request_count == 1
    assert recovered_run.failed_request_count == 0
    assert recovered_run.tool_call_count == 0
    assert recovered_run.completed_at is not None
    assert usage.latest_run is recovered_run

    # The successful retry must not rewrite the already-finalized failure record.
    assert usage.requests[0] is failed_request
    assert usage.runs[0] is failed_run
    assert failed_request.error_type == "ConnectionError"
    assert failed_run.error_type == "ProviderTransportError"


async def test_post_provider_tool_failure_keeps_request_success_separate_from_run_failure(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """A successful API request followed by tool resolution failure must not become a failed API request."""
    agent = await _agent(
        hass,
        **{CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_DEVICE},
    )
    usage = agent._usage
    assert usage is not None

    invented_call = {
        "index": 0,
        "id": "call-usage-invented-tool",
        "type": "function",
        "function": {
            "name": "provider_invented_unadvertised_tool",
            "arguments": "{}",
        },
    }
    failed_wire = _provider(monkeypatch, agent, [invented_call])
    failed = await _say(
        hass,
        agent,
        "Return a tool call that cannot be resolved.",
        device_id=_DEVICE_ID,
    )
    assert failed.response.error_code is IntentResponseErrorCode.UNKNOWN
    assert len(failed_wire) == 1

    # The provider request itself completed successfully and asked for one tool.
    # The overall turn then failed at the local Function Tool resolution boundary.
    assert usage.totals.conversation_count == 1
    assert usage.totals.api_request_count == 1
    assert usage.totals.successful_request_count == 1
    assert usage.totals.failed_request_count == 0
    assert len(usage.requests) == 1
    assert len(usage.runs) == 1

    provider_request = usage.requests[0]
    failed_run = usage.runs[0]
    assert provider_request.run_id == failed_run.run_id
    assert provider_request.successful is True
    assert provider_request.error_type is None
    assert provider_request.request_stage == "initial"
    assert provider_request.tool_calls_requested == 1
    assert failed_run.successful is False
    assert failed_run.error_type == "FunctionNotFound"
    assert failed_run.request_count == 1
    assert failed_run.successful_request_count == 1
    assert failed_run.failed_request_count == 0
    assert failed_run.tool_call_count == 1
    assert failed_run.completed_at is not None

    recovered_wire = _provider(
        monkeypatch,
        agent,
        ["Tool-failure accounting recovered cleanly."],
    )
    recovered = await _say(
        hass,
        agent,
        "Try another ordinary turn after the tool failure.",
        conversation_id=failed.conversation_id,
        device_id=_DEVICE_ID,
    )
    assert _speech(recovered) == "Tool-failure accounting recovered cleanly."
    assert len(recovered_wire) == 1

    _assert_recovered_totals(
        usage,
        successful_requests=2,
        failed_requests=0,
    )
    recovered_request = usage.requests[1]
    recovered_run = usage.runs[1]
    assert recovered_run.run_id != failed_run.run_id
    assert recovered_request.run_id == recovered_run.run_id
    assert recovered_request.successful is True
    assert recovered_request.error_type is None
    assert recovered_request.tool_calls_requested == 0
    assert recovered_run.successful is True
    assert recovered_run.error_type is None
    assert recovered_run.request_count == 1
    assert recovered_run.successful_request_count == 1
    assert recovered_run.failed_request_count == 0
    assert recovered_run.tool_call_count == 0
    assert recovered_run.completed_at is not None
    assert usage.latest_run is recovered_run

    # Recovery must not retroactively classify the first API request as failed.
    assert usage.requests[0] is provider_request
    assert usage.runs[0] is failed_run
    assert provider_request.successful is True
    assert provider_request.error_type is None
    assert failed_run.successful is False
    assert failed_run.error_type == "FunctionNotFound"


@pytest.mark.parametrize("days", [0, 30])
async def test_recovered_storage_policy_updates_live_volatile_manager(
    hass, monkeypatch, days
):
    native_load = usage._UsageStore.async_load

    async def fail_load(_store):
        raise OSError("transient Usage load failure")

    monkeypatch.setattr(usage._UsageStore, "async_load", fail_load)
    agent = await _agent(
        hass,
        usage_request_retention_days=30 if days == 0 else 0,
        usage_run_retention_days=30 if days == 0 else 0,
    )
    fallback = agent._usage
    assert (
        fallback
        is hass.data[usage._VOLATILE_USAGE_MANAGERS][
            (agent.entry.entry_id, agent.subentry.subentry_id)
        ]
    )
    monkeypatch.setattr(usage._UsageStore, "async_load", native_load)
    _provider(monkeypatch, agent, ["First reply"])
    first = await _say(hass, agent, "First turn")
    assert _speech(first) == "First reply"
    if fallback._prune_task:
        await fallback._prune_task
    command = await _management(hass, agent)
    configuration = await command("get")
    await command(
        "save",
        config={
            **configuration["config"],
            "usage_request_retention_days": days,
            "usage_run_retention_days": days,
        },
        revision=configuration["revision"],
    )
    assert fallback.request_retention_days == fallback.run_retention_days == days
    assert fallback.requests == [] and fallback.runs == []
    durable = await usage.async_get_durable_usage(
        hass, agent.entry.entry_id, agent.subentry.subentry_id
    )
    assert durable is not fallback
    await hass.async_block_till_done(wait_background_tasks=True)
    agent = conversation.async_get_agent(hass, agent.entry.entry_id)
    _provider(monkeypatch, agent, ["Second reply"])
    second = await _say(hass, agent, "Next turn", first.conversation_id)
    assert _speech(second) == "Second reply"
    assert agent._usage is fallback
    assert len(fallback.requests) == len(fallback.runs) == (0 if days == 0 else 1)
    assert fallback.totals.api_request_count == 2
    assert await hass.config_entries.async_unload(agent.entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
