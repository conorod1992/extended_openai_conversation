"""Native software audio → STT → EOAI → TTS → satellite acknowledgement."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import io
import json
import math
import struct
from typing import ClassVar
from urllib.parse import urlsplit
import wave

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    MockPlatform,
    MockUser,
    mock_integration,
    mock_platform,
)

from homeassistant.components import conversation, stt
from homeassistant.components.assist_pipeline.pipeline import KEY_ASSIST_PIPELINE
from homeassistant.components.assist_satellite.entity import (
    AssistSatelliteConfiguration,
    AssistSatelliteEntity,
    AssistSatelliteEntityFeature,
    AssistSatelliteState,
)
from homeassistant.components.tts.entity import TextToSpeechEntity
from homeassistant.config_entries import ConfigFlow
from homeassistant.core import Context
from homeassistant.helpers.device_registry import DeviceInfo
from tests_real_ha.test_assist_streaming_speech_processing import _speech_agent
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire
from tests_stress.conftest import record

_AUDIO_DOMAIN = "eoai_audio_fixture"
_TRANSCRIPT = "Deliver the recorded audio acceptance reply"


def _test_wav(period=16) -> bytes:
    """Deterministic PCM test recording, without microphone/vendor dependencies."""
    samples = b"".join(
        struct.pack("<h", int(5000 * math.sin(index * math.tau / period)))
        for index in range(1600)
    )
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(samples)
    return output.getvalue()


class _SoftwareSTT(stt.SpeechToTextEntity):
    _attr_name = "Audio acceptance STT"
    _attr_unique_id = "eoai-audio-stt"
    supported_languages: ClassVar[list] = ["en"]
    supported_formats: ClassVar[list] = [stt.AudioFormats.WAV]
    supported_codecs: ClassVar[list] = [stt.AudioCodecs.PCM]
    supported_bit_rates: ClassVar[list] = [stt.AudioBitRates.BITRATE_16]
    supported_sample_rates: ClassVar[list] = [stt.AudioSampleRates.SAMPLERATE_16000]
    supported_channels: ClassVar[list] = [stt.AudioChannels.CHANNEL_MONO]
    audio_processing = stt.SpeechAudioProcessing(False, False, False)

    def __init__(self, pcm):
        self.pcm = pcm
        self.hold = False
        self.started = asyncio.Event()
        self.recordings = []

    async def async_process_audio_stream(self, metadata, stream):
        assert self.check_metadata(metadata)
        captured = b"".join([chunk async for chunk in stream])
        assert captured == self.pcm
        self.recordings.append(len(captured))
        self.started.set()
        if self.hold:
            await asyncio.Event().wait()
        return stt.SpeechResult(_TRANSCRIPT, stt.SpeechResultState.SUCCESS)


class _SoftwareTTS(TextToSpeechEntity):
    _attr_name = "Audio acceptance TTS"
    _attr_unique_id = "eoai-audio-tts"
    _attr_supported_languages: ClassVar[list] = ["en"]
    _attr_default_language = "en"

    def __init__(self, recording):
        self.recording = recording
        self.messages = []

    async def async_get_tts_audio(self, message, language, options=None):
        assert language == "en"
        self.messages.append(message)
        return "wav", self.recording


class _SoftwareSatellite(AssistSatelliteEntity):
    _attr_name = "Audio acceptance satellite"
    _attr_unique_id = "eoai-audio-satellite"
    _attr_tts_options: ClassVar[dict] = {"preferred_format": "wav"}
    _attr_device_info = DeviceInfo(
        identifiers={(_AUDIO_DOMAIN, "satellite")}, name="Software audio output"
    )

    def __init__(self, suffix=None, *, announce=False):
        self.events = []
        self.announcements = []
        self.announce_started = asyncio.Event()
        self.announce_release = None
        if suffix:
            self._attr_name = f"Audio acceptance satellite {suffix}"
            self._attr_unique_id = f"eoai-audio-satellite-{suffix}"
            self._attr_device_info = DeviceInfo(
                identifiers={(_AUDIO_DOMAIN, suffix)}, name=f"Software output {suffix}"
            )
        if announce:
            self._attr_supported_features = AssistSatelliteEntityFeature.ANNOUNCE

    async def async_announce(self, announcement):
        # HA's native service blocks until the software recipient finishes playback.
        self.announcements.append(announcement)
        self.announce_started.set()
        if self.announce_release is not None:
            await self.announce_release.wait()

    def async_get_configuration(self):
        return AssistSatelliteConfiguration([], [], 0)

    async def async_set_configuration(self, config):
        pass

    def on_pipeline_event(self, event):
        self.events.append(event)


async def _install_audio_entities(
    hass, recording, *, speech=None, tts=None, satellites=None, media_players=None
):
    with wave.open(io.BytesIO(recording), "rb") as wav:
        pcm = wav.readframes(wav.getnframes())
    entities = {
        "stt": speech or _SoftwareSTT(pcm),
        "tts": tts or _SoftwareTTS(recording),
        "assist_satellite": satellites[0] if satellites else _SoftwareSatellite(),
    }
    if media_players:
        entities["media_player"] = media_players[0]

    async def setup_entry(hass, entry):
        await hass.config_entries.async_forward_entry_setups(entry, list(entities))
        return True

    async def unload_entry(hass, entry):
        return await hass.config_entries.async_unload_platforms(entry, list(entities))

    class AudioFlow(ConfigFlow, domain=_AUDIO_DOMAIN):
        VERSION = 1

    mock_integration(
        hass,
        MockModule(
            _AUDIO_DOMAIN,
            dependencies=["stt", "tts", "assist_satellite", "assist_pipeline"],
            async_setup_entry=setup_entry,
            async_unload_entry=unload_entry,
        ),
    )
    mock_platform(hass, f"{_AUDIO_DOMAIN}.config_flow")
    for platform, entity in entities.items():
        members = (
            satellites if platform == "assist_satellite" and satellites else [entity]
        )
        if platform == "media_player" and media_players:
            members = media_players

        async def setup_platform(hass, entry, add_entities, members=members):
            add_entities(members)

        mock_platform(
            hass,
            f"{_AUDIO_DOMAIN}.{platform}",
            MockPlatform(async_setup_entry=setup_platform),
        )
    entry = MockConfigEntry(domain=_AUDIO_DOMAIN, title="Software audio boundary")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert all(
        entity.entity_id and hass.states.get(entity.entity_id)
        for entity in entities.values()
    )
    return entities, pcm


@pytest.mark.parametrize("interruption", ["cancel_stt", "disconnect_output"])
async def test_recorded_audio_native_assist_delivery_recovers(
    hass,
    hass_client,
    monkeypatch,
    stress_trace,
    interruption,
):
    """Real native audio stages preserve origin and recover without duplicate output."""
    owner = MockUser(
        id="audio-acceptance-owner", name="Audio owner", is_owner=True
    ).add_to_hass(hass)
    # EOAI setup installs Recorder's bootstrap-owned registry listener before
    # software entity platforms subscribe to registry updates.
    agent = await _speech_agent(hass)
    recording = _test_wav()
    entities, pcm = await _install_audio_entities(hass, recording)
    speech, tts, satellite = (
        entities[key] for key in ("stt", "tts", "assist_satellite")
    )
    store = hass.data[KEY_ASSIST_PIPELINE].pipeline_store
    pipeline = await store.async_create_item(
        {
            "name": "EOAI native audio",
            "language": "en",
            "conversation_language": "en",
            "conversation_engine": agent.entity_id,
            "stt_engine": speech.entity_id,
            "stt_language": "en",
            "tts_engine": tts.entity_id,
            "tts_language": "en",
            "tts_voice": None,
            "wake_word_entity": None,
            "wake_word_id": None,
            "prefer_local_intents": False,
        }
    )
    store.async_set_preferred_item(pipeline.id)
    replies = (
        ["Recovered audio response."]
        if interruption == "cancel_stt"
        else ["First audio response.", "Recovered audio response."]
    )
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text(text) for text in replies])
    origins = []
    original_process = agent.async_process

    async def observe_origin(user_input):
        origins.append(user_input)
        return await original_process(user_input)

    monkeypatch.setattr(agent, "async_process", observe_origin)
    http = await hass_client(hass)
    context = Context(user_id=owner.id)

    async def audio_stream():
        # The native satellite API takes PCM frames from the test WAV recording.
        for start in range(0, len(pcm), 640):
            yield pcm[start : start + 640]
            await asyncio.sleep(0)

    async def run_audio():
        await asyncio.wait_for(
            satellite.async_accept_pipeline_from_satellite(
                audio_stream(), context=context
            ),
            15,
        )
        errors = [event for event in satellite.events if event.type.value == "error"]
        assert errors == []

    async def fetch_spoken_audio():
        event = next(
            event
            for event in reversed(satellite.events)
            if event.type.value == "tts-end"
        )
        response = await http.get(urlsplit(event.data["tts_output"]["url"]).path)
        assert response.status == 200
        body = await response.read()
        with wave.open(io.BytesIO(body), "rb") as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (
                16000,
                1,
                2,
            )
            assert wav.readframes(wav.getnframes()) == pcm
        assert satellite.state == AssistSatelliteState.RESPONDING
        return body

    if interruption == "cancel_stt":
        speech.hold = True
        held = asyncio.create_task(run_audio())
        try:
            await asyncio.wait_for(speech.started.wait(), 5)
            held.cancel()
            with pytest.raises(asyncio.CancelledError):
                await held
        finally:
            if not held.done():
                held.cancel()
                with suppress(asyncio.CancelledError):
                    await held
        speech.hold = False
        assert wire.requests == [] and tts.messages == []
        assert satellite.state == AssistSatelliteState.IDLE
    else:
        await run_audio()
        await fetch_spoken_audio()
        assert tts.messages == ["First audio response."]
        # Software playback disconnects after receiving valid audio but before
        # acknowledgement. Reconnect clears that abandoned native response.
        satellite._attr_available = False
        satellite.async_write_ha_state()
        assert hass.states.get(satellite.entity_id).state == "unavailable"
        satellite._attr_available = True
        satellite.tts_response_finished()
        assert satellite.state == AssistSatelliteState.IDLE

    satellite.events.clear()
    await run_audio()
    await fetch_spoken_audio()
    assert tts.messages == replies
    assert (
        len([event for event in satellite.events if event.type.value == "tts-end"]) == 1
    )
    assert len(wire.requests) == len(replies)
    assert _TRANSCRIPT in json.dumps(wire.requests[-1]["body"])
    origin = origins[-1]
    assert origin.text == _TRANSCRIPT and origin.context.user_id == owner.id
    assert origin.device_id == satellite.registry_entry.device_id
    assert origin.satellite_id == satellite.entity_id
    satellite.tts_response_finished()
    assert satellite.state == AssistSatelliteState.IDLE
    assert hass.states.get(satellite.entity_id).state == "idle"
    record(
        stress_trace,
        "summary",
        journey="native_audio_delivery",
        interruption=interruption,
        audio_deliveries=1,
        audio_playback_acknowledgements=1,
        provider_requests=len(wire.requests),
        public_turns=len(origins),
        stt_recordings=len(speech.recordings),
        audio_frames=1600,
    )


async def test_live_assist_language_change_reaches_same_satellite_session(
    hass,
    hass_client,
    monkeypatch,
    stress_trace,
):
    """A real Assist pipeline language change reaches STT, EOAI and TTS in place."""
    from homeassistant.components.assist_pipeline import async_update_pipeline

    owner = MockUser(
        id="audio-language-owner", name="Audio language owner", is_owner=True
    ).add_to_hass(hass)
    agent = await _speech_agent(hass)
    recording = _test_wav()
    transcripts = {"en": "Say hello in English", "fr": "Répondez en français"}

    class MultilingualSTT(_SoftwareSTT):
        supported_languages: ClassVar[list] = ["en", "fr"]

        def __init__(self, pcm):
            super().__init__(pcm)
            self.languages = []

        async def async_process_audio_stream(self, metadata, stream):
            assert self.check_metadata(metadata)
            captured = b"".join([chunk async for chunk in stream])
            assert captured == self.pcm
            assert metadata.language in transcripts
            self.languages.append(metadata.language)
            return stt.SpeechResult(
                transcripts[metadata.language], stt.SpeechResultState.SUCCESS
            )

    class MultilingualTTS(_SoftwareTTS):
        _attr_supported_languages: ClassVar[list] = ["en", "fr"]

        def __init__(self, audio):
            super().__init__(audio)
            self.deliveries = []

        async def async_get_tts_audio(self, message, language, options=None):
            self.deliveries.append((message, language))
            return "wav", self.recording

    speech = MultilingualSTT(recording)
    tts = MultilingualTTS(recording)
    satellite = _SoftwareSatellite()
    entities, pcm = await _install_audio_entities(
        hass, recording, speech=speech, tts=tts, satellites=[satellite]
    )
    assert entities["stt"] is speech

    store = hass.data[KEY_ASSIST_PIPELINE].pipeline_store
    pipeline = await store.async_create_item(
        {
            "name": "Live EOAI language change",
            "language": "en",
            "conversation_language": "*",
            "conversation_engine": agent.entity_id,
            "stt_engine": speech.entity_id,
            "stt_language": "en",
            "tts_engine": tts.entity_id,
            "tts_language": "en",
            "tts_voice": None,
            "wake_word_entity": None,
            "wake_word_id": None,
            "prefer_local_intents": False,
        }
    )
    store.async_set_preferred_item(pipeline.id)
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_text("Hello **there**."),
            _chat_sse_text("Bonjour **tout le monde**."),
        ],
    )
    user_inputs = []
    process = agent.async_process

    async def observe_input(user_input):
        user_inputs.append(user_input)
        return await process(user_input)

    monkeypatch.setattr(agent, "async_process", observe_input)
    http = await hass_client(hass)

    async def send_turn(expected_transcript: str):
        async def audio_stream():
            for start in range(0, len(pcm), 640):
                yield pcm[start : start + 640]
                await asyncio.sleep(0)

        await asyncio.wait_for(
            satellite.async_accept_pipeline_from_satellite(
                audio_stream(), context=Context(user_id=owner.id)
            ),
            15,
        )
        assert not [event for event in satellite.events if event.type.value == "error"]
        event = next(
            item
            for item in reversed(satellite.events)
            if item.type.value == "tts-end"
        )
        response = await http.get(urlsplit(event.data["tts_output"]["url"]).path)
        assert response.status == 200
        await response.read()
        assert user_inputs[-1].text == expected_transcript
        assert user_inputs[-1].context.user_id == owner.id
        satellite.tts_response_finished()
        satellite.events.clear()

    await send_turn(transcripts["en"])
    conversation_id = user_inputs[0].conversation_id
    assert user_inputs[0].language == "en"

    await async_update_pipeline(
        hass,
        pipeline,
        language="fr",
        stt_language="fr",
        tts_language="fr",
    )
    await send_turn(transcripts["fr"])

    assert speech.languages == ["en", "fr"]
    assert [item.language for item in user_inputs] == ["en", "fr"]
    assert [item.conversation_id for item in user_inputs] == [
        conversation_id,
        conversation_id,
    ]
    assert tts.deliveries == [
        ("Hello there.", "en"),
        ("Bonjour tout le monde.", "fr"),
    ]
    assert [request["body"]["messages"][-1]["content"] for request in wire.requests] == [
        transcripts["en"],
        transcripts["fr"],
    ]
    assert len(wire.requests) == 2
    record(
        stress_trace,
        "summary",
        journey="live_assist_language_change",
        pipeline_language_changes=1,
        same_conversation_id=True,
        stt_languages=speech.languages,
        tts_languages=[language for _, language in tts.deliveries],
    )


async def test_two_native_audio_journeys_interleave_without_cross_owned_output(
    hass,
    hass_client,
    monkeypatch,
    stress_trace,
):
    """Distinct real STT/SDK/TTS/satellite runs survive one targeted TTS failure."""
    from collections import Counter

    users = {
        key: MockUser(
            id=f"audio-concurrent-{key}", name=f"Audio user {key}", is_owner=key == "a"
        ).add_to_hass(hass)
        for key in ("a", "b")
    }
    agent = await _speech_agent(hass)
    recordings = {"a": _test_wav(16), "b": _test_wav(19)}
    pcm = {}
    for key, recording in recordings.items():
        with wave.open(io.BytesIO(recording), "rb") as wav:
            pcm[key] = wav.readframes(wav.getnframes())
    assert pcm["a"] != pcm["b"]
    transcripts = {key: f"Distinct native recording for satellite {key}" for key in pcm}
    started = {key: asyncio.Event() for key in pcm}
    release_stt = {key: asyncio.Event() for key in pcm}
    b_tts_started, release_b_tts = asyncio.Event(), asyncio.Event()
    a_tts_failed = asyncio.Event()
    stages = []

    class InterleavedSTT(_SoftwareSTT):
        async def async_process_audio_stream(self, metadata, stream):
            assert self.check_metadata(metadata)
            captured = b"".join([chunk async for chunk in stream])
            key = next(key for key, value in pcm.items() if value == captured)
            self.recordings.append(key)
            stages.append(f"stt-{key}-started")
            started[key].set()
            await release_stt[key].wait()
            return stt.SpeechResult(transcripts[key], stt.SpeechResultState.SUCCESS)

    class InterleavedTTS(_SoftwareTTS):
        async def async_get_tts_audio(self, message, language, options=None):
            assert language == "en"
            self.messages.append(message)
            stages.append(message)
            if message == "Reply for satellite A.":
                await b_tts_started.wait()
                assert not release_b_tts.is_set()
                stages.append("a-tts-failed-while-b-held")
                a_tts_failed.set()
                return "wav", None
            if message == "Reply for satellite B.":
                b_tts_started.set()
                await release_b_tts.wait()
                return "wav", recordings["b"]
            assert message == "Recovered reply for satellite A."
            return "wav", recordings["a"]

    satellites = {key: _SoftwareSatellite(key) for key in pcm}
    speech, tts = InterleavedSTT(pcm["a"]), InterleavedTTS(recordings["a"])
    await _install_audio_entities(
        hass,
        recordings["a"],
        speech=speech,
        tts=tts,
        satellites=list(satellites.values()),
    )
    assert len({sat.registry_entry.device_id for sat in satellites.values()}) == 2
    store = hass.data[KEY_ASSIST_PIPELINE].pipeline_store
    pipeline = await store.async_create_item(
        {
            "name": "Concurrent EOAI native audio",
            "language": "en",
            "conversation_language": "en",
            "conversation_engine": agent.entity_id,
            "stt_engine": speech.entity_id,
            "stt_language": "en",
            "tts_engine": tts.entity_id,
            "tts_language": "en",
            "tts_voice": None,
            "wake_word_entity": None,
            "wake_word_id": None,
            "prefer_local_intents": False,
        }
    )
    store.async_set_preferred_item(pipeline.id)
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_text(text)
            for text in (
                "Reply for satellite A.",
                "Reply for satellite B.",
                "Recovered reply for satellite A.",
            )
        ],
    )
    original_send = wire.send
    a_provider_started, b_provider_started, a_forwarded = (
        asyncio.Event() for _ in range(3)
    )
    first_a = True

    async def interleaved_send(request, *args, **kwargs):
        nonlocal first_a
        content = json.loads(request.content)["messages"][-1]["content"]
        if content == transcripts["a"] and first_a:
            first_a = False
            a_provider_started.set()
            await b_provider_started.wait()
            response = await original_send(request, *args, **kwargs)
            a_forwarded.set()
            return response
        if content == transcripts["b"]:
            assert a_provider_started.is_set()
            stages.append("both-provider-requests-admitted")
            b_provider_started.set()
            await a_forwarded.wait()
        return await original_send(request, *args, **kwargs)

    from tests_real_ha.test_provider_wire_e2e import _raw_client

    monkeypatch.setattr(_raw_client(agent)._client, "send", interleaved_send)
    origins = []
    original_process = agent.async_process

    async def observe(user_input):
        origins.append(user_input)
        return await original_process(user_input)

    monkeypatch.setattr(agent, "async_process", observe)
    http = await hass_client(hass)

    async def run(key):
        async def audio():
            for offset in range(0, len(pcm[key]), 640):
                yield pcm[key][offset : offset + 640]
                await asyncio.sleep(0)

        await satellites[key].async_accept_pipeline_from_satellite(
            audio(), context=Context(user_id=users[key].id)
        )

    async def output(key):
        events = [
            event for event in satellites[key].events if event.type.value == "tts-end"
        ]
        assert len(events) == 1
        response = await http.get(urlsplit(events[0].data["tts_output"]["url"]).path)
        assert response.status == 200
        with wave.open(io.BytesIO(await response.read()), "rb") as wav:
            assert wav.readframes(wav.getnframes()) == pcm[key]
        assert satellites[key].state == AssistSatelliteState.RESPONDING
        satellites[key].tts_response_finished()
        assert satellites[key].state == AssistSatelliteState.IDLE

    tasks = []
    try:
        tasks.append(asyncio.create_task(run("a")))
        await asyncio.wait_for(started["a"].wait(), 5)
        tasks.append(asyncio.create_task(run("b")))
        await asyncio.wait_for(started["b"].wait(), 5)
        assert not tasks[0].done() and not tasks[1].done()
        release_stt["a"].set()
        await asyncio.wait_for(a_provider_started.wait(), 5)
        release_stt["b"].set()
        await asyncio.wait_for(b_tts_started.wait(), 10)
        await asyncio.wait_for(tasks[0], 10)
        await asyncio.wait_for(a_tts_failed.wait(), 5)
        assert "a-tts-failed-while-b-held" in stages
        # HA publishes a TTS URL while synthesis continues in the background.
        # The failure is observed by the actual output consumer, not an invented
        # pipeline error event. B's independent synthesis is still blocked.
        assert not release_b_tts.is_set()
        outputs_a = [
            event for event in satellites["a"].events if event.type.value == "tts-end"
        ]
        assert len(outputs_a) == 1
        failed_output = await http.get(
            urlsplit(outputs_a[0].data["tts_output"]["url"]).path
        )
        assert failed_output.status >= 400
        await failed_output.read()
        assert not [
            event for event in satellites["b"].events if event.type.value == "error"
        ]
        release_b_tts.set()
        await asyncio.wait_for(tasks[1], 10)
        await output("b")
        # The software A recipient reconnects after its failed native output,
        # clearing that response exactly as the existing disconnect journey does.
        satellites["a"]._attr_available = False
        satellites["a"].async_write_ha_state()
        satellites["a"]._attr_available = True
        satellites["a"].tts_response_finished()
        satellites["a"].events.clear()
        await asyncio.wait_for(run("a"), 15)
        await output("a")
        assert Counter(
            (item.text, item.context.user_id, item.satellite_id, item.device_id)
            for item in origins
        ) == Counter(
            {
                (
                    transcripts[key],
                    users[key].id,
                    satellites[key].entity_id,
                    satellites[key].registry_entry.device_id,
                ): 2 if key == "a" else 1
                for key in pcm
            }
        )
        assert len(wire.requests) == 3
        assert speech.recordings == ["a", "b", "a"]
        assert tts.messages == [
            "Reply for satellite A.",
            "Reply for satellite B.",
            "Recovered reply for satellite A.",
        ]
        assert "both-provider-requests-admitted" in stages
        record(
            stress_trace,
            "summary",
            layer="native-ha-audio",
            concurrent_audio_journeys=2,
            audio_interleavings=1,
            targeted_tts_failures=1,
            audio_deliveries=2,
            audio_playback_acknowledgements=2,
            stt_recordings=3,
            public_turns=3,
            provider_requests=3,
            native_audio_recoveries=1,
        )
    finally:
        for event in (*release_stt.values(), release_b_tts):
            event.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_responses_native_audio_executes_tool_once_and_next_voice_turn_is_healthy(
    hass,
    monkeypatch,
    stress_trace,
):
    """Native audio covers Responses tool continuation and a healthy later turn."""
    from copy import deepcopy

    from custom_components.extended_openai_conversation_responses.const import (
        API_MODE_RESPONSES,
        CONF_API_MODE,
        CONF_CHAT_MODEL,
        CONF_FUNCTION_TOOLS,
        DEFAULT_CONF_FUNCTION_TOOLS,
    )
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
    )
    from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
    from tests_real_ha.test_provider_wire_e2e import (
        _responses_sse_text,
        _responses_sse_tool_call,
    )

    owner = MockUser(
        id="responses-audio-owner", name="Responses audio owner", is_owner=True
    ).add_to_hass(hass)
    entity_id = "light.responses_audio_probe"
    hass.states.async_set(entity_id, "on")
    async_expose_entity(hass, conversation.DOMAIN, entity_id, True)
    effects = []

    async def turn_off(call):
        effects.append((call.domain, call.service, dict(call.data)))
        hass.states.async_set(entity_id, "off")

    hass.services.async_register("light", "turn_off", turn_off)
    tool = deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])
    entry = _make_entry(
        "Responses native audio",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_RESPONSES,
            CONF_CHAT_MODEL: "gpt-5.6",
            "reasoning_effort": "none",
            CONF_FUNCTION_TOOLS: [tool],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    recording = _test_wav()
    entities, pcm = await _install_audio_entities(hass, recording)
    speech, tts, satellite = (
        entities[key] for key in ("stt", "tts", "assist_satellite")
    )
    store = hass.data[KEY_ASSIST_PIPELINE].pipeline_store
    pipeline = await store.async_create_item(
        {
            "name": "EOAI Responses native audio",
            "language": "en",
            "conversation_language": "en",
            "conversation_engine": agent.entity_id,
            "stt_engine": speech.entity_id,
            "stt_language": "en",
            "tts_engine": tts.entity_id,
            "tts_language": "en",
            "tts_voice": None,
            "wake_word_entity": None,
            "wake_word_id": None,
            "prefer_local_intents": False,
        }
    )
    store.async_set_preferred_item(pipeline.id)

    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _responses_sse_tool_call(
                "responses-audio-action",
                "execute_services",
                {
                    "list": [
                        {
                            "domain": "light",
                            "service": "turn_off",
                            "service_data": {"entity_id": [entity_id]},
                        }
                    ]
                },
            ),
            _responses_sse_text("Voice action complete"),
            _responses_sse_text("Follow-up voice healthy"),
        ],
    )

    async def audio_stream():
        for start in range(0, len(pcm), 640):
            yield pcm[start : start + 640]
            await asyncio.sleep(0)

    async def run_once():
        await asyncio.wait_for(
            satellite.async_accept_pipeline_from_satellite(
                audio_stream(), context=Context(user_id=owner.id)
            ),
            15,
        )
        assert not [event for event in satellite.events if event.type.value == "error"]

    await run_once()
    assert effects == [("light", "turn_off", {"entity_id": [entity_id]})]
    assert hass.states.get(entity_id).state == "off"
    assert tts.messages == ["Voice action complete"]
    assert len(wire.requests) == 2
    continuation = wire.requests[1]["body"]
    outputs = [
        item
        for item in continuation["input"]
        if item.get("type") == "function_call_output"
    ]
    assert len(outputs) == 1
    assert outputs[0]["call_id"] == "responses-audio-action"
    satellite.tts_response_finished()
    satellite.events.clear()

    await run_once()
    assert effects == [("light", "turn_off", {"entity_id": [entity_id]})]
    assert tts.messages == ["Voice action complete", "Follow-up voice healthy"]
    assert len(wire.requests) == 3
    follow_up = wire.requests[2]["body"]["input"]
    historical_calls = [
        item for item in continuation["input"] if item.get("type") == "function_call"
    ]
    follow_up_calls = [
        item for item in follow_up if item.get("type") == "function_call"
    ]
    assert len(historical_calls) == len(follow_up_calls) == 1
    assert follow_up_calls == historical_calls
    assert follow_up_calls[0]["call_id"] == "responses-audio-action"
    assert satellite.state == AssistSatelliteState.RESPONDING
    satellite.tts_response_finished()
    assert satellite.state == AssistSatelliteState.IDLE

    record(
        stress_trace,
        "summary",
        responses_native_audio_tool_journeys=1,
        responses_native_audio_tool_effects=1,
        responses_native_audio_healthy_followups=1,
        provider_requests=3,
        public_turns=2,
    )
