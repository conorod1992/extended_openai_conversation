"""Real HA Assist coverage for streaming spoken-response processing."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
    CONF_SPEECH_PROCESSING_ENABLED,
    CONF_SPEECH_REGEX_REPLACEMENTS,
    CONF_SPEECH_STRIP_MARKDOWN,
    CONF_SPEECH_STRIP_URLS,
    DEFAULT_CONF_FUNCTION_TOOLS,
)
from homeassistant.components import conversation, tts
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_tool_call,
    _install_wire,
    _response_object,
    _response_text_item,
    _responses_sse_text,
)

_RAW_RESPONSE = (
    "**Hello** see https://example.com/path and "
    "[docs](https://example.org/help) done."
)
_SPOKEN_RESPONSE = "Hello see and docs done."


@pytest.mark.parametrize("raw,expected,progressive_expected", [
    ("Result\n5.\nDone", "Result\n5.\nDone", None),
    ("> 5.\n", "5.", None),
    ("The switch is ***off*** [a.com](https://a.com) [b.com](https://b.com) ***now***.", "The switch is off now.", None),
    (r"\![a photograph](https://example.com/image.png)", "!a photograph", None),
    ("Result\n5.\nDone\n***off*** [a.com](https://a.com) [b.com](https://b.com) ***now***. " + r"\![photo](https://example.com/image.png)" + "\n2) [Check documentation](https://example.com)\n3) https://example.com.\n4) https://a.com **[b.com](https://b.com)**\n5) **https://a.com https://b.com**\nBefore **https://a.com https://b.com** after.", "Result\n5.\nDone\noff now. !photo\n2. Check documentation\nBefore after.", "Result\n5.\nDone\noff now. !photo\n2. Check documentation\nBefore after."),
    (r"\*literal\* `\*code\*`", r"*literal* \*code\*", None),
    ("Heading\n===", "Heading", None),
    (r"off now. \!photo", "off now. !photo", None),
    (r"*hello \!world*", "hello !world", None),
    (r"**outer _hello \!world_ tail**", "outer hello !world tail", None),
    ("Off now . Next !", "Off now. Next!", "Off now . Next !"),
    ("Read https://demo\u2013alias@example.invalid/guide first.", "Read first.", None),
    ("Router: http://router.local:8080\u2014never press Reset.", "Router: \u2014never press Reset.", None),
    ('Open "https://example.invalid"\u2014contact@example.invalid can help.', "Open \u2014contact@example.invalid can help.", None),
    ("**Open https://example.invalid**\u2014contact@example.invalid can help.", "Open\u2014contact@example.invalid can help.", None),
    ("Open **https://example.invalid/guide**,\u2014never press Reset.", "Open,\u2014never press Reset.", None),
    ("Open \u201chttps://example.invalid/guide\u201d\u2014\u201ccontact@example.invalid\u201d can help.", "Open \u2014\u201ccontact@example.invalid\u201d can help.", None),
    ("Open **https://example.invalid/guide**\u2014**never**\u2014press Reset.", "Open \u2014never\u2014press Reset.", None),
], ids=["standalone-number", "quoted-number", "consecutive-links", "escaped-image", "combined", "escaped-punctuation-code", "setext-equals", "literal-spacing", "single-emphasis-spacing", "nested-emphasis-spacing", "punctuation-attachment", "dashed-url-userinfo", "prose-dash-after-port", "dashed-prose-after-quoted-url", "dashed-prose-after-emphasized-phrase", "comma-before-dashed-negation", "separate-quoted-prose", "separate-emphasized-negation"])
async def test_assist_speech_regressions_preserve_provider_history(
    hass, hass_ws_client, monkeypatch, raw, expected, progressive_expected
):
    from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS

    agent = await _speech_agent(hass)
    assert await async_setup_component(hass, "assist_pipeline", {})
    _install_wire(monkeypatch, agent, [_chat_sse_deltas(list(raw))])
    events = await _run_assist(await hass_ws_client(hass), pipeline_id=agent.entity_id,
                               conversation_id="speech-regressions")
    # Ordinary punctuation attachment remains a completed-response stage;
    # escaped punctuation retains its authored separation in both paths.
    assert _progressive_text(events).strip() == (
        expected if progressive_expected is None else progressive_expected
    )
    assert _final_speech(events) == expected
    assert any(
        item.content == raw
        for log in hass.data[DATA_CHAT_LOGS].values()
        for item in log.content
        if isinstance(item, conversation.AssistantContent)
    )


@pytest.mark.parametrize("formatted", [r"**off** now. \!photo TOKEN", r"*off now. \!photo* TOKEN"], ids=["strong", "single"])
async def test_assist_custom_replacements_preserve_literal_spacing_and_history(
    hass, hass_ws_client, monkeypatch, formatted,
):
    from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS

    raw = formatted
    agent = await _speech_agent(hass, **{CONF_SPEECH_REGEX_REPLACEMENTS: [
        {"pattern": "photo", "replacement": "image"},
        {"pattern": "TOKEN", "replacement": " !"},
    ]})
    assert await async_setup_component(hass, "assist_pipeline", {})
    _install_wire(monkeypatch, agent, [_chat_sse_deltas(list(raw))])
    events = await _run_assist(await hass_ws_client(hass), pipeline_id=agent.entity_id,
                               conversation_id="literal-spacing-replacements")
    assert _final_speech(events) == "off now. !image!"
    assert agent.supports_streaming is False
    assert _progressive_text(events) == ""
    assert any(
        item.content == raw
        for log in hass.data[DATA_CHAT_LOGS].values()
        for item in log.content
        if isinstance(item, conversation.AssistantContent)
    )


@pytest.mark.parametrize(
    "case",
    ["chat", "responses", "completed-responses", "nonstreaming-tts", "short", "tool", "built-ins"],
)
async def test_assist_tts_engine_receives_cleaned_text(hass, hass_ws_client, monkeypatch, case):
    """Observe the actual ResultStream input instead of the intent or TTS event."""
    from custom_components.extended_openai_conversation_responses import request
    from homeassistant.components.assist_pipeline import websocket_api
    from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS

    use_replacements = case != "built-ins"
    raw = "**HA** says the window is closed. See https://example.invalid/manual for more information about the kitchen sensor."
    spoken = "HA says the window is closed. See for more information about the kitchen sensor."
    if case in {"short", "tool"}:
        raw, spoken = "**HA** is ready.", "HA is ready."
    if use_replacements:
        spoken = spoken.replace("HA", "Home Assistant", 1)
    api = API_MODE_RESPONSES if case in {"responses", "completed-responses"} else API_MODE_CHAT_COMPLETIONS
    agent = await _speech_agent(hass, **{
        CONF_API_MODE: api,
        CONF_SPEECH_REGEX_REPLACEMENTS: (
            [{"pattern": "HA", "replacement": "Home Assistant"}] if use_replacements else []
        ),
        CONF_FUNCTION_TOOLS: [{
            "spec": {"name": "check_window", "description": "Check window", "parameters": {"type": "object", "properties": {}}},
            "function": {"type": "template", "value_template": "closed"},
        }],
    })
    assert await async_setup_component(hass, "assist_pipeline", {})

    if case == "completed-responses":
        capabilities = request.request_model_capabilities
        monkeypatch.setattr(
            request, "request_model_capabilities",
            lambda *args, **kwargs: {**capabilities(*args, **kwargs), "streaming": False},
        )
        replies = [(200, _response_object("completed-speech", [_response_text_item(raw)]))]
    elif api == API_MODE_RESPONSES:
        replies = [_responses_sse_text(raw)]
    else:
        replies = [_chat_sse_deltas(list(raw))]
    if case == "tool":
        tool = _chat_sse_tool_call(name="check_window", arguments={})
        tool = tool.replace(b'"tool_calls": [', b'"content": "**HA** is checking. ", "tool_calls": [', 1)
        replies.insert(0, tool)
    wire = _install_wire(monkeypatch, agent, replies)

    original_pipeline_lookup = websocket_api.async_get_pipeline

    def pipeline_with_tts(hass, pipeline_id=None):
        return replace(
            original_pipeline_lookup(hass, pipeline_id=pipeline_id),
            tts_engine="tts.recording", tts_language="en",
        )

    spoken_inputs = []
    collect_tasks = []

    def record_completed(*, message, **_kwargs):
        spoken_inputs.append(("complete", message))
        return object()

    def record_stream(*, message_stream, **_kwargs):
        async def collect():
            spoken_inputs.append(("stream", "".join([part async for part in message_stream])))
        collect_tasks.append(asyncio.create_task(collect()))
        return object()

    monkeypatch.setattr(websocket_api, "async_get_pipeline", pipeline_with_tts)
    monkeypatch.setattr(
        tts, "async_create_stream",
        lambda **_kwargs: tts.ResultStream(
            hass=hass, token="speech-test", extension="wav", content_type="audio/wav",
            engine="tts.recording", use_file_cache=False, language="en", options={},
            supports_streaming_input=case != "nonstreaming-tts",
            _manager=SimpleNamespace(
                async_cache_message_in_memory=record_completed,
                async_cache_message_stream_in_memory=record_stream,
                token_to_stream={},
                token_to_stream_cleanup=SimpleNamespace(schedule=lambda: None),
            ),
        ),
    )
    try:
        events = await _run_assist(
            await hass_ws_client(hass), pipeline_id=agent.entity_id,
            conversation_id="actual-tts-speech", end_stage="tts",
        )
        async with asyncio.timeout(5):
            await asyncio.gather(*collect_tasks)
    finally:
        for task in collect_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*collect_tasks, return_exceptions=True)

    assert not [event for event in events if event["type"] == "error"]
    assert _final_speech(events) == spoken
    assert spoken_inputs == [("stream" if case == "built-ins" else "complete", spoken)]
    assert wire.requests[-1]["body"]["stream"] is (case != "completed-responses")
    assert len(wire.requests) == (2 if case == "tool" else 1)
    assert _progressive_text(events) == ("" if use_replacements else spoken)
    assert any(
        item.content == raw
        for log in hass.data[DATA_CHAT_LOGS].values()
        for item in log.content
        if isinstance(item, conversation.AssistantContent)
    )


def _chat_sse_deltas(parts: list[str]) -> bytes:
    """Return one real Chat Completions SSE response split across provider deltas."""
    events: list[str] = []
    for index, content in enumerate(parts):
        delta: dict[str, Any] = {"content": content}
        if index == 0:
            delta["role"] = "assistant"
        chunk = {
            "id": "chatcmpl-assist-streaming-speech",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-5.6",
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": "stop" if index == len(parts) - 1 else None,
                }
            ],
        }
        events.append(f"data: {json.dumps(chunk)}\n\n")
    events.append("data: [DONE]\n\n")
    return "".join(events).encode()


@pytest.mark.parametrize("preamble", ["", "Let me check. "])
@pytest.mark.parametrize("final_text", [False, True])
@pytest.mark.parametrize("tool_rounds", [1, 2])
async def test_conditional_final_answer_reaches_actual_assist_progress(hass, hass_ws_client, monkeypatch, preamble, final_text, tool_rounds):
    from custom_components.extended_openai_conversation_responses.const import (
        CONF_CONTINUE_CONVERSATION, CONTINUE_CONVERSATION_CONDITIONAL,
    )
    tool = {"spec": {"name": "check_door", "description": "Check door",
            "parameters": {"type": "object", "properties": {}}},
            "function": {"type": "template", "value_template": "unlocked"}}
    agent = await _speech_agent(hass, **{CONF_CONTINUE_CONVERSATION: CONTINUE_CONVERSATION_CONDITIONAL,
                                        CONF_FUNCTION_TOOLS: [tool]})
    assert await async_setup_component(hass, "assist_pipeline", {})
    question = "The door is unlocked. Would you like me to lock it?"

    call_serial = 0
    def tool_sse(name, arguments, text=""):
        nonlocal call_serial
        call_serial += 1
        chunk = {"id": "chatcmpl-followup", "object": "chat.completion.chunk", "created": 0,
            "model": "gpt-5.6", "choices": [{"index": 0, "delta": {"role": "assistant",
            "content": text, "tool_calls": [{"index": 0, "id": f"call-{call_serial}-{name}",
            "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]},
            "finish_reason": "tool_calls"}]}
        return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()

    wire = _install_wire(monkeypatch, agent, [tool_sse("check_door", {}, preamble),
        *[tool_sse("check_door", {}) for _ in range(tool_rounds - 1)],
        tool_sse("set_continue_conversation", {"continue_conversation": True, "response": question}, question if final_text else "")])
    events = await _run_assist(await hass_ws_client(hass), pipeline_id=agent.entity_id,
                               conversation_id="conditional-followup")
    spoken = _progressive_text(events)
    assert question in spoken
    assert spoken.count(question) == 1
    assert _final_speech(events) == question
    assert len(wire.requests) == tool_rounds + 1


async def _speech_agent(hass: HomeAssistant, **options) -> Any:
    """Create a real conversation entity with progressive speech cleanup enabled."""
    tool = deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])
    entry = _make_entry(
        "Assist Streaming Speech",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: [tool],
            CONF_SPEECH_PROCESSING_ENABLED: True,
            CONF_SPEECH_STRIP_MARKDOWN: True,
            CONF_SPEECH_STRIP_URLS: True,
            CONF_SPEECH_REGEX_REPLACEMENTS: [],
            **options,
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    assert agent.entity_id.startswith("conversation.")
    assert agent._attr_supports_streaming == (not options.get(CONF_SPEECH_REGEX_REPLACEMENTS))  # noqa: SLF001
    return agent


async def test_assist_progressive_speech_preserves_split_literal_identifiers(
    hass, hass_ws_client, monkeypatch
):
    """Real Assist listeners retain literals split inside Markdown/URL prefixes."""
    agent = await _speech_agent(hass)
    assert await async_setup_component(hass, "assist_pipeline", {})
    parts = ["sensor.kitchen", "_", "_", "temperature mailbox", "ht", "tps://example.com"]
    expected = "sensor.kitchen__temperature mailboxhttps://example.com"
    wire = _install_wire(monkeypatch, agent, [_chat_sse_deltas(parts)])
    events = await _run_assist(
        await hass_ws_client(hass),
        pipeline_id=agent.entity_id,
        conversation_id="literal-speech-boundaries",
    )
    assert _progressive_text(events) == expected
    assert _final_speech(events) == expected
    assert len(wire.requests) == 1


async def test_long_assist_answer_keeps_ha_progressing_during_final_cleanup(
    hass, hass_ws_client, monkeypatch
):
    """Actual completed cleanup runs outside the loop; HA can act before it ends."""
    from custom_components.extended_openai_conversation_responses import (
        regex_execution,
        speech,
    )

    agent = await _speech_agent(hass)
    assert await async_setup_component(hass, "assist_pipeline", {})
    raw = "**hello** world sensor.kitchen__temperature mailboxhttps://example.com " * 512
    expected = "hello world sensor.kitchen__temperature mailboxhttps://example.com " * 512
    parts = [raw[index : index + 256] for index in range(0, len(raw), 256)]
    wire = _install_wire(monkeypatch, agent, [_chat_sse_deltas(parts)])
    started, release, finished = (threading.Event() for _ in range(3))
    loop_thread = threading.get_ident()
    cleanup = speech._built_in_cleanup

    def observed_cleanup(text, **kwargs):
        assert threading.get_ident() != loop_thread, "completed cleanup blocked HA"
        assert text == raw
        started.set()
        # A deterministic checkpoint in the real cleanup path. No result is
        # mocked: HA must independently advance before the sanitizer can finish.
        assert release.wait(5), "HA did not progress during completed cleanup"
        result = cleanup(text, **kwargs)
        finished.set()
        return result

    monkeypatch.setattr(regex_execution, "_built_in_cleanup", observed_cleanup)
    monkeypatch.setattr(speech, "_built_in_cleanup", observed_cleanup)

    async def advance(call):
        hass.states.async_set("sensor.speech_cleanup_witness", "advanced")

    hass.services.async_register("test", "speech_cleanup_progress", advance)
    client = await hass_ws_client(hass)
    run = asyncio.create_task(
        _run_assist(
            client, pipeline_id=agent.entity_id, conversation_id="long-cleanup"
        )
    )
    try:
        async with asyncio.timeout(5):
            while not started.is_set():
                if run.done():
                    await run
                    raise AssertionError("completed cleanup never started")
                await asyncio.sleep(0.001)
        assert not finished.is_set()
        await hass.services.async_call("test", "speech_cleanup_progress", {}, blocking=True)
        assert hass.states.get("sensor.speech_cleanup_witness").state == "advanced"
        assert not finished.is_set()
        release.set()
        events = await run
        assert finished.is_set()
        assert _progressive_text(events).strip() == expected.strip()
        assert _final_speech(events) == expected.strip()
        assert len(wire.requests) == 1
    finally:
        release.set()
        if not run.done():
            run.cancel()
        await asyncio.gather(run, return_exceptions=True)


async def _run_assist(
    client: Any,
    *,
    pipeline_id: str,
    conversation_id: str,
    text: str = "Give me the streaming speech test response",
    end_stage: str = "intent",
) -> list[dict[str, Any]]:
    """Run the public HA Assist websocket pipeline and collect its real events."""
    await client.send_json_auto_id(
        {
            "type": "assist_pipeline/run",
            "start_stage": "intent",
            "end_stage": end_stage,
            "pipeline": pipeline_id,
            "input": {"text": text},
            "conversation_id": conversation_id,
            "device_id": "assist-streaming-speech-device",
        }
    )

    result = await client.receive_json()
    assert result["success"] is True

    events: list[dict[str, Any]] = []
    async with asyncio.timeout(5):
        while True:
            message = await client.receive_json()
            event = message["event"]
            events.append(event)
            if event["type"] == "run-end":
                return events


def _progressive_text(events: list[dict[str, Any]]) -> str:
    """Concatenate exactly what HA Assist exposed as progressive assistant speech."""
    return "".join(
        content
        for event in events
        if event["type"] == "intent-progress"
        and isinstance(event.get("data"), dict)
        and isinstance(event["data"].get("chat_log_delta"), dict)
        and isinstance(
            content := event["data"]["chat_log_delta"].get("content"), str
        )
    )


def _final_speech(events: list[dict[str, Any]]) -> str:
    """Return the completed IntentResponse speech emitted by the Assist pipeline."""
    intent_end = next(event for event in events if event["type"] == "intent-end")
    return intent_end["data"]["intent_output"]["response"]["speech"]["plain"][
        "speech"
    ]


async def test_actual_assist_stream_sanitizes_split_provider_deltas(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: Any,
) -> None:
    """Actual HA Assist must receive speech-safe deltas while OpenAI is streaming."""
    agent = await _speech_agent(hass)
    assert await async_setup_component(hass, "assist_pipeline", {})

    # Both unsafe constructs cross provider-delta boundaries. A completed-response
    # cleanup test cannot prove this: HA Assist sees these deltas progressively while
    # the provider stream is still open.
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_deltas(
                [
                    "**Hello** see ht",
                    "tps://example.com/path and [docs](https://exa",
                    "mple.org/help) done.",
                ]
            )
        ],
    )

    client = await hass_ws_client(hass)
    events = await _run_assist(
        client,
        pipeline_id=agent.entity_id,
        conversation_id="assist-streaming-speech",
    )

    event_types = [event["type"] for event in events]
    assert event_types[0] == "run-start"
    assert "intent-start" in event_types
    assert "intent-progress" in event_types
    assert "intent-end" in event_types
    assert event_types[-1] == "run-end"

    progressive = _progressive_text(events)
    assert progressive == _SPOKEN_RESPONSE
    assert _final_speech(events) == _SPOKEN_RESPONSE

    # Nothing unsafe may leak into any progressive Assist content event, including
    # partial URL prefixes that existed only while adjacent provider chunks arrived.
    progress_deltas = [
        event["data"]["chat_log_delta"]
        for event in events
        if event["type"] == "intent-progress"
        and isinstance(event.get("data"), dict)
        and isinstance(event["data"].get("chat_log_delta"), dict)
    ]
    assert any(delta.get("role") == "assistant" for delta in progress_deltas)
    assert len([delta for delta in progress_deltas if delta.get("content")]) >= 2
    for delta in progress_deltas:
        content = delta.get("content")
        if not isinstance(content, str):
            continue
        assert "http" not in content
        assert "example.com" not in content
        assert "example.org" not in content
        assert "[docs]" not in content
        assert "**" not in content

    assert len(wire.requests) == 1
    assert wire.requests[0]["path"] == "/v1/chat/completions"
    assert wire.requests[0]["body"]["stream"] is True

    # The expected final text is deliberately derived from a raw response containing
    # both Markdown and URLs, so this cannot accidentally pass with plain provider
    # output that needed no transformation.
    assert _RAW_RESPONSE != _SPOKEN_RESPONSE
