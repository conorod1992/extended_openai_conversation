"""Large model results prepare off-loop once, including AI Task and error history."""

import asyncio
import json
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import AsyncMock

from openai.types.chat import ChatCompletionChunk
import pytest

from custom_components.extended_openai_conversation_responses import (
    context as history_context,
    context_summary,
    model_tool_results as results,
)
from custom_components.extended_openai_conversation_responses.ai_task import (
    ExtendedOpenAITaskEntity,
)
from custom_components.extended_openai_conversation_responses.entity import (
    _convert_content_to_param,
    _convert_content_to_responses_param,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    backend_failure,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    caller_api_tools,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    HAToolResultError,
    ToolResultPayload,
    make_tool_result_content,
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    append_unresolved_tool_results,
)
from homeassistant.components import ai_task, conversation
from homeassistant.core import Context
from homeassistant.helpers import llm

from .test_conversation_provider_loop_contracts import _final_stream
from .test_ha_llm_tools import Echo, TestAPI
from .test_responses_api import FakeStream, _control_entity, _control_stream


def large_value(failed=False):
    value = [{"record": {"values": [index]}} for index in range(100000)]
    if failed:
        value.insert(0, backend_failure("ordinary error"))
    for _ in range(20):
        value = {"nested": value}
    return value


@pytest.mark.parametrize("failed", [False, True])
async def test_large_nested_projection_yields_and_history_reuses_prepared_output(
    hass, monkeypatch, failed
):
    loop = asyncio.get_running_loop()
    loop_thread = get_ident()
    entered = asyncio.Event()
    release = Event()
    original_scan = results._tool_result_failed
    scans = 0

    def scan(*args):
        nonlocal scans
        assert get_ident() != loop_thread, "failure scan ran on the event loop"
        scans += 1
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5), "event loop could not release the worker"
        return original_scan(*args)

    monkeypatch.setattr(results, "_tool_result_failed", scan)
    value = large_value(failed)
    source = make_tool_result_content(
        agent_id="probe",
        tool_call_id="large",
        tool_name="report",
        tool_result={"result": value},
    )
    log = conversation.ChatLog(hass, "large-retained-result")
    index = len(log.content)
    log.content.append(source)
    history = log.content
    pending = asyncio.create_task(results.async_prepare_tool_results(history))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # The scan remains blocked in its worker while the event loop makes progress.
        for _ in range(3):
            await asyncio.sleep(0)
        assert not pending.done()
    finally:
        release.set()
        await pending
    assert scans == 1
    prepared = history[index]
    assert prepared is not source
    assert tool_result_data(source)["result"] is value
    assert tool_result_data(log.content[index])["result"] is value
    assert isinstance(tool_result_data(prepared), ToolResultPayload)

    def unexpected(*args, **kwargs):
        raise AssertionError("prepared history repeated payload-sized work")

    monkeypatch.setattr(results, "_prepare_tool_result", unexpected)
    monkeypatch.setattr(conversation.ToolResultContent, "as_dict", unexpected)
    for _ in range(3):
        await results.async_prepare_tool_results(history)
        assert history[index] is prepared
        assert tool_result_data(log.content[index])["result"] is value
        assert history_context._weight([prepared]) < 1000
        transcript = history_context.history_as_summary_text([prepared])
        assert len(transcript) < 1000
        assert "output_overflow" in transcript
        signature = context_summary._history_signature([prepared])
        assert signature == context_summary._history_signature([prepared])
        for wire in (
            _convert_content_to_param([prepared])[0]["content"],
            _convert_content_to_responses_param([prepared])[0]["output"],
        ):
            output = json.loads(wire)["result"]
            assert output["status"] == "output_overflow"
            assert output.get("error", False) is failed
    assert scans == 1


def final_stream(api_mode):
    if api_mode == "responses":
        return _final_stream("Done")
    return FakeStream(
        [
            ChatCompletionChunk(
                id="done",
                object="chat.completion.chunk",
                created=0,
                model="gpt-4o-mini",
                choices=[
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "delta": {"role": "assistant", "content": "Done"},
                    }
                ],
            )
        ]
    )


