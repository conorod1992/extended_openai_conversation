"""Slow native audio recipients and overlapping producers retain ownership."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import io
import json
import socket
from urllib.parse import urlsplit
import wave

from aiohttp import web
import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.intercom import (
    async_get_intercom,
)
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    async_get_quiet_hours,
)
from homeassistant.components import conversation
from homeassistant.components.assist_pipeline.pipeline import KEY_ASSIST_PIPELINE
from homeassistant.components.assist_satellite.entity import AssistSatelliteState
from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.components.websocket_api.const import MAX_PENDING_MSG
from homeassistant.core import Context
from tests_real_ha.test_acceptance_lifecycle import _setup_entry
from tests_real_ha.test_assist_streaming_speech_processing import (
    _chat_sse_deltas,
    _final_speech,
    _progressive_text,
)
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _management_call,
)
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _speech
from tests_stress.conftest import record
from tests_stress.test_native_audio_delivery import (
    _install_audio_entities,
    _SoftwareSatellite,
    _SoftwareTTS,
    _test_wav,
)
from tests_stress.test_provider_real_connection_pool import _say
from tests_stress.test_runtime_soak import _resource_footprint
from tests_stress.test_shared_runtime_request_lifetimes import (
    _await_frames,
    _endpoint,
    _entry,
)


class _VolumeOutput(MediaPlayerEntity):
    _attr_supported_features = MediaPlayerEntityFeature.VOLUME_SET
    _attr_state = MediaPlayerState.IDLE

    def __init__(self, satellite):
        self._attr_unique_id = satellite.unique_id + "-volume"
        self._attr_name = satellite.name + " speaker"
        self._attr_device_info = satellite.device_info
        self._attr_volume_level = 0.6
        self.changes = []

    async def async_set_volume_level(self, volume):
        self.changes.append(volume)
        self._attr_volume_level = volume
        self.async_write_ha_state()


async def _audio_runtime(hass, url, *, large_audio=False):
    entry = _entry("chat_completions", url)
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    satellites = [
        _SoftwareSatellite(key, announce=True)
        for key in ("shared-output", "independent-output")
    ]
    controls = [_VolumeOutput(satellite) for satellite in satellites]
    recording = _test_wav()
    if large_audio:
        output = io.BytesIO()
        with (
            wave.open(io.BytesIO(recording), "rb") as source,
            wave.open(output, "wb") as target,
        ):
            target.setparams(source.getparams())
            target.writeframes(source.readframes(source.getnframes()) * 2048)
        spoken_audio = output.getvalue()
    else:
        spoken_audio = recording
    entities, pcm = await _install_audio_entities(
        hass,
        recording,
        tts=_SoftwareTTS(spoken_audio),
        satellites=satellites,
        media_players=controls,
    )
    store = hass.data[KEY_ASSIST_PIPELINE].pipeline_store
    pipeline = await store.async_create_item(
        {
            "name": "Shared native output",
            "language": "en",
            "conversation_language": "en",
            "conversation_engine": agent.entity_id,
            "stt_engine": entities["stt"].entity_id,
            "stt_language": "en",
            "tts_engine": entities["tts"].entity_id,
            "tts_language": "en",
            "tts_voice": None,
            "wake_word_entity": None,
            "wake_word_id": None,
            "prefer_local_intents": False,
        }
    )
    store.async_set_preferred_item(pipeline.id)

    async def run(satellite):
        async def source():
            for start in range(0, len(pcm), 640):
                yield pcm[start : start + 640]
                await asyncio.sleep(0)

        await asyncio.wait_for(
            satellite.async_accept_pipeline_from_satellite(
                source(), context=Context(user_id="real-pool-owner")
            ),
            15,
        )
        assert not [event for event in satellite.events if event.type.value == "error"]
        assert satellite.state is AssistSatelliteState.RESPONDING

    return entry, entities, satellites, controls, spoken_audio, run


def _audio_url(satellite):
    event = next(
        event for event in reversed(satellite.events) if event.type.value == "tts-end"
    )
    return urlsplit(event.data["tts_output"]["url"]).path


def _assert_bounded_recipient(sample):
    assert sample["paused"] is True
    assert 0 < sample["queued_bytes"] <= sample["high_water"] + 256 * 1024
    assert sample["received_bytes"] < sample["audio_bytes"]


@pytest.mark.parametrize("defect", ["unbounded", "finished", "not_paused"])
def test_recipient_guard_rejects_missing_active_backpressure(defect):
    sample = {
        "paused": True,
        "queued_bytes": 131072,
        "high_water": 131072,
        "received_bytes": 4096,
        "audio_bytes": 6553600,
    }
    _assert_bounded_recipient(sample)
    if defect == "unbounded":
        sample["queued_bytes"] = sample["audio_bytes"]
    elif defect == "finished":
        sample["received_bytes"] = sample["audio_bytes"]
    else:
        sample["paused"] = False
    with pytest.raises(AssertionError):
        _assert_bounded_recipient(sample)


@pytest.mark.parametrize("recipient", ["slow", "abandon"])
async def test_slow_native_audio_recipient_preserves_foreground_progress(
    hass,
    hass_client,
    hass_ws_client,
    socket_enabled,
    stress_trace,
    recipient,
):
    del socket_enabled
    MockUser(id="real-pool-owner", is_owner=True).add_to_hass(hass)
    spoken = "Substantial native response. " * 1024

    async def provider(request):
        body = json.dumps(await request.json())
        text = "Independent healthy" if "Healthy" in body else spoken
        payload = (
            _chat_sse_text(text)
            if text != spoken
            else _chat_sse_deltas(["Substantial native response. "] * 1024)
        )
        return web.Response(body=payload, content_type="text/event-stream")

    runner, url = await _endpoint(provider)
    response = None
    try:
        entry, entities, satellites, _, recording, run = await _audio_runtime(
            hass, url, large_audio=True
        )
        satellite = satellites[0]
        await run(satellite)
        assert entities["tts"].messages == [spoken]
        http = await hass_client(hass)
        response = await http.get(_audio_url(satellite))
        assert response.status == 200
        prefix = await response.content.readexactly(4096)
        async with asyncio.timeout(10):
            while not response.content._protocol._reading_paused:
                await asyncio.sleep(0)
        sample = {
            "paused": response.content._protocol._reading_paused,
            "queued_bytes": response.content._size,
            "high_water": response.content._high_water,
            "received_bytes": len(prefix),
            "audio_bytes": len(recording),
        }
        _assert_bounded_recipient(sample)
        resources = _resource_footprint(hass)
        admin = await _admin_client(hass, hass_ws_client)
        before = await _management_call(
            admin, entry=entry, section="configuration", action="get"
        )
        saved = await asyncio.wait_for(
            _management_call(
                admin,
                entry=entry,
                section="configuration",
                action="update",
                revision=before["revision"],
                title="Saved while audio recipient paused",
                config={},
            ),
            5,
        )
        assert saved["title"] == "Saved while audio recipient paused"
        assert (
            _speech(await asyncio.wait_for(_say(hass, entry.entry_id, "Healthy"), 5))
            == "Independent healthy"
        )
        hass.states.async_set("sensor.downstream_progress", "responsive")
        assert hass.states.get("sensor.downstream_progress").state == "responsive"
        assert satellite.state is AssistSatelliteState.RESPONDING
        if recipient == "abandon":
            response.close()
            assert response.closed
            # Native playback acknowledgement owns the satellite state. Losing
            # an HTTP audio reader does not imply cancelling completed synthesis.
            assert satellite.state is AssistSatelliteState.RESPONDING
        else:
            digest = hashlib.sha256(prefix)
            received = len(prefix)
            while chunk := await response.content.read(65536):
                received += len(chunk)
                digest.update(chunk)
                await asyncio.sleep(0)
            assert received == len(recording)
            assert digest.digest() == hashlib.sha256(recording).digest()
        satellite.tts_response_finished()
        assert satellite.state is AssistSatelliteState.IDLE
        assert response.content._size == 0 if recipient == "slow" else response.closed
        assert _resource_footprint(hass) == resources
        await run(satellite)
        async with await http.get(_audio_url(satellite)) as recovered:
            assert recovered.status == 200
            assert (
                hashlib.sha256(await recovered.read()).digest()
                == hashlib.sha256(recording).digest()
            )
        satellite.tts_response_finished()
        assert satellite.state is AssistSatelliteState.IDLE
        assert (
            len([event for event in satellite.events if event.type.value == "tts-end"])
            == 2
        )
        assert (
            _speech(await _say(hass, entry.entry_id, "Healthy after recipient"))
            == "Independent healthy"
        )
        record(
            stress_trace,
            "summary",
            downstream_audio_cases=1,
            downstream_bounded_samples=1,
            pre_gc_resource_samples=2,
            genuine_management_websocket_commands=2,
            recipient=recipient,
            recipient_observation=sample,
            resource_before=resources,
            resource_after=_resource_footprint(hass),
            provider_requests=4,
            public_turns=4,
        )
    finally:
        if response is not None:
            response.close()
        await runner.cleanup()


def _native_ws_handler(client):
    address = client._writer.transport.get_extra_info("sockname")
    handlers = []
    for task in asyncio.all_tasks():
        for frame in _await_frames(task):
            candidate = frame.f_locals.get("self")
            if (
                type(candidate).__name__ == "WebSocketHandler"
                and candidate._request.transport.get_extra_info("peername") == address
            ):
                handlers.append(candidate)
    assert handlers, "The selected native WebSocket handler was not observed"
    assert all(handler is handlers[0] for handler in handlers)
    return handlers[0]


def _ws_backlog(handler):
    if not hasattr(handler, "_message_queue"):
        queue = handler._to_write
        return queue.qsize(), sum(len(item) for item in queue._queue)
    queue = handler._message_queue
    if queue is None:
        assert handler._closing and handler._writer_task is None
        return 0, 0
    return len(queue), sum(len(item) for item in queue)


@pytest.mark.parametrize("recipient", ["resume", "abandon"])
async def test_slow_actual_assist_websocket_has_bounded_native_backlog(
    hass,
    hass_ws_client,
    socket_enabled,
    stress_trace,
    recipient,
):
    """Constrain actual recipient TCP flow, keeping HA's real writer and queue."""
    del socket_enabled
    MockUser(id="real-pool-owner", is_owner=True).add_to_hass(hass)
    produce, produced, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    prefix = "Audible prefix. "
    parts = [f"Chunk {index:02d} " + "bounded speech " * 512 for index in range(64)]
    transports = []

    async def provider(request):
        body = json.dumps(await request.json())
        if "Slow downstream socket" not in body:
            return web.Response(
                body=_chat_sse_text("Independent healthy"),
                content_type="text/event-stream",
            )
        transports.append(request.transport)
        response = web.StreamResponse(headers={"content-type": "text/event-stream"})
        await response.prepare(request)
        await response.write(
            _chat_sse_deltas([prefix, "unused"]).split(b"\n\n")[0] + b"\n\n"
        )
        await produce.wait()
        for part in parts:
            await response.write(
                _chat_sse_deltas([part, "unused"]).split(b"\n\n")[0] + b"\n\n"
            )
            await asyncio.sleep(0)
        produced.set()
        await finish.wait()
        if not transports[0].is_closing():
            await response.write(_chat_sse_deltas([""]))
        return response

    runner, url = await _endpoint(provider)
    client = handler = None
    try:
        entry, _, _, _, _, _ = await _audio_runtime(hass, url)
        client = await hass_ws_client(hass)
        handler = _native_ws_handler(client)
        transport = client._writer.transport
        transport.get_extra_info("socket").setsockopt(
            socket.SOL_SOCKET, socket.SO_RCVBUF, 4096
        )
        server_transport = handler._request.transport
        server_transport.get_extra_info("socket").setsockopt(
            socket.SOL_SOCKET, socket.SO_SNDBUF, 8192
        )
        await client.send_json_auto_id(
            {
                "type": "assist_pipeline/run",
                "start_stage": "intent",
                "end_stage": "intent",
                "input": {"text": "Slow downstream socket"},
                "device_id": "slow-downstream-device",
            }
        )
        result = await client.receive_json()
        assert result["success"] is True
        events = []
        async with asyncio.timeout(10):
            while _progressive_text(events) != prefix:
                message = await client.receive_json()
                events.append(message["event"])
        # This is external network flow control on the real recipient socket;
        # no production send method, writer, provider client or queue is replaced.
        transport.pause_reading()
        produce.set()
        await asyncio.wait_for(produced.wait(), 10)
        async with asyncio.timeout(10):
            while not (
                _ws_backlog(handler)[0]
                and server_transport.get_write_buffer_size()
                > server_transport.get_write_buffer_limits()[1]
            ):
                await asyncio.sleep(0)
        pending, retained = _ws_backlog(handler)
        assert 0 < pending <= len(parts) + 10 < MAX_PENDING_MSG
        assert 0 < retained <= sum(len(part.encode()) for part in parts) + 65536
        assert (
            server_transport.get_write_buffer_size()
            <= sum(len(part.encode()) for part in parts) + 65536
        )
        assert not handler._writer_task.done()
        writer = handler._writer_task
        connection = handler._connection
        admin = await _admin_client(hass, hass_ws_client)
        snapshot = await asyncio.wait_for(
            _management_call(admin, entry=entry, section="configuration", action="get"),
            5,
        )
        assert snapshot["title"]
        assert (
            _speech(
                await asyncio.wait_for(
                    _say(hass, entry.entry_id, "Healthy while WebSocket stalled"), 5
                )
            )
            == "Independent healthy"
        )
        observation = {
            "pending_messages": pending,
            "retained_message_bytes": retained,
            "socket_write_bytes": server_transport.get_write_buffer_size(),
            "resources": _resource_footprint(hass),
        }
        if recipient == "abandon":
            transport.abort()
            async with asyncio.timeout(10):
                while not transports[0].is_closing():
                    await asyncio.sleep(0)
        else:
            transport.resume_reading()
            finish.set()
            async with asyncio.timeout(15):
                while not any(event["type"] == "run-end" for event in events):
                    message = await client.receive_json()
                    events.append(message["event"])
            expected = prefix + "".join(parts)
            assert _progressive_text(events) == _final_speech(events) == expected
        transport.resume_reading()
        await client.close()
        await asyncio.wait_for(asyncio.gather(writer, return_exceptions=True), 10)
        assert writer.done() and _ws_backlog(handler)[0] == 0
        assert not connection.subscriptions
        assert (
            _speech(await _say(hass, entry.entry_id, "Healthy after WebSocket"))
            == "Independent healthy"
        )
        record(
            stress_trace,
            "summary",
            downstream_websocket_cases=1,
            downstream_bounded_samples=1,
            pre_gc_resource_samples=2,
            genuine_management_websocket_commands=1,
            recipient=recipient,
            native_backlog=observation,
            provider_requests=3,
            public_turns=3,
        )
    finally:
        produce.set()
        finish.set()
        if client is not None:
            client._writer.transport.resume_reading()
            await client.close()
        await runner.cleanup()


