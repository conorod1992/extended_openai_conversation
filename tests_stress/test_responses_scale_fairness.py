"""Responses execution and fairness acceptance at installation scale."""

from __future__ import annotations

import asyncio
import json
from time import monotonic

import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_MODE,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    async_get_knowledge,
)
from custom_components.extended_openai_conversation_responses.memory import (
    async_get_memory,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _raw_client,
    _responses_sse_text,
    _responses_sse_tool_call,
)
from tests_stress.conftest import record
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


def _assert_active_contention(before, after, alive):
    """Each claimed workload must perform real work during foreground progress."""
    assert set(before) == {"index", "archive", "backup", "speech", "storage"}
    assert set(after) == set(before) == set(alive)
    assert all(alive.values()), alive
    assert all(before[name] >= 1 and after[name] > before[name] for name in before), (
        before,
        after,
    )


@pytest.mark.parametrize("defect", ["idle", "exited", "missing"])
def test_contention_evidence_rejects_inactive_workloads(defect):
    before = dict.fromkeys(["index", "archive", "backup", "speech", "storage"], 1)
    after = dict.fromkeys(before, 2)
    alive = dict.fromkeys(before, True)
    _assert_active_contention(before, after, alive)
    if defect == "idle":
        after["backup"] = 1
    elif defect == "exited":
        alive["speech"] = False
    else:
        del before["index"]
    with pytest.raises(AssertionError):
        _assert_active_contention(before, after, alive)


