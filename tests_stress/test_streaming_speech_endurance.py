"""Nightly speech streaming through the public Home Assistant Assist pipeline."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import random
from typing import Any

import httpx
import pytest

from custom_components.extended_openai_conversation_responses import (
    entity as integration_entity,
)
from homeassistant.components import conversation
from homeassistant.components.assist_pipeline import pipeline as assist_pipeline
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
from tests_stress.provider_fault_transport import GatedSSEStream


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


async def _start_gated_assist(
    client: Any,
    agent: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    conversation_id: str,
) -> tuple[GatedSSEStream, dict[str, Any], Any, int]:
    """Start genuine Assist and stop only after safe progressive speech arrived."""
    frames = [
        frame + b"\n\n"
        for frame in _chat_sse_deltas(
            ["**Safe** audible sentence. ", "https://example.com/unfinished", " tail."]
        ).split(b"\n\n")
        if frame
    ]
    stream = GatedSSEStream(frames, gate_after=2)
    raw_client = _raw_client(agent)

    async def send(request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
        del args, kwargs
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=stream,
            request=request,
        )

    monkeypatch.setattr(raw_client._client, "send", send)
    captured: dict[str, Any] = {"listener_deltas": []}
    original_cleanup = integration_entity.async_streaming_speech_cleanup

    @contextmanager
    def observe_cleanup(chat_log: Any, config: dict[str, Any]):
        listener = chat_log.delta_listener
        assert listener is not None, "Assist did not install a ChatLog delta listener"

        def observed_listener(observed_log: Any, delta: dict[str, Any]) -> None:
            captured["listener_deltas"].append(dict(delta))
            listener(observed_log, delta)

        chat_log.delta_listener = observed_listener
        captured["chat_log"] = chat_log
        captured["original_listener"] = listener
        try:
            with original_cleanup(chat_log, config):
                captured["sanitizer_listener"] = chat_log.delta_listener
                captured["observed_listener"] = observed_listener
                yield
        finally:
            captured["restored_listener"] = chat_log.delta_listener

    monkeypatch.setattr(
        integration_entity, "async_streaming_speech_cleanup", observe_cleanup
    )
    original_execute = assist_pipeline.PipelineInput.execute

    async def observe_pipeline_task(self: Any) -> Any:
        captured["pipeline_task"] = asyncio.current_task()
        return await original_execute(self)

    monkeypatch.setattr(assist_pipeline.PipelineInput, "execute", observe_pipeline_task)

    await client.send_json_auto_id(
        {
            "type": "assist_pipeline/run",
            "start_stage": "intent",
            "end_stage": "intent",
            "pipeline": agent.entity_id,
            "input": {"text": "Speak safely before the interrupted provider response"},
            "conversation_id": conversation_id,
            "device_id": "assist-interrupted-speech-device",
        }
    )
    result = await client.receive_json()
    assert result["success"] is True
    run_id = result["id"]

    await asyncio.wait_for(stream.delivered.wait(), timeout=10)
    safe_progress = ""
    async with asyncio.timeout(10):
        while "Safe audible sentence." not in safe_progress:
            message = await client.receive_json()
            event = message.get("event", {})
            delta = event.get("data", {}).get("chat_log_delta", {})
            content = delta.get("content")
            if isinstance(content, str):
                safe_progress += content
    assert "https://" not in safe_progress
    assert stream.yielded == 2
    assert captured.get("pipeline_task") is not None
    assert not captured["pipeline_task"].done(), "Assist response finished before interruption"
    sanitizer = captured.get("sanitizer_listener")
    assert sanitizer is not None and sanitizer is not captured["original_listener"]
    assert "https://example.com/unfinished" in sanitizer._sanitizer._buffer
    assert captured["listener_deltas"]
    return stream, captured, captured["pipeline_task"], run_id


async def _unsubscribe_assist_run(client: Any, run_id: int, task: asyncio.Task) -> None:
    """Cancel the actual HA Assist run through its websocket subscription."""
    await client.send_json_auto_id(
        {"type": "unsubscribe_events", "subscription": run_id}
    )
    async with asyncio.timeout(10):
        while True:
            message = await client.receive_json()
            if message.get("id") == run_id + 1:
                assert message.get("success") is True, message
                break
    await asyncio.wait_for(
        asyncio.gather(task, return_exceptions=True), timeout=10
    )
    assert task.cancelled(), "Assist unsubscribe completed without cancelling its run"


def _assert_speech_listener_restored(captured: dict[str, Any]) -> int:
    """Prove the sanitizer detached and the original Assist listener was restored."""
    chat_log = captured["chat_log"]
    listener = captured["observed_listener"]
    assert captured["restored_listener"] is listener
    assert chat_log.delta_listener is listener
    return len(captured["listener_deltas"])


@pytest.mark.asyncio
async def test_cancelled_fragment_cannot_leak_into_next_assist_stream(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    """Cancel genuine Assist while its progressive sanitizer holds an unfinished URL."""
    agent = await _speech_agent(hass)
    assert await async_setup_component(hass, "assist_pipeline", {})
    client = await hass_ws_client(hass)
    stream, captured, task, run_id = await _start_gated_assist(
        client, agent, monkeypatch, conversation_id="nightly-speech-cancelled"
    )
    await _unsubscribe_assist_run(client, run_id, task)
    stream.assert_explicit_close_completed()
    assert not stream.iteration_finished.is_set()
    callbacks_at_restore = _assert_speech_listener_restored(captured)
    await hass.async_block_till_done()
    assert len(captured["listener_deltas"]) == callbacks_at_restore

    wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_deltas(["**Fresh** see ht", "tps://example.com/new and done."])],
    )
    events = await _run_assist(
        await hass_ws_client(hass),
        pipeline_id=agent.entity_id,
        conversation_id="nightly-speech-after-cancellation",
    )
    assert _progressive_text(events) == "Fresh see and done."
    assert _final_speech(events) == "Fresh see and done."
    assert "Safe audible sentence." not in str(events)
    assert "example.com/unfinished" not in str(events)
    assert len(wire.requests) == 1
    record(
        stress_trace,
        "summary",
        layer="Real HA Assist and provider wire",
        interrupted_active_speech_listener_cancel=1,
        explicit_stream_close_cancel=1,
        clean_recovery_requests=1,
    )


@pytest.mark.asyncio
async def test_unload_during_fragmented_stream_recovers_clean_speech(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    """Unload leaves the captured Assist run live until its owner unsubscribes it."""
    old_agent = await _speech_agent(hass)
    entry_id = old_agent.entry.entry_id
    assert await async_setup_component(hass, "assist_pipeline", {})
    client = await hass_ws_client(hass)
    stream, captured, task, run_id = await _start_gated_assist(
        client, old_agent, monkeypatch, conversation_id="nightly-speech-unload"
    )
    assert await asyncio.wait_for(hass.config_entries.async_unload(entry_id), timeout=10)
    assert conversation.async_get_agent(hass, entry_id) is None
    # EOAI unload releases its platform; it does not own or cancel an already-running
    # HA Assist pipeline task. Assert that current behavior before explicit test cleanup.
    assert not task.done(), "unload unexpectedly ended the in-flight Assist run"
    assert not stream.close_finished.is_set()
    await _unsubscribe_assist_run(client, run_id, task)
    stream.assert_explicit_close_completed()
    assert not stream.iteration_finished.is_set()
    callbacks_at_restore = _assert_speech_listener_restored(captured)
    await hass.async_block_till_done()
    assert len(captured["listener_deltas"]) == callbacks_at_restore

    assert await hass.config_entries.async_setup(entry_id)
    await hass.async_block_till_done()
    new_agent = conversation.async_get_agent(hass, entry_id)
    assert new_agent is not None and new_agent is not old_agent
    wire = _install_wire(
        monkeypatch,
        new_agent,
        [_chat_sse_deltas(["**New** see ht", "tps://example.com/new done."])],
    )
    events = await _run_assist(
        await hass_ws_client(hass),
        pipeline_id=new_agent.entity_id,
        conversation_id="nightly-speech-after-unload",
    )
    assert _progressive_text(events) == "New see done."
    assert _final_speech(events) == "New see done."
    assert "Safe audible sentence." not in str(events)
    assert "example.com/unfinished" not in str(events)
    assert len(wire.requests) == 1
    record(
        stress_trace,
        "summary",
        layer="Real HA Assist and provider wire",
        interrupted_active_speech_listener_unload=1,
        explicit_stream_close_unload=1,
        clean_recovery_requests=1,
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
    from tests_real_ha.test_cross_feature_acceptance import _agent
    from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _speech
    from tests_stress.test_request_rules_matrix import manager, rule

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
