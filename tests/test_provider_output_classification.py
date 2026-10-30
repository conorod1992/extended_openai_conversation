"""Speech, final presentation and native replay share provider output semantics."""

import asyncio
from types import SimpleNamespace

from openai.types.responses import ResponseFunctionWebSearch
import pytest

from custom_components.extended_openai_conversation_responses import assistant_response
from custom_components.extended_openai_conversation_responses.assistant_response import (
    classify_provider_output,
    final_assistant_text,
)
from custom_components.extended_openai_conversation_responses.completion_events import (
    completion_event_scope,
)
from custom_components.extended_openai_conversation_responses.entity import (
    _convert_content_to_responses_param,
)
from custom_components.extended_openai_conversation_responses.non_streaming import (
    completed_responses_events,
)
from custom_components.extended_openai_conversation_responses.speech import (
    async_streaming_speech_cleanup,
    process_speech_text,
)
from homeassistant.components import conversation
from tests.test_responses_history_coherence import entity, events, message, reasoning


@pytest.mark.parametrize("mapping", [False, True])
@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({}, "answer"),
        ({"type": "message"}, "answer"),
        ({"type": "message", "phase": "final_answer"}, "answer"),
        ({"type": "message", "phase": "future_phase"}, "answer"),
        ({"type": "message", "phase": "commentary"}, "commentary"),
        ({"type": "reasoning"}, "reasoning"),
        ({"type": "web_search_call"}, "hosted_tool"),
        ({"type": "function_call"}, "replay_only"),
        ({"type": "future_item"}, "replay_only"),
    ],
)
def test_classification_preserves_supported_and_legacy_metadata(
    mapping, metadata, expected
):
    value = metadata if mapping else SimpleNamespace(**metadata)
    assert classify_provider_output(value) == expected
    assert classify_provider_output(None) == "answer"


def speech_options(enabled):
    return {
        "speech_processing_enabled": enabled,
        "speech_strip_markdown": True,
        "speech_strip_urls": True,
        "speech_regex_replacements": [],
    }


