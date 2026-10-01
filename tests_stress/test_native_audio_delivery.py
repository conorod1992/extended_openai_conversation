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

from homeassistant.components import stt
from homeassistant.components.assist_pipeline.pipeline import KEY_ASSIST_PIPELINE
from homeassistant.components.assist_satellite.entity import (
    AssistSatelliteConfiguration,
    AssistSatelliteEntity,
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


def _test_wav() -> bytes:
    """Deterministic PCM test recording, without microphone/vendor dependencies."""
    samples = b"".join(
        struct.pack("<h", int(5000 * math.sin(index * math.tau / 16)))
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

    def __init__(self):
        self.events = []

    def async_get_configuration(self):
        return AssistSatelliteConfiguration([], [], 0)

    async def async_set_configuration(self, config):
        pass

    def on_pipeline_event(self, event):
        self.events.append(event)


async def _install_audio_entities(hass, recording):
    with wave.open(io.BytesIO(recording), "rb") as wav:
        pcm = wav.readframes(wav.getnframes())
    entities = {
        "stt": _SoftwareSTT(pcm),
        "tts": _SoftwareTTS(recording),
        "assist_satellite": _SoftwareSatellite(),
    }

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

        async def setup_platform(hass, entry, add_entities, entity=entity):
            add_entities([entity])

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