@pytest.mark.parametrize("kind", ["literal", "structured", "owned", "metadata"])
async def test_prepared_log_retains_full_values_and_metadata(hass, monkeypatch, kind):
    value = "x" * 64000
    payload = {"result": value}
    if kind in {"structured", "owned"}:
        value = {"error": value, "status": "business data"}
        payload = (
            results.OwnedJSONPayload(value) if kind == "owned" else {"result": value}
        )
    elif kind == "metadata":
        payload = {"result": "small", "metadata": {"records": [value]}}
    source = make_tool_result_content(
        agent_id="probe", tool_call_id=kind, tool_name="report", tool_result=payload
    )
    log = conversation.ChatLog(hass, f"retain-{kind}")
    index = len(log.content)
    log.content.append(source)
    await results.async_prepare_tool_results(log.content)
    retained = tool_result_data(log.content[index])
    assert retained == payload
    for key in payload:
        assert retained[key] is payload[key]

    def unexpected(*args, **kwargs):
        raise AssertionError("prepared history repeated payload-sized work")

    monkeypatch.setattr(results, "_prepare_tool_result", unexpected)
    for _ in range(2):
        await results.async_prepare_tool_results(log.content)
        content = log.content[index]
        assert tool_result_data(content) is retained
        for wire in (
            _convert_content_to_param([content])[0]["content"],
            _convert_content_to_responses_param([content])[0]["output"],
        ):
            output = json.loads(wire)
            assert len(wire) < 33000
            result = output["result"]
            if kind == "literal":
                assert isinstance(result, str)
                assert len(result) <= 32000
            else:
                if kind == "owned":
                    result = json.loads(result)
                assert result["status"] == "output_overflow"
                assert "error" not in result


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
async def test_ai_task_prepares_retained_failures_and_new_ha_results_before_every_conversion(
    hass, monkeypatch, api_mode
):
    loop_thread = get_ident()
    original_prepare = results._prepare_tool_result
    prepared_calls = []

    def prepare(content):
        assert get_ident() != loop_thread, (
            "provider conversion bypassed async preparation"
        )
        prepared_calls.append(content.tool_call_id)
        return original_prepare(content)

    monkeypatch.setattr(results, "_prepare_tool_result", prepare)
    value = large_value()

    class Report(Echo):
        async def async_call(self, hass, tool_input, llm_context):
            self.calls.append(tool_input)
            result_type = getattr(llm, "ToolResult", None)
            return result_type(data=value) if result_type is not None else value

    tool = Report()
    context = llm.LLMContext(
        platform="ai_task",
        context=Context(user_id="caller"),
        language=None,
        assistant="ai_task",
        device_id=None,
    )
    instance = await TestAPI(hass, tools=[tool]).async_get_api_instance(context)
    _, tools = caller_api_tools(instance)
    base, log, create = _control_entity(
        hass,
        api_mode,
        [
            _control_stream(
                api_mode, tools[0]["spec"]["name"], {"value": "report"}, "fresh"
            ),
            final_stream(api_mode),
            final_stream(api_mode),
        ],
    )
    entity = object.__new__(ExtendedOpenAITaskEntity)
    entity.__dict__.update(base.__dict__)
    entity.entity_id = "ai_task.large"
    entity.entry.entry_id = "entry"
    entity.subentry.subentry_id = "agent"
    hass.auth.async_get_user.return_value = SimpleNamespace(is_active=True)
    log.llm_api = instance
    log.async_provide_llm_data = AsyncMock()
    old_call = llm.ToolInput(id="retained", tool_name="previous", tool_args={})
    log.content.append(
        conversation.AssistantContent(agent_id=entity.entity_id, tool_calls=[old_call])
    )
    append_unresolved_tool_results(
        log, entity.entity_id, [old_call], error=HAToolResultError(value)
    )
    original_failure = log.content[-1]
    log.async_add_user_content(conversation.UserContent(content="Continue"))
    task = ai_task.GenDataTask(name="Report", instructions="Get a report")
    assert (await entity._async_generate_data(task, log)).data == "Done"
    log.async_add_user_content(conversation.UserContent(content="Again"))
    assert (await entity._async_generate_data(task, log)).data == "Done"
    assert prepared_calls == ["retained", "fresh"]
    assert len(tool.calls) == 1
    assert tool_result_data(original_failure)["result"]["data"] is value
    retained_results = {
        item.tool_call_id: tool_result_data(item)
        for item in log.content
        if hasattr(item, "tool_call_id")
    }
    assert retained_results["retained"]["result"]["data"] is value
    assert retained_results["fresh"]["result"] is value
    requests = create.await_args_list
    assert len(requests) == 3
    for index, request in enumerate(requests):
        items = request.kwargs["input" if api_mode == "responses" else "messages"]
        outputs = {
            item["call_id" if api_mode == "responses" else "tool_call_id"]: json.loads(
                item["output" if api_mode == "responses" else "content"]
            )["result"]
            for item in items
            if item.get("type") == "function_call_output" or item.get("role") == "tool"
        }
        assert outputs["retained"]["error"] is True
        assert outputs["retained"]["status"] == "output_overflow"
        if index:
            assert outputs["fresh"]["status"] == "output_overflow"
            assert outputs["fresh"].get("error", False) is False