async def output_witness(hass, *, cleanup, phase, completed=False):
    raw_answer = "**Final** answer."
    output = [
        message("progress", "Private progress."),
        reasoning("thinking"),
        ResponseFunctionWebSearch(
            id="search",
            type="web_search_call",
            status="completed",
            action={"type": "search", "query": "answer"},
        ),
        message("answer", raw_answer).model_copy(update={"phase": phase}),
    ]
    obj, _ = entity(hass, [])
    log = conversation.ChatLog(hass, "classification")
    log.async_add_user_content(conversation.UserContent(content="Question"))
    heard = []
    heard_answer = asyncio.Event()
    finish = asyncio.Event()

    def listener(_log, delta):
        heard.append(delta)
        if "answer." in delta.get("content", ""):
            heard_answer.set()

    log.delta_listener = listener

    async def provider_events():
        if completed:
            async for event in completed_responses_events(
                SimpleNamespace(output=output, status="completed", usage=None)
            ):
                yield event
        else:
            for event in events(output).events:
                yield event

    async def source():
        async for event in provider_events():
            yield event
            if event.type == "response.output_text.delta" and event.output_index == 3:
                await finish.wait()

    async def consume():
        with async_streaming_speech_cleanup(log, speech_options(cleanup)):
            async for _ in log.async_add_delta_content_stream(
                obj.entity_id, obj._transform_responses_stream(log, source())
            ):
                pass

    task = asyncio.create_task(consume())
    waiter = asyncio.create_task(heard_answer.wait())
    try:
        done, _ = await asyncio.wait(
            [task, waiter], timeout=5, return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            await task
        assert waiter in done, "answer speech was held until completion"
        assert not task.done(), "answer speech must precede native/response completion"
        assert not any(
            "Private progress" in delta.get("content", "") for delta in heard
        )
        finish.set()
        await task
    finally:
        finish.set()
        if not task.done():
            task.cancel()
        waiter.cancel()
        await asyncio.gather(task, waiter, return_exceptions=True)

    assert log.delta_listener is listener
    expected_speech = process_speech_text(raw_answer, speech_options(cleanup))
    assert "".join(delta.get("content", "") for delta in heard) == expected_speech
    assert final_assistant_text(log.content) == raw_answer
    assert (
        process_speech_text(final_assistant_text(log.content), speech_options(cleanup))
        == expected_speech
    )
    # Presentation never strips retained progress or modifies its provider IDs.
    assert any(content.content == "Private progress." for content in log.content)
    replay = _convert_content_to_responses_param(log.content)
    assert [item.get("id") for item in replay if item.get("id")] == [
        item.id for item in output
    ]
    assert replay[1] == output[0].model_dump(exclude_none=True)


@pytest.mark.parametrize("cleanup", [False, True])
@pytest.mark.parametrize("phase", [None, "final_answer"])
@pytest.mark.parametrize("completed", [False, True], ids=["streamed", "completed"])
async def test_listener_answer_and_native_replay_are_independent(
    hass, cleanup, phase, completed
):
    await output_witness(hass, cleanup=cleanup, phase=phase, completed=completed)


async def test_classifier_sensitivity_detects_spoken_commentary(hass, monkeypatch):
    original = assistant_response.classify_provider_output

    def mutant(item):
        result = original(item)
        return "answer" if result == "commentary" else result

    monkeypatch.setattr(assistant_response, "classify_provider_output", mutant)
    with pytest.raises(AssertionError):
        await output_witness(hass, cleanup=False, phase="final_answer")


@pytest.mark.parametrize("cancel", [False, True])
async def test_listener_restored_after_generator_close_and_cancel(hass, cancel):
    obj, _ = entity(hass, [])
    log = conversation.ChatLog(hass, f"listener-{cancel}")

    def listener(*_):
        pass

    log.delta_listener = listener
    entered = asyncio.Event()

    async def source():
        yield events([message("progress", "Progress")]).events[0]
        entered.set()
        await asyncio.Event().wait()

    stream = obj._transform_responses_stream(log, source())
    assert await anext(stream) == {"role": "assistant"}
    assert log.delta_listener is not listener
    if cancel:
        pending = asyncio.create_task(anext(stream))
        await entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    else:
        await stream.aclose()
    assert log.delta_listener is listener


async def test_consumer_failure_does_not_resurrect_a_disposed_speech_wrapper(hass):
    obj, _ = entity(hass, [])
    log = conversation.ChatLog(hass, "consumer-failure")

    def listener(_log, delta):
        if "role" in delta:
            raise RuntimeError("listener failed")

    log.delta_listener = listener
    stream = obj._transform_responses_stream(
        log, events([message("progress", "Progress")])
    )
    with (
        pytest.raises(RuntimeError, match="listener failed"),
        async_streaming_speech_cleanup(log, speech_options(True)),
    ):
        async for _ in log.async_add_delta_content_stream(obj.entity_id, stream):
            pass
    assert log.delta_listener is listener
    # Production closes provider generators after the consumer/speech scope has
    # unwound. A late close may retire its own wrapper, never restore that scope.
    await stream.aclose()
    assert log.delta_listener is listener


async def test_conditional_fallback_does_not_speak_retained_commentary(hass):
    first = message("draft", "Superseded draft").model_copy(update={"phase": None})
    final = message("final", "Fallback answer").model_copy(
        update={"phase": "final_answer"}
    )
    output = [message("progress", "Progress only"), final]
    obj, client = entity(hass, [events([first]), events(output)])
    log = conversation.ChatLog(hass, "conditional-classification")
    log.async_add_user_content(conversation.UserContent(content="Question"))
    heard = []
    log.delta_listener = lambda _log, delta: heard.append(delta)
    async with completion_event_scope():
        assert (
            await obj._async_handle_chat_log(log, [], [], conditional_continue=True)
            is None
        )
    assert client.responses.create.await_count == 2
    assert "".join(delta.get("content", "") for delta in heard) == "Fallback answer"
    assert final_assistant_text(log.content) == "Fallback answer"
    assert [
        item["id"]
        for item in _convert_content_to_responses_param(log.content)
        if "id" in item
    ] == ["draft", "progress", "final"]


async def test_role_boundary_flushes_previous_answer_without_spoken_progress(hass):
    # A trailing Markdown prefix is withheld by sanitation until the next role.
    output = [
        message("first", "Answer *").model_copy(update={"phase": "final_answer"}),
        message("progress", "Progress only"),
        message("last", "Last.").model_copy(update={"phase": "final_answer"}),
    ]
    obj, _ = entity(hass, [])
    log = conversation.ChatLog(hass, "flush-classification")
    heard = []
    log.delta_listener = lambda _log, delta: heard.append(delta)
    with async_streaming_speech_cleanup(log, speech_options(True)):
        async for _ in log.async_add_delta_content_stream(
            obj.entity_id, obj._transform_responses_stream(log, events(output))
        ):
            pass
    assert "".join(delta.get("content", "") for delta in heard) == "Answer *Last."
