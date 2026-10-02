"""Nightly speech streaming through the public Home Assistant Assist pipeline."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import random
from typing import Any

import httpx
import pytest

from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.setup import async_setup_component
from tests_real_ha.test_assist_streaming_speech_processing import (
    _chat_sse_deltas,
    _final_speech,
    _progressive_text,
    _run_assist,
    _speech_agent,
)
from tests_real_ha.test_provider_wire_e2e import _install_wire, _raw_client
from tests_stress.conftest import record


@pytest.mark.asyncio
async def test_seeded_assist_speech_streams_remain_isolated(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    stress_seed: int,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    """Many fragmented real Assist streams retain exact progressive speech."""
    agent = await _speech_agent(hass)
    assert await async_setup_component(hass, "assist_pipeline", {})
    rng = random.Random(stress_seed ^ 0x5EEEC4)
    count = 8 if stress_scale == 1 else 24
    expected: list[str] = []
    replies: list[bytes] = []
    for index in range(count):
        word = f"Voice{index}_{rng.randrange(100000)}"
        expected.append(f"{word} see and done.")
        replies.append(
            _chat_sse_deltas(
                [
                    f"**{word}** see ht",
                    f"tps://example.com/{index} and ",
                    "done.",
                ]
            )
        )
    wire = _install_wire(monkeypatch, agent, replies)
    client = await hass_ws_client(hass)
    for index, spoken in enumerate(expected):
        events = await _run_assist(
            client,
            pipeline_id=agent.entity_id,
            conversation_id=f"nightly-speech-{stress_seed}-{index}",
        )
        assert _progressive_text(events) == spoken
        assert _final_speech(events) == spoken
        assert "http" not in spoken
        if index:
            assert expected[index - 1] not in _progressive_text(events)
    assert len(wire.requests) == count
    assert all(request["body"]["stream"] is True for request in wire.requests)
    record(
        stress_trace,
        "summary",
        layer="Real HA Assist and provider wire",
        assist_speech_streams=count,
        provider_requests=len(wire.requests),
    )


class _GatedSpeechStream(httpx.AsyncByteStream):
    """Pause a real HTTPX SSE stream after its first provider fragment."""

    def __init__(self, first: bytes, rest: bytes) -> None:
        self.first = first
        self.rest = rest
        self.first_delivered = asyncio.Event()
        self.release = asyncio.Event()

    async def __aiter__(self):
        yield self.first
        self.first_delivered.set()
        await self.release.wait()
        yield self.rest


@pytest.mark.asyncio
async def test_cancelled_fragment_cannot_leak_into_next_assist_stream(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    """A cancelled provider stream leaves the next public Assist turn clean."""
    agent = await _speech_agent(hass)
    assert await async_setup_component(hass, "assist_pipeline", {})
    raw = _chat_sse_deltas(["**Stale** ht", "tps://example.com/old done."])
    split = raw.index(b"\n\n") + 2
    stream = _GatedSpeechStream(raw[:split], raw[split:])
    requests: list[httpx.Request] = []

    async def send(request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
        del args, kwargs
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=stream,
            request=request,
        )

    monkeypatch.setattr(_raw_client(agent)._client, "send", send)
    cancelled = asyncio.create_task(
        conversation.async_converse(
            hass=hass,
            text="Begin a stream that will be cancelled",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=agent.entry.entry_id,
        )
    )
    try:
        await asyncio.wait_for(stream.first_delivered.wait(), timeout=10)
        cancelled.cancel()
    finally:
        stream.release.set()
        with suppress(asyncio.CancelledError):
            await cancelled
    assert cancelled.done()
    assert len(requests) == 1

    wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_deltas(["**Fresh** see ht", "tps://example.com/new and done."])],
    )
    client = await hass_ws_client(hass)
    events = await _run_assist(
        client,
        pipeline_id=agent.entity_id,
        conversation_id="nightly-speech-after-cancellation",
    )
    assert _progressive_text(events) == "Fresh see and done."
    assert _final_speech(events) == "Fresh see and done."
    assert "Stale" not in str(events)
    assert len(wire.requests) == 1
    record(
        stress_trace,
        "summary",
        layer="Real HA Assist and provider wire",
        cancelled_speech_streams=1,
        recovered_speech_streams=1,
    )


@pytest.mark.asyncio
async def test_unload_during_fragmented_stream_recovers_clean_speech(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    """A streaming response cannot survive unload into a recreated agent."""
    old_agent = await _speech_agent(hass)
    entry_id = old_agent.entry.entry_id
    assert await async_setup_component(hass, "assist_pipeline", {})
    raw = _chat_sse_deltas(["**Old** see ht", "tps://example.com/old done."])
    split = raw.index(b"\n\n") + 2
    stream = _GatedSpeechStream(raw[:split], raw[split:])

    async def send(request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
        del args, kwargs
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=stream,
            request=request,
        )

    monkeypatch.setattr(_raw_client(old_agent)._client, "send", send)
    active = asyncio.create_task(
        conversation.async_converse(
            hass=hass,
            text="Begin speech before integration unload",
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry_id,
        )
    )
    try:
        await asyncio.wait_for(stream.first_delivered.wait(), timeout=10)
        assert await asyncio.wait_for(
            hass.config_entries.async_unload(entry_id), timeout=10
        )
        assert conversation.async_get_agent(hass, entry_id) is None
    finally:
        active.cancel()
        stream.release.set()
        with suppress(asyncio.CancelledError):
            await active

    assert await hass.config_entries.async_setup(entry_id)
    await hass.async_block_till_done()
    new_agent = conversation.async_get_agent(hass, entry_id)
    assert new_agent is not None and new_agent is not old_agent
    wire = _install_wire(
        monkeypatch,
        new_agent,
        [_chat_sse_deltas(["**New** see ht", "tps://example.com/new done."])],
    )
    client = await hass_ws_client(hass)
    events = await _run_assist(
        client,
        pipeline_id=new_agent.entity_id,
        conversation_id="nightly-speech-after-unload",
    )
    assert _progressive_text(events) == "New see done."
    assert _final_speech(events) == "New see done."
    assert "Old" not in str(events)
    assert len(wire.requests) == 1
    record(
        stress_trace,
        "summary",
        layer="Real HA Assist and provider wire",
        interrupted_speech_unloads=1,
        recovered_speech_streams=1,
    )


async def test_concurrent_distinct_matcher_workers_settle_and_keep_ha_responsive(
    hass, monkeypatch, stress_trace
):
    """Bounded automata, executor regex, and async speech regex own separate work."""
    import threading
    from custom_components.extended_openai_conversation_responses import (
        regex_execution,
        request_rule_patterns,
    )
    from custom_components.extended_openai_conversation_responses.function_execution import (
        async_validate_function_arguments,
    )
    from custom_components.extended_openai_conversation_responses.management_ui import (
        async_management_command,
    )
    from homeassistant.exceptions import HomeAssistantError
    from tests_stress.test_request_rules_matrix import manager, rule
    from tests_real_ha.test_cross_feature_acceptance import _agent
    from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _speech

    agent = await _agent(hass)
    rules = await manager(
        rule("pressure", "start {a} x {b} x {c} x {d} end", "sentence_pattern")
    )
    processes, work = [], []
    lock = threading.Lock()
    popen = regex_execution.subprocess.Popen
    spawn = regex_execution.asyncio.create_subprocess_exec
    consume = request_rule_patterns.MatchBudget.consume

    def observed_popen(*args, **kwargs):
        process = popen(*args, **kwargs)
        with lock:
            processes.append(process)
        return process

    async def observed_spawn(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        processes.append(process)
        return process

    def charged(budget, amount=1):
        if budget.used == 0:
            work.append(budget)
        return consume(budget, amount)

    monkeypatch.setattr(regex_execution.subprocess, "Popen", observed_popen)
    monkeypatch.setattr(
        regex_execution.asyncio, "create_subprocess_exec", observed_spawn
    )
    monkeypatch.setattr(request_rule_patterns.MatchBudget, "consume", charged)
    spec = {
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string", "pattern": "^(a+)+$"}},
            "required": ["value"],
        }
    }
    pathological = "a" * 30 + "!"
    function_tasks = [
        asyncio.create_task(
            async_validate_function_arguments(hass, spec, {"value": pathological})
        )
        for _ in range(3)
    ]
    speech_tasks = [
        asyncio.create_task(
            regex_execution._async_apply_speech_replacements(
                pathological, [{"pattern": "^(a+)+$", "replacement": "ok"}]
            )
        )
        for _ in range(3)
    ]
    match_tasks = [
        asyncio.create_task(rules.async_match(hass, "start " + "x " * 180 + "end"))
        for _ in range(3)
    ]
    tasks = [*function_tasks, *speech_tasks, *match_tasks]
    try:
        async with asyncio.timeout(10):
            while len(processes) < 6 or not work:
                await asyncio.sleep(0.01)
        assert any(
            getattr(process, "returncode", None) is None for process in processes
        )
        function_tasks[0].cancel()
        speech_tasks[0].cancel()
        _install_wire(monkeypatch, agent, [_chat_sse_text("Matchers remain usable")])
        usable = await asyncio.wait_for(
            conversation.async_converse(
                hass=hass,
                text="Ordinary traffic under matching pressure",
                conversation_id=None,
                context=Context(),
                language="en",
                agent_id=agent.entry.entry_id,
            ),
            15,
        )
        assert _speech(usable) == "Matchers remain usable"
        overview = await asyncio.wait_for(
            async_management_command(
                hass,
                "matcher-admin",
                True,
                {
                    "section": "overview",
                    "action": "summary",
                    "entry_id": agent.entry.entry_id,
                    "subentry_id": agent.subentry.subentry_id,
                },
            ),
            15,
        )
        assert overview["load_errors"] == []
        outcomes = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), 15
        )
        assert isinstance(outcomes[0], asyncio.CancelledError)
        assert isinstance(outcomes[3], asyncio.CancelledError)
        assert all(isinstance(outcome, HomeAssistantError) for outcome in outcomes[1:3])
        assert outcomes[4:6] == [pathological, pathological]
        assert len(work) == 3 and all(budget.used > 1000 for budget in work)
        async with asyncio.timeout(5):
            while any(
                getattr(process, "returncode", None) is None for process in processes
            ):
                await asyncio.sleep(0.02)
        assert await async_validate_function_arguments(
            hass, spec, {"value": "aaa"}
        ) == {"value": "aaa"}
        assert (
            await regex_execution._async_apply_speech_replacements(
                "aaa", [{"pattern": "a+", "replacement": "healthy"}]
            )
            == "healthy"
        )
        assert (
            await rules.async_match(hass, "start one x two x three x four end")
            is not None
        )
        record(
            stress_trace,
            "summary",
            layer="Real HA workers",
            distinct_matcher_pressure_cases=1,
            regex_worker_settlements=6,
            recovery_conversations=1,
            bounded_automaton_work=sum(budget.used for budget in work),
        )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
