"""Concurrent provider limits and slow responses through public HA Assist."""

from __future__ import annotations

import asyncio
from collections import Counter
import json

import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
)
from homeassistant.core import HomeAssistant
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _management_call,
)
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _prepare_service,
    _raw_client,
    _responses_sse_text,
    _responses_sse_tool_call,
    _speech,
)
from tests_stress.conftest import record
from tests_stress.test_provider_valid_chunking import _agent, _say

_OWNER = "chunk-boundary-owner"


def _reply(mode: str, text: str) -> bytes:
    return (
        _responses_sse_text(text)
        if mode == API_MODE_RESPONSES
        else _chat_sse_text(text)
    )


def _marker(request: httpx.Request, markers: tuple[str, ...]) -> str:
    body = json.loads(request.content)
    serialized = json.dumps(body)
    found = [marker for marker in markers if marker in serialized]
    assert len(found) == 1, found
    return found[0]


@pytest.mark.parametrize("mode", [API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS])
async def test_concurrent_429_burst_has_bounded_independent_sdk_retries(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
    mode: str,
) -> None:
    """EOAI currently inherits the SDK's bounded retry policy, including Retry-After."""
    MockUser(id=_OWNER, name="Backpressure owner", is_owner=True).add_to_hass(hass)
    agents = [await _agent(hass, mode) for _ in range(2)]
    raw = _raw_client(agents[0])
    assert raw._client is _raw_client(agents[1])._client
    retries = raw.max_retries
    assert retries == 2, "update the bounded attempt assertion if SDK policy changes"
    markers = tuple(f"Burst-{index}" for index in range(4))
    attempts: Counter[str] = Counter()
    first_arrivals = 0
    burst_ready = asyncio.Event()

    async def send(request: httpx.Request, *args, **kwargs) -> httpx.Response:
        nonlocal first_arrivals
        del args, kwargs
        marker = _marker(request, markers)
        attempts[marker] += 1
        if attempts[marker] == 1:
            first_arrivals += 1
            if first_arrivals == len(markers):
                burst_ready.set()
            await burst_ready.wait()
            return httpx.Response(
                429,
                headers={"content-type": "application/json", "retry-after": "0"},
                json={
                    "error": {
                        "message": "bounded rate limit",
                        "type": "rate_limit_error",
                    }
                },
                request=request,
            )
        assert attempts[marker] == 2, marker
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_reply(mode, f"Recovered {marker}"),
            request=request,
        )

    monkeypatch.setattr(raw._client, "send", send)
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                _say(hass, agents[index % 2], marker)
                for index, marker in enumerate(markers)
            )
        ),
        timeout=30,
    )
    assert [_speech(result) for result in results] == [
        f"Recovered {marker}" for marker in markers
    ]
    assert attempts == Counter({marker: 2 for marker in markers})
    for agent in agents:
        assert agent._usage.totals.conversation_count == 2
        assert agent._usage.totals.failed_request_count == 0
        assert (
            len(
                [
                    turn
                    for turns in agent._archive._turns.values()
                    for turn in turns
                    if turn.successful
                ]
            )
            == 2
        )
    record(
        stress_trace,
        "concurrent_429",
        mode=mode,
        conversations=4,
        agents=2,
        attempts=dict(attempts),
    )