@pytest.mark.asyncio
@pytest.mark.usefixtures("real_store_io")
async def test_active_native_contention_preserves_assist_management_and_ha_progress(
    hass,
    hass_ws_client,
    monkeypatch,
    stress_scale,
    stress_seed,
    stress_trace,
):
    """Actual indexing, compression, search, speech and fsync compete with Assist."""
    from pathlib import Path
    import random

    import psutil

    from custom_components.extended_openai_conversation_responses import backup_transfer
    from custom_components.extended_openai_conversation_responses.agent_maintenance import (
        get_agent_maintenance_gate,
    )
    from custom_components.extended_openai_conversation_responses.conversation_archive import (
        async_get_archive,
    )
    from custom_components.extended_openai_conversation_responses.scope import (
        ResolvedDataScope,
    )
    from homeassistant.setup import async_setup_component
    from tests_real_ha.test_assist_streaming_speech_processing import (
        _chat_sse_deltas,
        _final_speech,
        _run_assist,
        _speech_agent,
    )
    from tests_real_ha.test_management_backend_acceptance import (
        _admin_client,
        _management_call,
    )

    owner = "contention-owner"
    heavy_entry = _make_entry(
        "Active native work",
        include_ai_task=False,
        conversation_options={
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_KNOWLEDGE_ENABLED: True,
        },
    )
    light_entry = _make_entry(
        "Foreground progress",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_RESPONSES,
            CONF_CHAT_MODEL: "gpt-5.6",
            "reasoning_effort": "none",
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
        },
    )
    await _setup_entry(hass, heavy_entry)
    await _setup_entry(hass, light_entry)
    subentry = next(iter(heavy_entry.subentries.values()))
    knowledge = await async_get_knowledge(
        hass, heavy_entry.entry_id, subentry.subentry_id
    )
    memory = await async_get_memory(hass, heavy_entry.entry_id, subentry.subentry_id)
    archive = await async_get_archive(hass, heavy_entry.entry_id, subentry.subentry_id)
    rng = random.Random(stress_seed ^ 0xAC71)
    content = "indexmarker " + " ".join(
        f"term{rng.randrange(1_000_000)}" for _ in range(8500)
    )
    sources = [
        await knowledge.async_create(f"Native source {n}", "Contention", content)
        for n in range(4 * stress_scale)
    ]
    scope = ResolvedDataScope(f"user:{owner}", "user", "context", owner)
    session = await archive.async_begin_session(
        "contention",
        scope,
        "contention",
        archive_enabled=True,
        shared_archive_enabled=True,
        inactivity_minutes=30,
    )
    assert session is not None
    for n in range(24 * stress_scale):
        await archive.async_record_turn(
            session.session_id,
            run_id=None,
            user_text=f"archivemarker {n} " + content[:8000],
            assistant_text=content[:8000],
            successful=True,
        )
    added = await memory.async_add(
        owner, "Durable contention record", "preferences", "explicit"
    )
    memory_id = added["memory"]["memory_id"]
    speech_agent = await _speech_agent(hass)
    assert await async_setup_component(hass, "assist_pipeline", {})
    client = await _admin_client(hass, hass_ws_client, user_id=owner)
    speech_client = await hass_ws_client(hass)
    spoken = "Large answer. " * (2400 * stress_scale)

    async def speech_send(request, *args, **kwargs):
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_chat_sse_deltas(
                [spoken[i : i + 1024] for i in range(0, len(spoken), 1024)]
            ),
            request=request,
        )

    async def light_send(request, *args, **kwargs):
        if request.url.path.endswith("chat/completions"):
            return await speech_send(request, *args, **kwargs)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_responses_sse_text("Light complete"),
            request=request,
        )

    monkeypatch.setattr(_raw_client(speech_agent)._client, "send", light_send)
    light_agent = conversation.async_get_agent(hass, light_entry.entry_id)
    monkeypatch.setattr(_raw_client(light_agent)._client, "send", light_send)
    counts = dict.fromkeys(["index", "archive", "backup", "speech", "storage"], 0)
    completed = {name: asyncio.Event() for name in counts}
    stop = False
    temp_paths = []

    async def work(name):
        while not stop:
            n = counts[name]
            if name == "index":
                source = sources[n % len(sources)]
                await knowledge.async_update(
                    source.source_id, content=content + f" update{n}"
                )
                assert await knowledge.async_search("indexmarker", limit=5)
            elif name == "archive":
                # Public Assist and management own this lease before entering the
                # native journal; direct native workload calls keep that contract.
                async with get_agent_maintenance_gate(
                    hass, heavy_entry.entry_id, subentry.subentry_id
                ).shared():
                    await archive.async_record_turn(
                        session.session_id,
                        run_id=None,
                        user_text=f"archivemarker live {n}",
                        assistant_text=content[:8000],
                        successful=True,
                    )
                    assert (
                        await archive.async_search(scope.scope_id, "archivemarker")
                    )["results"]
            elif name == "backup":
                exported = await backup_transfer._start_export(
                    hass, heavy_entry, subentry
                )
                path = backup_transfer._exports(hass)[exported["session_id"]].path
                temp_paths.append(path)
                try:
                    restored = await hass.async_add_executor_job(
                        backup_transfer._load_archive_document, path
                    )
                    assert "indexmarker" in json.dumps(restored)
                    assert exported["uncompressed_size"] > 300_000
                finally:
                    assert await backup_transfer._discard_export(
                        hass, exported["session_id"]
                    )
                assert not Path(path).exists()
            elif name == "speech":
                events = await _run_assist(
                    speech_client,
                    pipeline_id=speech_agent.entity_id,
                    conversation_id="contention-speech",
                )
                assert _final_speech(events).strip() == spoken.strip()
            else:
                await memory.async_update(
                    owner, memory_id, content=f"Durable contention {n}"
                )
                assert (await memory.async_list(owner))[
                    0
                ].content == f"Durable contention {n}"
            counts[name] += 1
            completed[name].set()

    workers = {
        name: asyncio.create_task(work(name), name=f"native-contention-{name}")
        for name in counts
    }
    cpu_before = psutil.Process().cpu_times()
    latencies = []

    async def workload_checkpoint():
        async def checkpoint():
            await asyncio.gather(*(event.wait() for event in completed.values()))

        barrier = asyncio.create_task(checkpoint())
        try:
            done, _ = await asyncio.wait(
                [barrier, *workers.values()],
                timeout=60,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in workers.values():
                if task in done:
                    await task
                    raise AssertionError(
                        "Native workload stopped before foreground completion"
                    )
            assert barrier in done, {
                name: task.get_stack() for name, task in workers.items()
            }
            await barrier
        finally:
            barrier.cancel()
            await asyncio.gather(barrier, return_exceptions=True)

    try:
        await workload_checkpoint()
        before = dict(counts)
        for event in completed.values():
            event.clear()
        for n in range(8):
            began = monotonic()
            result = await asyncio.wait_for(
                conversation.async_converse(
                    hass=hass,
                    text=f"Light contention {n}",
                    conversation_id="contention-light",
                    context=Context(user_id=owner),
                    language="en",
                    agent_id=light_entry.entry_id,
                ),
                5,
            )
            assert (
                result.response.as_dict()["speech"]["plain"]["speech"]
                == "Light complete"
            )
            await asyncio.wait_for(
                _management_call(
                    client,
                    entry=light_entry,
                    section="memories",
                    action="add",
                    content=f"Live save {n}",
                    category="preferences",
                    key=f"live{n}",
                ),
                5,
            )
            hass.states.async_set("sensor.contention_progress", n)
            assert hass.states.get("sensor.contention_progress").state == str(n)
            latencies.append(monotonic() - began)
        await workload_checkpoint()
        _assert_active_contention(
            before, counts, {name: not task.done() for name, task in workers.items()}
        )
    finally:
        stop = True
        done, pending = await asyncio.wait(workers.values(), timeout=20)
        for task in pending:
            task.cancel()
        assert not pending, {task.get_name(): task.get_stack() for task in pending}
        outcomes = await asyncio.gather(*done, return_exceptions=True)
        assert not any(isinstance(result, BaseException) for result in outcomes), (
            outcomes
        )
    cpu_after = psutil.Process().cpu_times()
    assert cpu_after.user + cpu_after.system > cpu_before.user + cpu_before.system
    # Inspect settled workers and native transfer files before any forced GC.
    assert not backup_transfer._exports(hass)
    assert all(not Path(path).exists() for path in temp_paths)
    record(
        stress_trace,
        "summary",
        active_contention_journeys=1,
        active_native_workloads=5,
        active_workload_completions=sum(counts.values()),
        contention_foreground_requests=8,
        contention_websocket_saves=8,
        pre_gc_resource_samples=1,
        realised_workload_counts=dict(counts),
        foreground_max_seconds=max(latencies),
    )


def _tool(index: int) -> dict:
    return {
        "spec": {
            "name": f"scale_marker_{index}",
            "description": f"Scale marker tool {index}",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        "function": {
            "type": "template",
            "value_template": f"SCALE_TOOL_RESULT_{index}",
        },
        "enabled": True,
    }


@pytest.mark.asyncio
async def test_populated_scale_executes_responses_retrieval_and_tool_wire(
    hass, monkeypatch, stress_scale, stress_trace
):
    owner = MockUser(
        id="responses-scale-owner", name="Responses scale owner", is_owner=True
    )
    owner.add_to_hass(hass)
    tool_count = 60 if stress_scale == 1 else 120
    memory_count = 120 if stress_scale == 1 else 240
    knowledge_count = 60 if stress_scale == 1 else 120
    tools = [_tool(index) for index in range(tool_count)]
    groups = [
        {
            "id": f"responses-scale-group-{index}",
            "name": f"Responses scale group {index}",
            "description": "Scale Responses group",
            "loading_mode": "always" if index < 10 else "on_demand",
            "functions": [
                f"scale_marker_{index * 3 + offset}"
                for offset in range(3)
                if index * 3 + offset < tool_count
            ],
            "enabled": True,
        }
        for index in range(max(1, tool_count // 3))
    ]
    entry = _make_entry(
        "Responses scale execution",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_RESPONSES,
            CONF_CHAT_MODEL: "gpt-5.6",
            "reasoning_effort": "none",
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_KNOWLEDGE_ENABLED: True,
            CONF_FUNCTION_TOOLS: tools,
            CONF_FUNCTION_GROUPS: groups,
        },
    )
    await _setup_entry(hass, entry)
    subentry = next(iter(entry.subentries.values()))
    agent = conversation.async_get_agent(hass, entry.entry_id)
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)
    for index in range(memory_count):
        await memory.async_add(
            owner.id,
            f"scale memory {index} {'RESPONSES_MEMORY_MARKER' if index == 0 else 'ordinary'}",
            "scale",
            "explicit",
            key=f"responses-scale-{index}",
        )
    for index in range(knowledge_count):
        await knowledge.async_create(
            f"Responses source {index}",
            "Scale source",
            f"scale knowledge {index} {'RESPONSES_KNOWLEDGE_MARKER' if index == 0 else 'ordinary'}",
            True,
        )

    requests = []
    replies = [
        _responses_sse_tool_call(
            "scale-memory",
            "memory_search",
            {"query": "RESPONSES_MEMORY_MARKER", "scope": "personal", "limit": 5},
        ),
        _responses_sse_tool_call(
            "scale-knowledge",
            "knowledge_search",
            {"query": "RESPONSES_KNOWLEDGE_MARKER", "limit": 5},
        ),
        _responses_sse_tool_call("scale-function", "scale_marker_0", {}),
        _responses_sse_text("Responses scale healthy"),
    ]

    async def send(request, *args, **kwargs):
        del args, kwargs
        body = json.loads(request.content)
        requests.append(body)
        index = len(requests) - 1
        assert index < len(replies)
        if index == 0:
            names = {
                tool["name"] for tool in body["tools"] if tool.get("type") == "function"
            }
            assert {"memory_search", "knowledge_search", "scale_marker_0"} <= names
        if index == 1:
            assert "RESPONSES_MEMORY_MARKER" in json.dumps(body)
        if index == 2:
            assert "RESPONSES_KNOWLEDGE_MARKER" in json.dumps(body)
        if index == 3:
            assert "SCALE_TOOL_RESULT_0" in json.dumps(body)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=replies[index],
            request=request,
        )

    monkeypatch.setattr(_raw_client(agent)._client, "send", send)
    result = await conversation.async_converse(
        hass=hass,
        text="Use scale memory, knowledge and marker tool",
        conversation_id=None,
        context=Context(user_id=owner.id),
        language="en",
        agent_id=entry.entry_id,
    )
    assert result.response.error_code is None
    assert (
        result.response.as_dict()["speech"]["plain"]["speech"]
        == "Responses scale healthy"
    )
    assert len(requests) == 4
    record(
        stress_trace,
        "summary",
        large_responses_wire_journeys=1,
        large_responses_memory_records=memory_count,
        large_responses_knowledge_sources=knowledge_count,
        large_responses_function_tools=tool_count,
        actual_tool_executions=3,
    )


@pytest.mark.asyncio
async def test_mixed_background_pressure_preserves_lightweight_progress(
    hass, monkeypatch, hass_ws_client, stress_trace
):
    """An outstanding provider proves isolation, independently of CPU/IO contention."""
    owner = MockUser(id="fairness-owner", name="Fairness owner", is_owner=True)
    owner.add_to_hass(hass)
    entry = _make_entry(
        "Mixed workload fairness",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_RESPONSES,
            CONF_CHAT_MODEL: "gpt-5.6",
            "reasoning_effort": "none",
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_KNOWLEDGE_ENABLED: True,
        },
    )
    await _setup_entry(hass, entry)
    from tests_real_ha.test_management_backend_acceptance import (
        _admin_client,
        _management_call,
    )

    client = await _admin_client(hass, hass_ws_client)
    warm = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    await _management_call(
        client,
        entry=entry,
        section="configuration",
        action="save",
        revision=warm["revision"],
        config={"max_tokens": 600},
    )
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, entry.entry_id)
    admitted = asyncio.Event()
    release_heavy = asyncio.Event()
    lightweight_latencies = []
    heavy_calls = 0

    async def send(request, *args, **kwargs):
        nonlocal heavy_calls
        body = json.loads(request.content)
        text = json.dumps(body)
        if "HEAVY_FAIRNESS" in text:
            heavy_calls += 1
            admitted.set()
            await release_heavy.wait()
            payload = _responses_sse_text("Heavy complete")
        else:
            assert body["max_output_tokens"] == 777
            payload = _responses_sse_text("Light complete")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=payload,
            request=request,
        )

    monkeypatch.setattr(_raw_client(agent)._client, "send", send)
    heavy = asyncio.create_task(
        conversation.async_converse(
            hass=hass,
            text="HEAVY_FAIRNESS",
            conversation_id=None,
            context=Context(user_id=owner.id),
            language="en",
            agent_id=entry.entry_id,
        )
    )
    await asyncio.wait_for(admitted.wait(), 5)
    try:
        before = await _management_call(
            client, entry=entry, section="configuration", action="get"
        )
        saved = await asyncio.wait_for(
            _management_call(
                client,
                entry=entry,
                section="configuration",
                action="save",
                revision=before["revision"],
                config={"max_tokens": 777},
            ),
            5,
        )
        assert saved["revision"] != before["revision"]
        assert saved["_performance"]["live_runtime_update"]
        assert conversation.async_get_agent(hass, entry.entry_id) is agent
        assert not heavy.done() and not release_heavy.is_set()
        for index in range(8):
            started = monotonic()
            result = await asyncio.wait_for(
                conversation.async_converse(
                    hass=hass,
                    text=f"LIGHT_FAIRNESS_{index}",
                    conversation_id=None,
                    context=Context(user_id=owner.id),
                    language="en",
                    agent_id=entry.entry_id,
                ),
                5,
            )
            lightweight_latencies.append(monotonic() - started)
            assert (
                result.response.as_dict()["speech"]["plain"]["speech"]
                == "Light complete"
            )
        assert max(lightweight_latencies) < 5
        assert not heavy.done()
    finally:
        release_heavy.set()
    result = await asyncio.wait_for(heavy, 10)
    assert result.response.as_dict()["speech"]["plain"]["speech"] == "Heavy complete"
    assert heavy_calls == 1
    record(
        stress_trace,
        "summary",
        resource_fairness_cases=1,
        resource_fairness_lightweight_requests=len(lightweight_latencies),
        resource_fairness_max_light_seconds=max(lightweight_latencies),
        provider_isolation_live_saves=1,
    )