@pytest.mark.parametrize("schedule", ["acknowledge", "reconnect", "expire"])
async def test_assist_broadcast_and_quiet_hours_share_native_output_ownership(
    hass,
    socket_enabled,
    stress_trace,
    schedule,
):
    del socket_enabled
    MockUser(id="real-pool-owner", is_owner=True).add_to_hass(hass)
    await hass.config.async_set_time_zone("UTC")

    async def provider(request):
        await request.json()
        return web.Response(
            body=_chat_sse_text("Ordinary Assist reply"),
            content_type="text/event-stream",
        )

    runner, url = await _endpoint(provider)
    manager = quiet = None
    try:
        _, _, satellites, controls, _, run = await _audio_runtime(hass, url)
        satellite, independent = satellites
        output, untouched = controls
        manager = await async_get_intercom(hass)
        await manager.async_set_enabled(True)
        quiet = await async_get_quiet_hours(hass)
        await quiet.async_update_config(
            {
                "enabled": True,
                "start": "22:00",
                "end": "07:00",
                "max_volume": 0.2,
                "wake_sound": "off",
                "overrides": {},
            }
        )
        await quiet.async_reconcile(now=datetime(2026, 1, 10, 21, 59, tzinfo=UTC))
        await run(satellite)

        async def send(text, target, ttl=60):
            return await hass.services.async_call(
                DOMAIN,
                "broadcast",
                {
                    "message": text,
                    "entity_id": [target.entity_id],
                    "ttl_seconds": ttl,
                },
                blocking=True,
                return_response=True,
                context=Context(user_id="real-pool-owner"),
            )

        def status(item, target=satellite):
            row = next(row for row in manager.history() if row["id"] == item["id"])
            return row["deliveries"][target.entity_id]["status"]

        pending = await send(
            "Broadcast behind ordinary Assist",
            satellite,
            5 if schedule == "expire" else 60,
        )
        assert status(pending) == "queued_busy" and not satellite.announcements
        await quiet.async_reconcile(now=datetime(2026, 1, 10, 22, 0, tzinfo=UTC))
        assert output.volume_level == untouched.volume_level == 0.2
        peer = await send("Independent destination still works", independent)
        await asyncio.wait_for(independent.announce_started.wait(), 10)
        await hass.async_block_till_done()
        assert status(peer, independent) == "delivered"
        assert status(pending) == "queued_busy"
        await hass.services.async_call(
            "media_player",
            "volume_set",
            {
                "entity_id": output.entity_id,
                "volume_level": 0.4,
            },
            blocking=True,
        )
        assert output.volume_level == 0.4
        if schedule == "reconnect":
            satellite._attr_available = False
            satellite.async_write_ha_state()
            assert hass.states.get(satellite.entity_id).state == "unavailable"
            satellite._attr_available = True
            satellite.async_write_ha_state()
            assert satellite.state is AssistSatelliteState.RESPONDING
            assert status(pending) == "queued_busy"
        elif schedule == "expire":
            boundary = asyncio.get_running_loop().create_future()
            timer = asyncio.get_running_loop().call_later(
                5.1, boundary.set_result, None
            )
            try:
                await boundary
            finally:
                timer.cancel()
            assert status(pending) == "expired" and not satellite.announcements
        satellite.announce_release = asyncio.Event()
        satellite.tts_response_finished()
        if schedule != "expire":
            await asyncio.wait_for(satellite.announce_started.wait(), 10)
            assert status(pending) == "delivering"
            assert satellite.state is AssistSatelliteState.RESPONDING
            assert [item.message for item in satellite.announcements] == [
                pending["message"]
            ]
            assert output.volume_level == 0.4
            following = await send(
                "Following Broadcast retains its own queue entry", satellite
            )
            assert status(following) == "queued_busy"
            await quiet.async_reconcile(now=datetime(2026, 1, 11, 7, 0, tzinfo=UTC))
            assert output.volume_level == 0.4 and untouched.volume_level == 0.6
            assert (
                status(pending) == "delivering" and status(following) == "queued_busy"
            )
            assert satellite.state is AssistSatelliteState.RESPONDING
            satellite.announce_release.set()
            await hass.async_block_till_done()
            assert status(pending) == status(following) == "delivered"
            assert [item.message for item in satellite.announcements] == [
                pending["message"],
                following["message"],
            ]
        else:
            assert satellite.state is AssistSatelliteState.IDLE
        await quiet.async_reconcile(now=datetime(2026, 1, 11, 7, 0, tzinfo=UTC))
        assert output.volume_level == 0.4 and untouched.volume_level == 0.6
        assert (
            not manager._queues and not manager._draining and not manager._drain_tasks
        )
        assert satellite.state is independent.state is AssistSatelliteState.IDLE
        assert len(satellite.announcements) == (0 if schedule == "expire" else 2)
        record(
            stress_trace,
            "summary",
            shared_satellite_ownership_cases=1,
            intercom_deliveries=1 if schedule == "expire" else 3,
            intercom_expired=int(schedule == "expire"),
            quiet_ownership_cases=1,
            pre_gc_resource_samples=1,
            schedule=schedule,
            realised_schedule=[
                "assist-response",
                "broadcast-queued",
                "quiet-start",
                "peer-delivered",
                "manual-volume",
                schedule,
            ]
            + (
                ["queue-expired", "assist-acknowledged", "quiet-end"]
                if schedule == "expire"
                else [
                    "assist-acknowledged",
                    "broadcast-delivering",
                    "following-queued",
                    "quiet-end",
                    "announcement-acknowledged",
                    "following-delivered",
                ]
            ),
        )
    finally:
        for satellite in locals().get("satellites", []):
            if satellite.announce_release is not None:
                satellite.announce_release.set()
        if manager is not None:
            await manager.async_shutdown()
        if quiet is not None:
            await quiet.async_shutdown()
        await runner.cleanup()