@pytest.mark.parametrize("mode", [API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS])
async def test_slow_provider_saturation_isolates_mixed_failure_and_success(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
    hass_ws_client,
    mode: str,
) -> None:
    """Three gated provider calls cannot globally block a fourth or local HA work."""
    MockUser(id=_OWNER, name="Backpressure owner", is_owner=True).add_to_hass(hass)
    agents = [await _agent(hass, mode) for _ in range(2)]
    raw = _raw_client(agents[0])
    markers = ("Slow-0", "Slow-1", "Slow-2", "Fast", "RateLimited", "Healthy")
    entered = {marker: asyncio.Event() for marker in markers[:3]}
    release = asyncio.Event()
    attempts: Counter[str] = Counter()
    service_calls = await _prepare_service(hass)

    async def send(request: httpx.Request, *args, **kwargs) -> httpx.Response:
        del args, kwargs
        marker = _marker(request, markers)
        attempts[marker] += 1
        if marker in entered:
            entered[marker].set()
            await release.wait()
        if marker == "RateLimited":
            return httpx.Response(
                429,
                headers={"content-type": "application/json", "retry-after": "0"},
                json={"error": {"message": "rate limit", "type": "rate_limit_error"}},
                request=request,
            )
        if marker == "Fast" and attempts[marker] == 1:
            payload = (
                _responses_sse_tool_call()
                if mode == API_MODE_RESPONSES
                else _chat_sse_tool_call()
            )
        else:
            payload = _reply(mode, marker)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=payload,
            request=request,
        )

    monkeypatch.setattr(raw._client, "send", send)
    blocked = [
        asyncio.create_task(_say(hass, agents[index % 2], marker))
        for index, marker in enumerate(markers[:3])
    ]
    try:
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in entered.values())), timeout=10
        )
        assert all(not task.done() for task in blocked)
        fast = await asyncio.wait_for(_say(hass, agents[1], "Fast"), timeout=10)
        assert _speech(fast) == "Fast"
        assert len(service_calls) == 1
        # A genuine management WebSocket read and local HA state work remain live.
        admin = await _admin_client(hass, hass_ws_client)
        configuration = await asyncio.wait_for(
            _management_call(
                admin, entry=agents[1].entry, section="configuration", action="get"
            ),
            timeout=10,
        )
        assert configuration["config"]
        hass.states.async_set("sensor.backpressure_probe", "ready")
        assert hass.states.get("sensor.backpressure_probe").state == "ready"
        failure = await asyncio.wait_for(
            _say(hass, agents[1], "RateLimited"), timeout=30
        )
        assert failure.response.error_code is not None
        assert attempts["RateLimited"] == raw.max_retries + 1
        assert all(not task.done() for task in blocked)
    finally:
        release.set()
    settled = await asyncio.wait_for(asyncio.gather(*blocked), timeout=15)
    assert [_speech(result) for result in settled] == list(markers[:3])
    healthy = await asyncio.wait_for(_say(hass, agents[1], "Healthy"), timeout=10)
    assert _speech(healthy) == "Healthy"
    assert all(attempts[marker] == 1 for marker in (*markers[:3], "Healthy"))
    assert attempts["Fast"] == 2
    assert len(service_calls) == 1
    assert agents[1]._usage.totals.failed_request_count == 1
    assert agents[0]._usage.totals.failed_request_count == 0
    record(
        stress_trace,
        "slow_provider_saturation",
        mode=mode,
        gated=3,
        attempts=dict(attempts),
    )


@pytest.mark.parametrize("mode", [API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS])
async def test_seeded_public_concurrency_records_realised_barrier_schedule(
    hass, monkeypatch, stress_seed, stress_scale, stress_trace, mode
):
    """Independent requests obey a generated release order with native cancellation."""
    import random

    MockUser(id=_OWNER, name="Schedule owner", is_owner=True).add_to_hass(hass)
    agent = await _agent(hass, mode)
    raw = _raw_client(agent)
    rng = random.Random(stress_seed)
    realised = []
    for witness in range(3 + 3 * stress_scale):
        markers = tuple(f"schedule-{witness}-{index}" for index in range(4))
        entered = {marker: asyncio.Event() for marker in markers}
        releases = {marker: asyncio.Event() for marker in markers}
        outcomes = dict(
            zip(markers, ("success", "failure", "cancel", "success"), strict=True)
        )
        order = list(markers)
        rng.shuffle(order)

        async def send(request, *_args, **_kwargs):
            marker = _marker(request, markers)
            realised.append({"event": "entered", "marker": marker, "witness": witness})
            entered[marker].set()
            await releases[marker].wait()
            if outcomes[marker] == "failure":
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": "controlled rejected request",
                            "type": "invalid_request_error",
                        }
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_reply(mode, marker),
                request=request,
            )

        monkeypatch.setattr(raw._client, "send", send)
        pending = {
            marker: asyncio.create_task(_say(hass, agent, marker)) for marker in markers
        }
        try:
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in entered.values())), 10
            )
            assert all(not task.done() for task in pending.values())
            for marker in order:
                outcome = outcomes[marker]
                realised.append(
                    {
                        "event": "release",
                        "marker": marker,
                        "outcome": outcome,
                        "witness": witness,
                    }
                )
                if outcome == "cancel":
                    pending[marker].cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await pending[marker]
                else:
                    releases[marker].set()
                    result = await asyncio.wait_for(pending[marker], 10)
                    if outcome == "failure":
                        assert result.response.error_code is not None
                    else:
                        assert _speech(result) == marker
                realised.append(
                    {"event": "settled", "marker": marker, "witness": witness}
                )
                assert all(
                    not pending[other].done()
                    for other in order[order.index(marker) + 1 :]
                )
            assert [
                row["marker"]
                for row in realised
                if row["event"] == "settled" and row["witness"] == witness
            ] == order
        finally:
            for event in releases.values():
                event.set()
            for task in pending.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending.values(), return_exceptions=True)
    assert sum(row["event"] == "settled" for row in realised) == 4 * (
        3 + 3 * stress_scale
    )
    record(
        stress_trace,
        "summary",
        generated_public_schedule_cases=3 + 3 * stress_scale,
        seed=stress_seed,
        api=mode,
        realised_schedule=realised,
    )
    assert await hass.config_entries.async_unload(agent.entry.entry_id)
