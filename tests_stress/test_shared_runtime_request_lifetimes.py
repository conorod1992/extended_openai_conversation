"""Real shared HA clients survive EOAI ownership changes and cancelled waits."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json

from aiohttp import web
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    MockUser,
    mock_integration,
    mock_platform,
)

from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.script import Script, async_validate_actions_config
from homeassistant.setup import async_setup_component
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _raw_client,
    _responses_sse_text,
    _responses_sse_tool_call,
    _speech,
)
from tests_stress.conftest import record
from tests_stress.test_active_provider_stream_cancellation import _partial_text
from tests_stress.test_provider_real_connection_pool import _say


def _reply(mode, text):
    return (_responses_sse_text if mode == "responses" else _chat_sse_text)(text)


async def _endpoint(provider, neighbour=None):
    app = web.Application()
    app.router.add_post("/v1/chat/completions", provider)
    app.router.add_post("/v1/responses", provider)
    if neighbour is not None:
        app.router.add_get("/neighbour", neighbour)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"


def _await_frames(task):
    """Observe suspended coroutines without changing SDK sleeps or policy."""
    current = task.get_coro()
    while current is not None:
        frame = getattr(current, "cr_frame", None) or getattr(current, "gi_frame", None)
        if frame is not None:
            yield frame
        current = getattr(current, "cr_await", None) or getattr(
            current, "gi_yieldfrom", None
        )


async def _wait_backoff(task):
    async with asyncio.timeout(10):
        while not task.done():
            frames = list(_await_frames(task))
            sdk = any(
                "openai" in frame.f_code.co_filename
                and frame.f_code.co_name in {"_sleep_for_retry", "_retry_request"}
                for frame in frames
            )
            delays = [
                frame.f_locals.get("delay")
                for frame in frames
                if frame.f_code.co_name == "sleep"
            ]
            if sdk and 2 in delays:
                return
            await asyncio.sleep(0)
    raise AssertionError("Public request never entered real nonzero SDK backoff")


async def _cancel(task):
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)


def _entry(mode, url, tools=None):
    return _make_entry(
        "Shared runtime",
        include_ai_task=False,
        base_url=url + "/v1",
        conversation_options={
            CONF_API_MODE: mode,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: tools or [],
        },
    )


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_neighbour_shared_client_and_native_script_survive_final_removal(
    hass,
    socket_enabled,
    stress_trace,
    mode,
):
    """A genuinely separate integration retains its HA-owned HTTPX client."""
    del socket_enabled
    MockUser(id="real-pool-owner", is_owner=True).add_to_hass(hass)
    entered, release = asyncio.Event(), asyncio.Event()
    effects, bodies = [], []

    async def neighbouring_endpoint(request):
        entered.set()
        await release.wait()
        return web.json_response({"neighbour": "healthy"})

    async def provider(request):
        body = await request.json()
        bodies.append(body)
        if len(bodies) % 2:
            tool = (
                _responses_sse_tool_call if mode == "responses" else _chat_sse_tool_call
            )
            payload = tool(f"call-shared-script-{len(bodies)}", "shared_script", {})
        else:
            payload = _reply(mode, "EOAI healthy")
        return web.Response(body=payload, content_type="text/event-stream")

    runner, url = await _endpoint(provider, neighbouring_endpoint)

    async def setup_neighbour(runtime, config_entry):
        runtime.data["shared_neighbour"] = get_async_client(runtime)
        return True

    mock_integration(
        hass, MockModule("shared_neighbour", async_setup_entry=setup_neighbour)
    )

    class NeighbourFlow(ConfigFlow, domain="shared_neighbour"):
        VERSION = 1

    mock_platform(hass, "shared_neighbour.config_flow")
    neighbour = MockConfigEntry(domain="shared_neighbour")
    neighbour.add_to_hass(hass)
    assert await hass.config_entries.async_setup(neighbour.entry_id)
    client = hass.data["shared_neighbour"]

    async def effect(call):
        effects.append(call.data["marker"])

    hass.services.async_register("shared_probe", "record", effect)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {
                    "alias": "Independent neighbour event",
                    "triggers": [
                        {"trigger": "event", "event_type": "shared_neighbour_probe"}
                    ],
                    "actions": [
                        {
                            "action": "shared_probe.record",
                            "data": {"marker": "Automation"},
                        }
                    ],
                }
            ]
        },
    )
    tool = {
        "spec": {
            "name": "shared_script",
            "description": "Run isolated script",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "script",
            "sequence": [
                {"action": "shared_probe.record", "data": {"marker": "EOAI"}},
            ],
        },
    }
    entry = _entry(mode, url, [tool])
    task = None
    try:
        await _setup_entry(hass, entry)
        assert (
            _raw_client(conversation.async_get_agent(hass, entry.entry_id))._client
            is client
        )
        task = asyncio.create_task(client.get(url + "/neighbour"))
        await asyncio.wait_for(entered.wait(), 10)
        assert await hass.config_entries.async_reload(entry.entry_id)
        assert (
            _speech(await _say(hass, entry.entry_id, "Run shared script"))
            == "EOAI healthy"
        )
        assert await hass.config_entries.async_remove(entry.entry_id)
        assert not client.is_closed and not task.done()
        replacement = _entry(mode, url, [tool])
        await _setup_entry(hass, replacement)
        native = Script(
            hass,
            await async_validate_actions_config(
                hass,
                cv.SCRIPT_SCHEMA(
                    [
                        {"action": "shared_probe.record", "data": {"marker": "Native"}},
                    ]
                ),
            ),
            "Unrelated native script",
            "shared_neighbour",
        )
        await native.async_run()
        hass.bus.async_fire("shared_neighbour_probe")
        await hass.async_block_till_done()
        release.set()
        assert (await asyncio.wait_for(task, 10)).json() == {"neighbour": "healthy"}
        assert (await client.get(url + "/neighbour")).json() == {"neighbour": "healthy"}
        assert (
            _speech(await _say(hass, replacement.entry_id, "Run recreated script"))
            == "EOAI healthy"
        )
        assert effects == ["EOAI", "Native", "Automation", "EOAI"]
        assert len(bodies) == 4
        assert not client.is_closed
        record(
            stress_trace,
            "summary",
            shared_runtime_ownership_cases=1,
            neighbour_requests=2,
            native_script_effects=1,
            public_turns=2,
            mode=mode,
        )
    finally:
        release.set()
        if task is not None and not task.done():
            await _cancel(task)
        await runner.cleanup()


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("stop", ["caller", "unload", "remove"])
async def test_nonzero_sdk_backoff_stops_with_its_owner(
    hass,
    socket_enabled,
    stress_trace,
    mode,
    stop,
):
    del socket_enabled
    MockUser(id="real-pool-owner", is_owner=True).add_to_hass(hass)
    attempts = []

    async def provider(request):
        body = json.dumps(await request.json())
        marker = "Backoff" if "Backoff" in body else "Healthy"
        attempts.append(marker)
        if marker == "Backoff":
            return web.json_response(
                {"error": {"message": "rate limited", "type": "rate_limit"}},
                status=429,
                headers={"Retry-After": "2"},
            )
        return web.Response(
            body=_reply(mode, "Recovered"), content_type="text/event-stream"
        )

    runner, url = await _endpoint(provider)
    entry = _entry(mode, url)
    active = None
    try:
        await _setup_entry(hass, entry)
        shared = get_async_client(hass)
        active = asyncio.create_task(_say(hass, entry.entry_id, "Backoff"))
        await _wait_backoff(active)
        assert attempts == ["Backoff"] and not active.done()
        if stop == "caller":
            await _cancel(active)
        elif stop == "unload":
            # Reload preserves already admitted caller-owned conversations. The
            # abandoned caller must still cancel its backoff after platform unload.
            assert await hass.config_entries.async_unload(entry.entry_id)
            await _cancel(active)
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        else:
            assert await asyncio.wait_for(
                hass.config_entries.async_remove(entry.entry_id), 10
            )
            with pytest.raises(asyncio.CancelledError):
                await active
            entry = _entry(mode, url)
            await _setup_entry(hass, entry)
        assert active.cancelled()
        assert not list(_await_frames(active))
        assert not shared.is_closed
        assert _speech(await _say(hass, entry.entry_id, "Healthy")) == "Recovered"
        # Observe beyond the original SDK retry deadline, rather than assuming
        # cancellation prevents a detached retry from firing later.
        deadline = asyncio.get_running_loop().create_future()
        timer = asyncio.get_running_loop().call_later(2.1, deadline.set_result, None)
        try:
            await deadline
        finally:
            timer.cancel()
        assert attempts == ["Backoff", "Healthy"]
        record(
            stress_trace,
            "summary",
            nonzero_backoff_cancel_cases=1,
            provider_requests=2,
            public_turns=2,
            mode=mode,
            stop=stop,
            retry_delay_seconds=2,
        )
    finally:
        if active is not None and not active.done():
            await _cancel(active)
        await runner.cleanup()


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_active_nonterminal_socket_stream_cancels_and_recovers(
    hass,
    socket_enabled,
    stress_trace,
    mode,
):
    del socket_enabled
    MockUser(id="real-pool-owner", is_owner=True).add_to_hass(hass)
    commands = asyncio.Queue()
    acknowledged = asyncio.Queue()
    release = asyncio.Event()
    transports = []

    async def provider(request):
        body = json.dumps(await request.json())
        if "Heartbeats" not in body:
            return web.Response(
                body=_reply(mode, "Independent healthy"),
                content_type="text/event-stream",
            )
        transports.append(request.transport)
        response = web.StreamResponse(headers={"content-type": "text/event-stream"})
        await response.prepare(request)
        await response.write(_partial_text(mode)[0])
        acknowledged.put_nowait("partial")
        while True:
            command = await commands.get()
            if command == "stop":
                break
            with suppress(ConnectionResetError):
                await response.write(b": active heartbeat\n\n")
            acknowledged.put_nowait(command)
        await release.wait()
        return response

    runner, url = await _endpoint(provider)
    entry = _entry(mode, url)
    active = None
    try:
        await _setup_entry(hass, entry)
        active = asyncio.create_task(_say(hass, entry.entry_id, "Heartbeats"))
        assert await asyncio.wait_for(acknowledged.get(), 10) == "partial"
        for index in range(6):
            commands.put_nowait(index)
            assert await asyncio.wait_for(acknowledged.get(), 10) == index
            assert not active.done()
        assert (
            _speech(await _say(hass, entry.entry_id, "Healthy"))
            == "Independent healthy"
        )
        await _cancel(active)
        async with asyncio.timeout(10):
            while not transports[0].is_closing():
                await asyncio.sleep(0)
        assert (
            _speech(await _say(hass, entry.entry_id, "Healthy again"))
            == "Independent healthy"
        )
        assert not get_async_client(hass).is_closed
        record(
            stress_trace,
            "summary",
            active_nonterminal_stream_cases=1,
            provider_heartbeats=6,
            public_turns=3,
            mode=mode,
            pre_gc_resource_samples=1,
        )
    finally:
        commands.put_nowait("stop")
        release.set()
        if active is not None and not active.done():
            await _cancel(active)
        await runner.cleanup()
